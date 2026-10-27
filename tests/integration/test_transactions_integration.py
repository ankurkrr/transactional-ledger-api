"""Integration tests for Transaction endpoints executed against real PostgreSQL.

Validates end-to-end credit and debit execution, balance reconciliation,
row locking concurrency control, idempotency guarantees, and immutability.
"""

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from ledger.core.config import get_settings


def _get_test_db_url() -> str:
    """Returns the isolated test database URL from settings or derived from DATABASE_URL."""
    settings = get_settings()
    if settings.TEST_DATABASE_URL:
        return settings.TEST_DATABASE_URL
    if settings.DATABASE_URL.endswith("/ledger"):
        return settings.DATABASE_URL[:-7] + "/ledger_test"
    return settings.DATABASE_URL


def _probe_postgres_reachable() -> bool:
    """Synchronously probes if PostgreSQL is reachable with application credentials."""

    async def _probe() -> bool:
        try:
            test_url = _get_test_db_url()
            engine = create_async_engine(test_url)
            async with engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
            await engine.dispose()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


POSTGRES_AVAILABLE = _probe_postgres_reachable()

pytestmark = pytest.mark.skipif(
    not POSTGRES_AVAILABLE,
    reason="PostgreSQL is not reachable on configured port with application credentials.",
)


@pytest.mark.asyncio
async def test_integration_credit_and_debit_flow(
    async_client: AsyncClient,
) -> None:
    """Validates credit increases balance, debit decreases balance, and checks DB state."""
    # 1. Create account
    create_res = await async_client.post(
        "/api/v1/accounts",
        json={"name": f"Tx Test {uuid.uuid4().hex[:6]}"},
    )
    assert create_res.status_code == 201
    account_id = create_res.json()["id"]

    # 2. Credit 5,000 paise (₹50.00)
    c1_res = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "50.00",
            "description": "First credit",
            "reference_id": f"REF-C1-{uuid.uuid4().hex[:6]}",
        },
    )
    assert c1_res.status_code == 201
    assert c1_res.json()["amount"] == "50.00"
    assert c1_res.json()["balance_after"] == "50.00"
    assert c1_res.json()["currency"] == "INR"

    # 3. Credit another 3,000 paise (₹30.00)
    c2_res = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "30.00",
            "description": "Second credit",
            "reference_id": f"REF-C2-{uuid.uuid4().hex[:6]}",
        },
    )
    assert c2_res.status_code == 201
    assert c2_res.json()["amount"] == "30.00"
    assert c2_res.json()["balance_after"] == "80.00"
    assert c2_res.json()["currency"] == "INR"

    # 4. Debit 2,500 paise (₹25.00)
    d1_res = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "debit",
            "amount": "25.00",
            "description": "First debit",
            "reference_id": f"REF-D1-{uuid.uuid4().hex[:6]}",
        },
    )
    assert d1_res.status_code == 201
    assert d1_res.json()["amount"] == "25.00"
    assert d1_res.json()["balance_after"] == "55.00"
    assert d1_res.json()["currency"] == "INR"

    # 5. Verify current balance via GET /balance
    bal_res = await async_client.get(f"/api/v1/accounts/{account_id}/balance")
    assert bal_res.status_code == 200
    assert bal_res.json()["balance"] == "55.00"
    assert bal_res.json()["currency"] == "INR"

    # 6. Verify directly in PostgreSQL
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            # Check account balance
            row = (
                await conn.execute(
                    sa.text("SELECT balance_minor FROM accounts WHERE id = :id"),
                    {"id": uuid.UUID(account_id)},
                )
            ).fetchone()
            assert row is not None
            assert row[0] == 5500

            # Check transaction count
            tx_count = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM transactions WHERE account_id = :id"),
                    {"id": uuid.UUID(account_id)},
                )
            ).scalar()
            assert tx_count == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_debit_insufficient_funds_leaves_balance_untouched(
    async_client: AsyncClient,
) -> None:
    """Debit exceeding available balance is rejected with 422; DB balance and tx log untouched."""
    # 1. Create account and credit 1,000 paise
    create_res = await async_client.post(
        "/api/v1/accounts",
        json={"name": "Low Balance"},
    )
    account_id = create_res.json()["id"]

    await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "10.00",
            "description": "Initial credit",
            "reference_id": f"REF-INIT-{uuid.uuid4().hex[:6]}",
        },
    )

    # 2. Attempt debit of 2,000 paise (exceeds 1,000)
    overdraft_ref = f"REF-OVERDRAFT-{uuid.uuid4().hex[:6]}"
    debit_res = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "debit",
            "amount": "20.00",
            "description": "Attempted overdraft",
            "reference_id": overdraft_ref,
        },
    )
    assert debit_res.status_code == 422
    assert debit_res.json()["error"]["code"] == "INSUFFICIENT_FUNDS"

    # 3. Direct DB check: balance remains 1000 and rejected tx not recorded
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            bal = (
                await conn.execute(
                    sa.text("SELECT balance_minor FROM accounts WHERE id = :id"),
                    {"id": uuid.UUID(account_id)},
                )
            ).scalar()
            assert bal == 1000

            tx_exists = (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM transactions WHERE account_id = :id AND reference_id = :ref"
                    ),
                    {"id": uuid.UUID(account_id), "ref": overdraft_ref},
                )
            ).scalar()
            assert tx_exists == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_duplicate_reference_and_cross_account_reuse(
    async_client: AsyncClient,
) -> None:
    """Same reference on same account returns 409; same reference on different account succeeds."""
    # Create two distinct accounts
    acc1 = (await async_client.post("/api/v1/accounts", json={"name": "Account 1"})).json()["id"]
    acc2 = (await async_client.post("/api/v1/accounts", json={"name": "Account 2"})).json()["id"]

    shared_reference = f"REF-INVOICE-{uuid.uuid4().hex[:6]}"

    # Post to Account 1 -> 201 Created
    res1 = await async_client.post(
        f"/api/v1/accounts/{acc1}/transactions",
        json={
            "type": "credit",
            "amount": "50.00",
            "description": "First invoice payment",
            "reference_id": shared_reference,
        },
    )
    assert res1.status_code == 201

    # Post SAME reference to Account 2 -> 201 Created (Allowed across accounts)
    res2 = await async_client.post(
        f"/api/v1/accounts/{acc2}/transactions",
        json={
            "type": "credit",
            "amount": "50.00",
            "description": "Same invoice reference on different account",
            "reference_id": shared_reference,
        },
    )
    assert res2.status_code == 201

    # Post SAME reference AGAIN to Account 1 -> 409 Conflict
    res3 = await async_client.post(
        f"/api/v1/accounts/{acc1}/transactions",
        json={
            "type": "credit",
            "amount": "50.00",
            "description": "Repeated invoice payment",
            "reference_id": shared_reference,
        },
    )
    assert res3.status_code == 409
    assert res3.json()["error"]["code"] == "DUPLICATE_REFERENCE"

    # Verify Account 1 balance was NOT doubled
    bal1 = (await async_client.get(f"/api/v1/accounts/{acc1}/balance")).json()["balance"]
    assert bal1 == "50.00"


@pytest.mark.asyncio
async def test_integration_concurrent_debits_cannot_overdraw(
    async_client: AsyncClient,
) -> None:
    """Row lock ensures concurrent debits cannot exceed available balance or overdraw."""
    # Create account and credit 10,000 paise (₹100)
    acc_id = (
        await async_client.post("/api/v1/accounts", json={"name": "Concurrency Debit Account"})
    ).json()["id"]

    await async_client.post(
        f"/api/v1/accounts/{acc_id}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "Initial balance",
            "reference_id": f"REF-CONCUR-INIT-{uuid.uuid4().hex[:6]}",
        },
    )

    # Concurrently submit 4 debits of 4,000 paise each (Total = 16,000 > 10,000)
    async def _send_debit(idx: int):
        return await async_client.post(
            f"/api/v1/accounts/{acc_id}/transactions",
            json={
                "type": "debit",
                "amount": "40.00",
                "description": f"Concurrent debit {idx}",
                "reference_id": f"REF-DEBIT-{idx}-{uuid.uuid4().hex[:6]}",
            },
        )

    responses = await asyncio.gather(*[_send_debit(i) for i in range(4)])

    status_codes = [r.status_code for r in responses]
    success_count = status_codes.count(201)
    insufficient_count = status_codes.count(422)

    # Exactly 2 debits can succeed (4,000 * 2 = 8,000 <= 10,000). Remaining 2 must fail.
    assert success_count == 2
    assert insufficient_count == 2

    # Verify final balance is exactly 2,000 paise
    final_bal = (await async_client.get(f"/api/v1/accounts/{acc_id}/balance")).json()["balance"]
    assert final_bal == "20.00"


@pytest.mark.asyncio
async def test_integration_concurrent_duplicate_references(
    async_client: AsyncClient,
) -> None:
    """Concurrent requests with identical reference_id cannot double-apply."""
    acc_id = (
        await async_client.post(
            "/api/v1/accounts", json={"name": "Concurrency Idempotency Account"}
        )
    ).json()["id"]

    same_reference = f"REF-IDEMPOTENT-{uuid.uuid4().hex[:8]}"

    # Concurrently submit 5 requests with the exact same reference_id
    async def _send_tx():
        return await async_client.post(
            f"/api/v1/accounts/{acc_id}/transactions",
            json={
                "type": "credit",
                "amount": "10.00",
                "description": "Concurrent same reference",
                "reference_id": same_reference,
            },
        )

    responses = await asyncio.gather(*[_send_tx() for _ in range(5)])

    status_codes = [r.status_code for r in responses]
    success_count = status_codes.count(201)
    conflict_count = status_codes.count(409)

    # Exactly 1 must succeed; remaining 4 must return 409 Conflict
    assert success_count == 1
    assert conflict_count == 4

    # Final balance must be credited only once (1,000 paise, NOT 5,000)
    final_bal = (await async_client.get(f"/api/v1/accounts/{acc_id}/balance")).json()["balance"]
    assert final_bal == "10.00"


@pytest.mark.asyncio
async def test_integration_balance_mathematical_reconciliation(
    async_client: AsyncClient,
) -> None:
    """Verifies balance strictly reconciles with sum of accepted transactions."""
    acc_id = (
        await async_client.post("/api/v1/accounts", json={"name": "Reconciliation Account"})
    ).json()["id"]

    # Execute alternating credits and debits
    ops = [
        ("credit", "100.00"),
        ("debit", "30.00"),
        ("credit", "50.00"),
        ("debit", "20.00"),
        ("debit", "40.00"),
        ("credit", "70.00"),
    ]

    for idx, (tx_type, amount) in enumerate(ops):
        res = await async_client.post(
            f"/api/v1/accounts/{acc_id}/transactions",
            json={
                "type": tx_type,
                "amount": amount,
                "description": f"Op {idx}",
                "reference_id": f"REF-RECON-{idx}-{uuid.uuid4().hex[:6]}",
            },
        )
        assert res.status_code == 201

    # Query balance
    api_bal = (await async_client.get(f"/api/v1/accounts/{acc_id}/balance")).json()["balance"]
    assert api_bal == "130.00"

    # Query DB mathematical sum: credits minus debits
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            calculated_sum = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT COALESCE(SUM(
                            CASE WHEN type = 'credit' THEN amount_minor ELSE -amount_minor END
                        ), 0)
                        FROM transactions
                        WHERE account_id = :id
                        """
                    ),
                    {"id": uuid.UUID(acc_id)},
                )
            ).scalar()
            assert calculated_sum == (10000 - 3000 + 5000 - 2000 - 4000 + 7000)  # 13000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_database_role_immutability_enforced() -> None:
    """Runtime DB user (ledger_app) has UPDATE and DELETE on transactions revoked by PostgreSQL."""
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    test_uuid = uuid.uuid4()

    try:
        # 1. Direct UPDATE must be denied
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as exc_update:
                await conn.execute(
                    sa.text("UPDATE transactions SET amount_minor = 9999 WHERE id = :id"),
                    {"id": test_uuid},
                )
            assert "permission denied for table transactions" in str(exc_update.value).lower()

        # 2. Direct DELETE must be denied
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as exc_delete:
                await conn.execute(
                    sa.text("DELETE FROM transactions WHERE id = :id"),
                    {"id": test_uuid},
                )
            assert "permission denied for table transactions" in str(exc_delete.value).lower()

        # 3. Direct DDL (CREATE TABLE on public schema) must be denied (Fix V-03)
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as exc_create:
                await conn.execute(
                    sa.text("CREATE TABLE public.unauthorized_audit_table (id serial primary key)")
                )
            assert "permission denied for schema public" in str(exc_create.value).lower()

        # 4. Legitimate operations (SELECT and INSERT on accounts) must succeed
        async with engine.connect() as conn:
            res = await conn.execute(sa.text("SELECT COUNT(*) FROM accounts"))
            assert res.scalar() is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_amount_boundary_and_type_validation(
    async_client: AsyncClient,
) -> None:
    """Verifies that 0, negative, float, booleans, malformed, and > BIGINT max are rejected and leave DB clean."""
    res_acc = await async_client.post("/api/v1/accounts", json={"name": "Boundary Test Account"})
    assert res_acc.status_code == 201
    account_id = res_acc.json()["id"]

    invalid_amounts: list[object] = [
        0,
        -100,
        -5000,
        50.25,
        100.0,
        True,
        False,
        "0",
        "0.00",
        "-1.00",
        "-50.00",
        "1.001",
        "1.234",
        "abc",
        "",
        "   ",
        "10.5.5",
        "92233720368547758.08",
        "10000000000000000000",
    ]

    for val in invalid_amounts:
        res = await async_client.post(
            f"/api/v1/accounts/{account_id}/transactions",
            json={
                "type": "credit",
                "amount": val,
                "description": f"Attempt with {val}",
                "reference_id": f"REF-INV-{uuid.uuid4()}",
            },
        )
        assert res.status_code == 422, f"Expected 422 for amount={val}, got {res.status_code}"
        assert res.json()["error"]["code"] in ("VALIDATION_ERROR", "INVALID_AMOUNT")

    # Verify that account balance remains 0 and transaction log is empty
    bal_res = await async_client.get(f"/api/v1/accounts/{account_id}/balance")
    assert bal_res.status_code == 200
    assert bal_res.json()["balance"] == "0.00"

    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM transactions WHERE account_id = :id"),
                    {"id": uuid.UUID(account_id)},
                )
            ).scalar()
            assert count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_max_bigint_and_overflow_prevention(
    async_client: AsyncClient,
) -> None:
    """Verifies credit of max BIGINT succeeds and subsequent overflow is rejected with state preserved."""
    res_acc = await async_client.post("/api/v1/accounts", json={"name": "Max Bigint Account"})
    assert res_acc.status_code == 201
    account_id = res_acc.json()["id"]

    max_bigint_rupees = "92233720368547758.07"

    # 1. Credit exact maximum BIGINT
    res_credit = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": max_bigint_rupees,
            "description": "Deposit max signed 64-bit bigint",
            "reference_id": "REF-MAX-CREDIT-1",
        },
    )
    assert res_credit.status_code == 201
    assert res_credit.json()["amount"] == max_bigint_rupees
    assert res_credit.json()["balance_after"] == max_bigint_rupees

    # Verify balance query returns exact max_bigint
    bal_res = await async_client.get(f"/api/v1/accounts/{account_id}/balance")
    assert bal_res.status_code == 200
    assert bal_res.json()["balance"] == max_bigint_rupees

    # 2. Attempt to credit 1 more paise (would overflow signed BIGINT)
    res_overflow = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "0.01",
            "description": "Overflow trigger",
            "reference_id": "REF-OVERFLOW-FAIL",
        },
    )
    assert res_overflow.status_code == 422
    assert res_overflow.json()["error"]["code"] == "INVALID_AMOUNT"

    # 3. Verify balance is unchanged at max_bigint
    bal_after = await async_client.get(f"/api/v1/accounts/{account_id}/balance")
    assert bal_after.status_code == 200
    assert bal_after.json()["balance"] == max_bigint_rupees

    # 4. Verify transaction count is exactly 1 (the failed overflow did not leave a record)
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM transactions WHERE account_id = :id"),
                    {"id": uuid.UUID(account_id)},
                )
            ).scalar()
            assert count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_transaction_history_empty(
    async_client: AsyncClient,
) -> None:
    """1. Empty transaction history returns 200 with empty items and zero pagination totals."""
    res_acc = await async_client.post("/api/v1/accounts", json={"name": "Empty History Acc"})
    assert res_acc.status_code == 201
    account_id = res_acc.json()["id"]

    res_hist = await async_client.get(f"/api/v1/accounts/{account_id}/transactions")
    assert res_hist.status_code == 200
    data = res_hist.json()
    assert data["items"] == []
    assert data["pagination"] == {
        "current_page": 1,
        "page_size": 20,
        "total_records": 0,
        "total_pages": 0,
    }


@pytest.mark.asyncio
async def test_integration_transaction_history_credits_and_debits_and_filters(
    async_client: AsyncClient,
) -> None:
    """2 & 3 & 5 & 10. History with credits and debits, type filters, default pagination, and accurate totals."""
    res_acc = await async_client.post("/api/v1/accounts", json={"name": "History Filter Acc"})
    assert res_acc.status_code == 201
    account_id = res_acc.json()["id"]

    # Post 3 credits and 2 debits
    # Credit 1: 10,000 paise (₹100.00)
    await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "C1",
            "reference_id": f"REF-C1-{uuid.uuid4().hex[:6]}",
        },
    )
    # Credit 2: 5,000 paise (₹50.00)
    await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "50.00",
            "description": "C2",
            "reference_id": f"REF-C2-{uuid.uuid4().hex[:6]}",
        },
    )
    # Debit 1: 3,000 paise (₹30.00)
    await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "debit",
            "amount": "30.00",
            "description": "D1",
            "reference_id": f"REF-D1-{uuid.uuid4().hex[:6]}",
        },
    )
    # Credit 3: 2,000 paise (₹20.00)
    await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "20.00",
            "description": "C3",
            "reference_id": f"REF-C3-{uuid.uuid4().hex[:6]}",
        },
    )
    # Debit 2: 4,000 paise (₹40.00)
    await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "debit",
            "amount": "40.00",
            "description": "D2",
            "reference_id": f"REF-D2-{uuid.uuid4().hex[:6]}",
        },
    )

    # 1. Default pagination without filter: returns all 5 records
    res_all = await async_client.get(f"/api/v1/accounts/{account_id}/transactions")
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert len(data_all["items"]) == 5
    assert data_all["pagination"]["current_page"] == 1
    assert data_all["pagination"]["page_size"] == 20
    assert data_all["pagination"]["total_records"] == 5
    assert data_all["pagination"]["total_pages"] == 1

    # Verify rupee fields and currency present on items
    assert all("amount" in item and "balance_after" in item for item in data_all["items"])
    assert all(item["currency"] == "INR" for item in data_all["items"])

    # Verify chronological newest-first order
    types_order = [item["type"] for item in data_all["items"]]
    assert types_order == ["debit", "credit", "debit", "credit", "credit"]

    # 2. Credit-only filter: returns only 3 credits
    res_credits = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?type=credit")
    assert res_credits.status_code == 200
    data_credits = res_credits.json()
    assert len(data_credits["items"]) == 3
    assert all(item["type"] == "credit" for item in data_credits["items"])
    assert data_credits["pagination"]["total_records"] == 3
    assert data_credits["pagination"]["total_pages"] == 1

    # 3. Debit-only filter: returns only 2 debits
    res_debits = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?type=debit")
    assert res_debits.status_code == 200
    data_debits = res_debits.json()
    assert len(data_debits["items"]) == 2
    assert all(item["type"] == "debit" for item in data_debits["items"])
    assert data_debits["pagination"]["total_records"] == 2
    assert data_debits["pagination"]["total_pages"] == 1


@pytest.mark.asyncio
async def test_integration_transaction_history_invalid_query_params(
    async_client: AsyncClient,
) -> None:
    """4 & 7. Reject invalid type filters, page values, and limit values exceeding 100."""
    res_acc = await async_client.post("/api/v1/accounts", json={"name": "Validation Query Acc"})
    account_id = res_acc.json()["id"]

    # Invalid type filters
    for bad_type in ["transfer", "CREDIT", "Debit", "refund", "123"]:
        res = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?type={bad_type}")
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    # Invalid page bounds (page < 1)
    for bad_page in [0, -1, -99]:
        res = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page={bad_page}")
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"

    # Invalid limit bounds (limit < 1 or limit > 100)
    for bad_limit in [0, -5, 101, 500]:
        res = await async_client.get(
            f"/api/v1/accounts/{account_id}/transactions?limit={bad_limit}"
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_integration_transaction_history_multi_page_traversal(
    async_client: AsyncClient,
) -> None:
    """6 & 8 & 9 & 10 & 14. Exercises pagination across 25 records (pages 1, 2, 3, and beyond)."""
    res_acc = await async_client.post("/api/v1/accounts", json={"name": "Pagination Acc"})
    account_id = res_acc.json()["id"]

    # Create 25 transactions sequentially
    created_tx_ids = []
    for i in range(25):
        res_tx = await async_client.post(
            f"/api/v1/accounts/{account_id}/transactions",
            json={
                "type": "credit",
                "amount": f"{i + 1}.00",
                "description": f"Deposit #{i + 1}",
                "reference_id": f"REF-PAGINATE-{i + 1}",
            },
        )
        assert res_tx.status_code == 201
        created_tx_ids.append(res_tx.json()["id"])

    # Expected order: newest first (i.e. reversed created_tx_ids)
    expected_order = list(reversed(created_tx_ids))

    # Page 1: limit 10 -> items 0..9
    res_p1 = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=1&limit=10")
    assert res_p1.status_code == 200
    data_p1 = res_p1.json()
    assert len(data_p1["items"]) == 10
    assert data_p1["pagination"] == {
        "current_page": 1,
        "page_size": 10,
        "total_records": 25,
        "total_pages": 3,
    }
    p1_ids = [item["id"] for item in data_p1["items"]]
    assert p1_ids == expected_order[0:10]

    # Page 2: limit 10 -> items 10..19 (middle page)
    res_p2 = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=2&limit=10")
    assert res_p2.status_code == 200
    data_p2 = res_p2.json()
    assert len(data_p2["items"]) == 10
    assert data_p2["pagination"] == {
        "current_page": 2,
        "page_size": 10,
        "total_records": 25,
        "total_pages": 3,
    }
    p2_ids = [item["id"] for item in data_p2["items"]]
    assert p2_ids == expected_order[10:20]

    # Page 3: limit 10 -> items 20..24 (last page, partial 5 items)
    res_p3 = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=3&limit=10")
    assert res_p3.status_code == 200
    data_p3 = res_p3.json()
    assert len(data_p3["items"]) == 5
    assert data_p3["pagination"] == {
        "current_page": 3,
        "page_size": 10,
        "total_records": 25,
        "total_pages": 3,
    }
    p3_ids = [item["id"] for item in data_p3["items"]]
    assert p3_ids == expected_order[20:25]

    # Check complete union across pages has all 25 records with zero overlap
    all_traversed_ids = p1_ids + p2_ids + p3_ids
    assert len(all_traversed_ids) == 25
    assert len(set(all_traversed_ids)) == 25
    assert all_traversed_ids == expected_order

    # Page 4: beyond available results -> empty items list, valid pagination metadata
    res_p4 = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=4&limit=10")
    assert res_p4.status_code == 200
    data_p4 = res_p4.json()
    assert data_p4["items"] == []
    assert data_p4["pagination"] == {
        "current_page": 4,
        "page_size": 10,
        "total_records": 25,
        "total_pages": 3,
    }

    # Page 99: far beyond available results
    res_p99 = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=99&limit=10")
    assert res_p99.status_code == 200
    data_p99 = res_p99.json()
    assert data_p99["items"] == []
    assert data_p99["pagination"]["current_page"] == 99
    assert data_p99["pagination"]["total_records"] == 25
    assert data_p99["pagination"]["total_pages"] == 3


@pytest.mark.asyncio
async def test_integration_transaction_history_deterministic_tie_breaker(
    async_client: AsyncClient,
) -> None:
    """11. Newest-first ordering with deterministic tie-breaking (id DESC) when timestamps are identical."""
    from datetime import UTC, datetime

    res_acc = await async_client.post("/api/v1/accounts", json={"name": "Tie Breaker Acc"})
    account_id = uuid.UUID(res_acc.json()["id"])

    # Directly insert 3 transaction records in PostgreSQL with the EXACT same created_at timestamp
    fixed_time = datetime(2026, 6, 15, 10, 30, 0, tzinfo=UTC)
    id_low = uuid.UUID("11111111-1111-4111-8111-111111111111")
    id_mid = uuid.UUID("22222222-2222-4222-8222-222222222222")
    id_high = uuid.UUID("33333333-3333-4333-8333-333333333333")

    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.begin() as conn:
            for tx_id, ref in [
                (id_low, "REF-TIE-1"),
                (id_mid, "REF-TIE-2"),
                (id_high, "REF-TIE-3"),
            ]:
                await conn.execute(
                    sa.text(
                        """
                        INSERT INTO transactions (id, account_id, type, amount_minor, balance_after_minor, description, reference_id, created_at)
                        VALUES (:id, :account_id, 'credit', 1000, 1000, 'Tie test', :ref, :created_at)
                        """
                    ),
                    {"id": tx_id, "account_id": account_id, "ref": ref, "created_at": fixed_time},
                )
    finally:
        await engine.dispose()

    # Query transaction history
    res = await async_client.get(f"/api/v1/accounts/{account_id}/transactions")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 3

    # All 3 have identical timestamps, so secondary sort key `id DESC` must determine order
    item_ids = [uuid.UUID(item["id"]) for item in items]
    assert item_ids == [id_high, id_mid, id_low]


@pytest.mark.asyncio
async def test_integration_transaction_history_missing_account(
    async_client: AsyncClient,
) -> None:
    """12. Missing account returns HTTP 404 with ACCOUNT_NOT_FOUND error code."""
    missing_id = uuid.uuid4()
    res = await async_client.get(f"/api/v1/accounts/{missing_id}/transactions")
    assert res.status_code == 404
    data = res.json()
    assert data["error"]["code"] == "ACCOUNT_NOT_FOUND"
    assert data["error"]["message"] == "The requested account does not exist."


@pytest.mark.asyncio
async def test_integration_transaction_history_account_isolation(
    async_client: AsyncClient,
) -> None:
    """13. Complete isolation: Account A never sees Account B's transactions."""
    # Create Account A and Account B
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Account Alpha"})
    account_a = res_a.json()["id"]

    res_b = await async_client.post("/api/v1/accounts", json={"name": "Account Beta"})
    account_b = res_b.json()["id"]

    # Post transactions for Account A
    res_a1 = await async_client.post(
        f"/api/v1/accounts/{account_a}/transactions",
        json={
            "type": "credit",
            "amount": "50.00",
            "description": "Alpha credit",
            "reference_id": "REF-ALPHA-1",
        },
    )
    tx_a1_id = res_a1.json()["id"]

    # Post transactions for Account B
    res_b1 = await async_client.post(
        f"/api/v1/accounts/{account_b}/transactions",
        json={
            "type": "credit",
            "amount": "80.00",
            "description": "Beta credit",
            "reference_id": "REF-BETA-1",
        },
    )
    tx_b1_id = res_b1.json()["id"]

    # Query history for Account A
    res_hist_a = await async_client.get(f"/api/v1/accounts/{account_a}/transactions")
    assert res_hist_a.status_code == 200
    a_items = res_hist_a.json()["items"]
    assert len(a_items) == 1
    assert a_items[0]["id"] == tx_a1_id
    assert a_items[0]["account_id"] == account_a
    # Must not contain Beta transaction
    assert all(item["id"] != tx_b1_id for item in a_items)
    assert all(item["account_id"] != account_b for item in a_items)

    # Query history for Account B
    res_hist_b = await async_client.get(f"/api/v1/accounts/{account_b}/transactions")
    assert res_hist_b.status_code == 200
    b_items = res_hist_b.json()["items"]
    assert len(b_items) == 1
    assert b_items[0]["id"] == tx_b1_id
    assert b_items[0]["account_id"] == account_b
    # Must not contain Alpha transaction
    assert all(item["id"] != tx_a1_id for item in b_items)
    assert all(item["account_id"] != account_a for item in b_items)


@pytest.mark.asyncio
async def test_integration_transaction_history_read_only_invariance(
    async_client: AsyncClient,
) -> None:
    """15. Reading history does not modify balances, timestamps, or transaction records."""
    res_acc = await async_client.post("/api/v1/accounts", json={"name": "Invariant Acc"})
    account_id = res_acc.json()["id"]

    # 1. Credit 10,000 paise (₹100.00)
    await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "Deposit",
            "reference_id": "REF-INV-C1",
        },
    )
    # 2. Debit 4,000 paise (₹40.00)
    await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "debit",
            "amount": "40.00",
            "description": "Withdrawal",
            "reference_id": "REF-INV-D1",
        },
    )

    # Fetch initial state from DB
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            acc_row = (
                await conn.execute(
                    sa.text("SELECT balance_minor, updated_at FROM accounts WHERE id = :id"),
                    {"id": uuid.UUID(account_id)},
                )
            ).fetchone()
            assert acc_row is not None
            initial_balance = acc_row[0]
            initial_updated_at = acc_row[1]

            tx_rows = (
                await conn.execute(
                    sa.text(
                        "SELECT id, balance_after_minor FROM transactions WHERE account_id = :id ORDER BY id"
                    ),
                    {"id": uuid.UUID(account_id)},
                )
            ).fetchall()
            initial_tx_data = [(r[0], r[1]) for r in tx_rows]
    finally:
        await engine.dispose()

    assert initial_balance == 6000
    assert len(initial_tx_data) == 2

    # Issue multiple history reads with different filters and pagination
    await async_client.get(f"/api/v1/accounts/{account_id}/transactions")
    await async_client.get(f"/api/v1/accounts/{account_id}/transactions?type=credit")
    await async_client.get(f"/api/v1/accounts/{account_id}/transactions?type=debit")
    await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=1&limit=1")
    await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=2&limit=1")
    await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=99&limit=50")

    # Re-verify PostgreSQL state
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            acc_row_after = (
                await conn.execute(
                    sa.text("SELECT balance_minor, updated_at FROM accounts WHERE id = :id"),
                    {"id": uuid.UUID(account_id)},
                )
            ).fetchone()
            assert acc_row_after is not None
            assert acc_row_after[0] == initial_balance
            assert acc_row_after[1] == initial_updated_at

            tx_rows_after = (
                await conn.execute(
                    sa.text(
                        "SELECT id, balance_after_minor FROM transactions WHERE account_id = :id ORDER BY id"
                    ),
                    {"id": uuid.UUID(account_id)},
                )
            ).fetchall()
            after_tx_data = [(r[0], r[1]) for r in tx_rows_after]
            assert after_tx_data == initial_tx_data
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_null_byte_rejection_leaves_state_intact(
    async_client: AsyncClient,
) -> None:
    """Regression test for V-01: Null-byte rejection in API routes preserves balance and connection."""
    # 1. Null byte in account name is rejected with 422
    acc_null = await async_client.post("/api/v1/accounts", json={"name": "Bad\x00Account"})
    assert acc_null.status_code == 422
    assert acc_null.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "cannot contain null bytes" in str(acc_null.json()["error"]["details"])

    # 2. Create valid account
    acc_ok = await async_client.post("/api/v1/accounts", json={"name": "Null Byte Test Acc"})
    assert acc_ok.status_code == 201
    account_id = acc_ok.json()["id"]

    # Seed balance
    seed = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "50.00",
            "description": "Initial funding",
            "reference_id": f"REF-SEED-{uuid.uuid4().hex[:6]}",
        },
    )
    assert seed.status_code == 201
    assert seed.json()["balance_after"] == "50.00"

    # 3. Reject null bytes in reference_id (beginning, middle, end)
    for ref_payload in ["\x00REF-START", "REF\x00MID", "REF-END\x00"]:
        res = await async_client.post(
            f"/api/v1/accounts/{account_id}/transactions",
            json={
                "type": "credit",
                "amount": "10.00",
                "description": "Valid description",
                "reference_id": ref_payload,
            },
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert "cannot contain null bytes" in str(res.json()["error"]["details"])

    # 4. Reject null bytes in description (beginning, middle, end)
    for desc_payload in ["\x00Desc start", "Desc \x00 mid", "Desc end\x00"]:
        res = await async_client.post(
            f"/api/v1/accounts/{account_id}/transactions",
            json={
                "type": "credit",
                "amount": "10.00",
                "description": desc_payload,
                "reference_id": f"REF-{uuid.uuid4().hex[:6]}",
            },
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"
        assert "cannot contain null bytes" in str(res.json()["error"]["details"])

    # 5. Confirm balance remains exactly 5000 and zero invalid records were persisted
    bal_res = await async_client.get(f"/api/v1/accounts/{account_id}/balance")
    assert bal_res.status_code == 200
    assert bal_res.json()["balance"] == "50.00"

    hist_res = await async_client.get(f"/api/v1/accounts/{account_id}/transactions")
    assert hist_res.status_code == 200
    assert hist_res.json()["pagination"]["total_records"] == 1

    # 6. Verify connection remains fully usable by executing a valid debit
    subsequent = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "debit",
            "amount": "20.00",
            "description": "Subsequent valid debit",
            "reference_id": f"REF-SUBSEQ-{uuid.uuid4().hex[:6]}",
        },
    )
    assert subsequent.status_code == 201
    assert subsequent.json()["balance_after"] == "30.00"


@pytest.mark.asyncio
async def test_integration_rupee_exact_amounts_450_5_and_450_50_and_transfers(
    async_client: AsyncClient,
) -> None:
    """Verifies that '450.5', '450.50', and '1.10' convert to exact integer paise in transactions and transfers."""
    # 1. Create two accounts
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Alice Rupee Test"})
    assert res_a.status_code == 201
    acc_a_id = res_a.json()["id"]

    res_b = await async_client.post("/api/v1/accounts", json={"name": "Bob Rupee Test"})
    assert res_b.status_code == 201
    acc_b_id = res_b.json()["id"]

    # 2. Credit '450.50' (exact user test case)
    tx1 = await async_client.post(
        f"/api/v1/accounts/{acc_a_id}/transactions",
        json={
            "type": "credit",
            "amount": "450.50",
            "description": "Credit 450.50",
            "reference_id": f"REF-45050-{uuid.uuid4().hex[:6]}",
        },
    )
    assert tx1.status_code == 201
    assert tx1.json()["amount"] == "450.50"
    assert tx1.json()["balance_after"] == "450.50"
    assert tx1.json()["currency"] == "INR"

    # 3. Credit '450.5' (single decimal place representing 50 paise)
    tx2 = await async_client.post(
        f"/api/v1/accounts/{acc_a_id}/transactions",
        json={
            "type": "credit",
            "amount": "450.5",
            "description": "Credit 450.5",
            "reference_id": f"REF-4505-{uuid.uuid4().hex[:6]}",
        },
    )
    assert tx2.status_code == 201
    assert tx2.json()["amount"] in ("450.50", "450.5")
    assert tx2.json()["balance_after"] == "901.00"

    # 4. Credit '1.10'
    tx3 = await async_client.post(
        f"/api/v1/accounts/{acc_a_id}/transactions",
        json={
            "type": "credit",
            "amount": "1.10",
            "description": "Credit 1.10",
            "reference_id": f"REF-110-{uuid.uuid4().hex[:6]}",
        },
    )
    assert tx3.status_code == 201
    assert tx3.json()["amount"] == "1.10"
    assert tx3.json()["balance_after"] == "902.10"

    # 5. Transfer '450.50' from Account A to Account B
    xfer1 = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": acc_a_id,
            "destination_account_id": acc_b_id,
            "amount": "450.50",
            "description": "Transfer 450.50",
            "reference_id": f"XFER-45050-{uuid.uuid4().hex[:6]}",
        },
    )
    assert xfer1.status_code == 201
    assert xfer1.json()["amount"] == "450.50"
    assert xfer1.json()["currency"] == "INR"

    # 6. Transfer '450.5' from Account A to Account B
    xfer2 = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": acc_a_id,
            "destination_account_id": acc_b_id,
            "amount": "450.5",
            "description": "Transfer 450.5",
            "reference_id": f"XFER-4505-{uuid.uuid4().hex[:6]}",
        },
    )
    assert xfer2.status_code == 201
    assert xfer2.json()["amount"] in ("450.50", "450.5")

    # 7. Verify balances via API
    bal_a = await async_client.get(f"/api/v1/accounts/{acc_a_id}/balance")
    assert bal_a.status_code == 200
    assert bal_a.json()["balance"] == "1.10"

    bal_b = await async_client.get(f"/api/v1/accounts/{acc_b_id}/balance")
    assert bal_b.status_code == 200
    assert bal_b.json()["balance"] == "901.00"

    # 8. Verify direct database storage in integer paise
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            # Check Account A balance_minor
            acc_a_row = (
                await conn.execute(
                    sa.text("SELECT balance_minor FROM accounts WHERE id = :id"),
                    {"id": uuid.UUID(acc_a_id)},
                )
            ).fetchone()
            assert acc_a_row is not None
            assert acc_a_row[0] == 110  # 110 paise

            # Check Account B balance_minor
            acc_b_row = (
                await conn.execute(
                    sa.text("SELECT balance_minor FROM accounts WHERE id = :id"),
                    {"id": uuid.UUID(acc_b_id)},
                )
            ).fetchone()
            assert acc_b_row is not None
            assert acc_b_row[0] == 90100  # 90100 paise
    finally:
        await engine.dispose()
