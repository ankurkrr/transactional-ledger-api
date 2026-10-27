"""Integration tests for Transfer endpoints executed against real PostgreSQL.

Validates atomic cross-account transfers, balance reconciliation, deterministic
lock ordering, concurrency safety, duplicate reference handling, and rollback guarantees.
"""

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
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
async def test_integration_successful_transfer_flow(
    async_client: AsyncClient,
) -> None:
    """1, 2, 3, 4. End-to-end transfer execution, balances, transfer record, and dual transactions."""
    # Create Account A and Account B
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Transfer Src Alpha"})
    assert res_a.status_code == 201
    src_id = res_a.json()["id"]

    res_b = await async_client.post("/api/v1/accounts", json={"name": "Transfer Dst Beta"})
    assert res_b.status_code == 201
    dst_id = res_b.json()["id"]

    # Seed Account A with 10,000 paise (₹100) and Account B with 2,000 paise (₹20)
    await async_client.post(
        f"/api/v1/accounts/{src_id}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "Seed A",
            "reference_id": f"REF-SEED-{uuid.uuid4()}",
        },
    )
    await async_client.post(
        f"/api/v1/accounts/{dst_id}/transactions",
        json={
            "type": "credit",
            "amount": "20.00",
            "description": "Seed B",
            "reference_id": f"REF-SEED-{uuid.uuid4()}",
        },
    )

    # Execute transfer: 3,500 paise (₹35.00) from A to B
    xfer_ref = f"XFER-{uuid.uuid4().hex[:8]}"
    xfer_res = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_id,
            "destination_account_id": dst_id,
            "amount": "35.00",
            "description": "Consulting reimbursement",
            "reference_id": xfer_ref,
        },
    )
    assert xfer_res.status_code == 201
    xfer_data = xfer_res.json()
    transfer_id = xfer_data["transfer_id"]
    assert xfer_data["source_account_id"] == src_id
    assert xfer_data["destination_account_id"] == dst_id
    assert xfer_data["amount"] == "35.00"
    assert xfer_data["currency"] == "INR"
    assert xfer_data["status"] == "completed"
    assert xfer_data["reference_id"] == xfer_ref

    # 2. Verify account balances
    bal_a = await async_client.get(f"/api/v1/accounts/{src_id}/balance")
    assert bal_a.json()["balance"] == "65.00"  # 100.00 - 35.00

    bal_b = await async_client.get(f"/api/v1/accounts/{dst_id}/balance")
    assert bal_b.json()["balance"] == "55.00"  # 20.00 + 35.00

    # 3 & 4. Verify PostgreSQL transfer and dual transaction records
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            # Transfer record
            t_row = (
                await conn.execute(
                    sa.text(
                        "SELECT source_account_id, destination_account_id, amount_minor, status, reference_id FROM transfers WHERE id = :id"
                    ),
                    {"id": uuid.UUID(transfer_id)},
                )
            ).fetchone()
            assert t_row is not None
            assert t_row[0] == uuid.UUID(src_id)
            assert t_row[1] == uuid.UUID(dst_id)
            assert t_row[2] == 3500
            assert t_row[3] == "completed"
            assert t_row[4] == xfer_ref

            # Transactions referencing this transfer
            tx_rows = (
                await conn.execute(
                    sa.text(
                        "SELECT account_id, type, amount_minor, balance_after_minor, reference_id, transfer_id FROM transactions WHERE transfer_id = :tid ORDER BY type DESC"
                    ),
                    {"tid": uuid.UUID(transfer_id)},
                )
            ).fetchall()
            assert len(tx_rows) == 2

            # Debit record for source
            debit = [r for r in tx_rows if r[1] == "debit"][0]
            assert debit[0] == uuid.UUID(src_id)
            assert debit[2] == 3500
            assert debit[3] == 6500
            assert debit[4] == xfer_ref
            assert debit[5] == uuid.UUID(transfer_id)

            # Credit record for destination
            credit = [r for r in tx_rows if r[1] == "credit"][0]
            assert credit[0] == uuid.UUID(dst_id)
            assert credit[2] == 3500
            assert credit[3] == 5500
            assert credit[4] == xfer_ref
            assert credit[5] == uuid.UUID(transfer_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_missing_accounts_returns_404(
    async_client: AsyncClient,
) -> None:
    """5 & 6. Missing source or destination account returns HTTP 404 ACCOUNT_NOT_FOUND."""
    res_acc = await async_client.post("/api/v1/accounts", json={"name": "Existing Acc"})
    acc_id = res_acc.json()["id"]
    missing_id = str(uuid.uuid4())

    # Missing source
    res_src = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": missing_id,
            "destination_account_id": acc_id,
            "amount": "10.00",
            "description": "Missing source",
            "reference_id": f"XFER-{uuid.uuid4()}",
        },
    )
    assert res_src.status_code == 404
    assert res_src.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"

    # Missing destination
    res_dst = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": acc_id,
            "destination_account_id": missing_id,
            "amount": "10.00",
            "description": "Missing destination",
            "reference_id": f"XFER-{uuid.uuid4()}",
        },
    )
    assert res_dst.status_code == 404
    assert res_dst.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_integration_same_account_transfer_returns_422(
    async_client: AsyncClient,
) -> None:
    """7. Transfer between identical source and destination accounts returns 422 TRANSFER_SAME_ACCOUNT."""
    res_acc = await async_client.post("/api/v1/accounts", json={"name": "Self Acc"})
    acc_id = res_acc.json()["id"]

    res = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": acc_id,
            "destination_account_id": acc_id,
            "amount": "10.00",
            "description": "Self transfer",
            "reference_id": f"XFER-{uuid.uuid4()}",
        },
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "TRANSFER_SAME_ACCOUNT"


@pytest.mark.asyncio
async def test_integration_insufficient_funds_leaves_state_untouched(
    async_client: AsyncClient,
) -> None:
    """8. Transfer exceeding source balance is rejected with 422; both accounts and DB untouched."""
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Low Balance Src"})
    src_id = res_a.json()["id"]
    res_b = await async_client.post("/api/v1/accounts", json={"name": "Dst Acc"})
    dst_id = res_b.json()["id"]

    # Seed source with 1,000 paise (₹10.00)
    await async_client.post(
        f"/api/v1/accounts/{src_id}/transactions",
        json={
            "type": "credit",
            "amount": "10.00",
            "description": "Seed",
            "reference_id": f"REF-{uuid.uuid4()}",
        },
    )

    # Attempt to transfer 2,000 paise (₹20.00)
    xfer_res = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_id,
            "destination_account_id": dst_id,
            "amount": "20.00",
            "description": "Overdraft attempt",
            "reference_id": f"XFER-FAIL-{uuid.uuid4()}",
        },
    )
    assert xfer_res.status_code == 422
    assert xfer_res.json()["error"]["code"] == "INSUFFICIENT_FUNDS"

    # Verify balances untouched
    assert (await async_client.get(f"/api/v1/accounts/{src_id}/balance")).json()[
        "balance"
    ] == "10.00"
    assert (await async_client.get(f"/api/v1/accounts/{dst_id}/balance")).json()[
        "balance"
    ] == "0.00"

    # Verify no transfer record in DB
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            xfer_count = (
                await conn.execute(
                    sa.text("SELECT count(*) FROM transfers WHERE source_account_id = :id"),
                    {"id": uuid.UUID(src_id)},
                )
            ).scalar()
            assert xfer_count == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_duplicate_transfer_reference_rejection(
    async_client: AsyncClient,
) -> None:
    """10 & 12. Duplicate reference_id is rejected with 409 DUPLICATE_REFERENCE globally."""
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Src 1"})
    src_id = res_a.json()["id"]
    res_b = await async_client.post("/api/v1/accounts", json={"name": "Dst 1"})
    dst_id = res_b.json()["id"]

    await async_client.post(
        f"/api/v1/accounts/{src_id}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "Seed",
            "reference_id": f"REF-{uuid.uuid4()}",
        },
    )

    xfer_ref = f"XFER-GLOBAL-{uuid.uuid4().hex[:8]}"

    # First transfer succeeds
    r1 = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_id,
            "destination_account_id": dst_id,
            "amount": "20.00",
            "description": "First transfer",
            "reference_id": xfer_ref,
        },
    )
    assert r1.status_code == 201

    # Second transfer with SAME reference_id (same accounts) -> 409
    r2 = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_id,
            "destination_account_id": dst_id,
            "amount": "20.00",
            "description": "Duplicate transfer",
            "reference_id": xfer_ref,
        },
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "DUPLICATE_REFERENCE"

    # 12. Attempt to reuse reference across DIFFERENT accounts C -> D -> 409
    res_c = await async_client.post("/api/v1/accounts", json={"name": "Src 2"})
    src_c = res_c.json()["id"]
    res_d = await async_client.post("/api/v1/accounts", json={"name": "Dst 2"})
    dst_d = res_d.json()["id"]
    await async_client.post(
        f"/api/v1/accounts/{src_c}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "Seed C",
            "reference_id": f"REF-{uuid.uuid4()}",
        },
    )

    r3 = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_c,
            "destination_account_id": dst_d,
            "amount": "10.00",
            "description": "Reusing reference on different accounts",
            "reference_id": xfer_ref,
        },
    )
    assert r3.status_code == 409
    assert r3.json()["error"]["code"] == "DUPLICATE_REFERENCE"


@pytest.mark.asyncio
async def test_integration_concurrent_duplicate_transfers(
    async_client: AsyncClient,
) -> None:
    """11. Concurrent duplicate transfer requests: exactly one succeeds, one receives 409."""
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Concurrent Dup Src"})
    src_id = res_a.json()["id"]
    res_b = await async_client.post("/api/v1/accounts", json={"name": "Concurrent Dup Dst"})
    dst_id = res_b.json()["id"]

    await async_client.post(
        f"/api/v1/accounts/{src_id}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "Seed",
            "reference_id": f"REF-{uuid.uuid4()}",
        },
    )

    shared_ref = f"XFER-RACE-{uuid.uuid4().hex[:8]}"
    payload = {
        "source_account_id": src_id,
        "destination_account_id": dst_id,
        "amount": "20.00",
        "description": "Concurrent duplicate test",
        "reference_id": shared_ref,
    }

    # Fire 2 identical requests concurrently
    responses = await asyncio.gather(
        async_client.post("/api/v1/transfers", json=payload),
        async_client.post("/api/v1/transfers", json=payload),
    )

    status_codes = sorted([r.status_code for r in responses])
    assert status_codes == [201, 409]

    # Verify balance was decremented only ONCE
    bal_a = (await async_client.get(f"/api/v1/accounts/{src_id}/balance")).json()["balance"]
    assert bal_a == "80.00"  # 100.00 - 20.00, not 60.00!


@pytest.mark.asyncio
async def test_integration_destination_balance_overflow_rollback(
    async_client: AsyncClient,
) -> None:
    """16. Destination balance exceeding signed BIGINT maximum rolls back source debit and inserts."""
    max_bigint = 9_223_372_036_854_775_807
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Overflow Src"})
    src_id = res_a.json()["id"]
    res_b = await async_client.post("/api/v1/accounts", json={"name": "Overflow Dst"})
    dst_id = res_b.json()["id"]

    # Seed source with 5,000 paise (₹50.00)
    await async_client.post(
        f"/api/v1/accounts/{src_id}/transactions",
        json={
            "type": "credit",
            "amount": "50.00",
            "description": "Seed A",
            "reference_id": f"REF-{uuid.uuid4()}",
        },
    )

    # Directly set destination balance to (MAX_BIGINT - 50) via SQL
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE accounts SET balance_minor = :bal WHERE id = :id"),
                {"bal": max_bigint - 50, "id": uuid.UUID(dst_id)},
            )
    finally:
        await engine.dispose()

    # Attempt to transfer 100 paise (₹1.00) (destination balance would become MAX_BIGINT + 50)
    xfer_res = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_id,
            "destination_account_id": dst_id,
            "amount": "1.00",
            "description": "Overflow transfer attempt",
            "reference_id": f"XFER-OVERFLOW-{uuid.uuid4()}",
        },
    )
    assert xfer_res.status_code == 422
    assert xfer_res.json()["error"]["code"] == "INVALID_AMOUNT"

    # Verify source balance is untouched at 5,000 paise (₹50.00)
    bal_a = (await async_client.get(f"/api/v1/accounts/{src_id}/balance")).json()["balance"]
    assert bal_a == "50.00"

    # Verify destination balance is untouched
    bal_b = (await async_client.get(f"/api/v1/accounts/{dst_id}/balance")).json()["balance"]
    assert bal_b == "92233720368547757.57"


@pytest.mark.asyncio
async def test_integration_concurrent_transfers_cannot_overdraw(
    async_client: AsyncClient,
) -> None:
    """17. Concurrent transfers cannot overdraw source account under competitive load."""
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Limited Src"})
    src_id = res_a.json()["id"]
    res_b = await async_client.post("/api/v1/accounts", json={"name": "Limited Dst"})
    dst_id = res_b.json()["id"]

    # Seed source with 5,000 paise (₹50.00)
    await async_client.post(
        f"/api/v1/accounts/{src_id}/transactions",
        json={
            "type": "credit",
            "amount": "50.00",
            "description": "Seed",
            "reference_id": f"REF-{uuid.uuid4()}",
        },
    )

    # 3 concurrent transfers of 3,000 paise (₹30.00) each (total 9,000 > 5,000)
    tasks = [
        async_client.post(
            "/api/v1/transfers",
            json={
                "source_account_id": src_id,
                "destination_account_id": dst_id,
                "amount": "30.00",
                "description": f"Competitive transfer {i}",
                "reference_id": f"XFER-COMPETE-{i}-{uuid.uuid4().hex[:6]}",
            },
        )
        for i in range(3)
    ]
    responses = await asyncio.gather(*tasks)
    status_codes = [r.status_code for r in responses]

    # Exactly 1 must succeed (201), and 2 must fail with 422 (INSUFFICIENT_FUNDS)
    assert status_codes.count(201) == 1
    assert status_codes.count(422) == 2

    bal_a = (await async_client.get(f"/api/v1/accounts/{src_id}/balance")).json()["balance"]
    bal_b = (await async_client.get(f"/api/v1/accounts/{dst_id}/balance")).json()["balance"]
    assert bal_a == "20.00"  # 50.00 - 30.00
    assert bal_b == "30.00"


@pytest.mark.asyncio
async def test_integration_opposing_direction_concurrent_transfers(
    async_client: AsyncClient,
) -> None:
    """18. Opposing concurrent transfers (A -> B and B -> A) execute safely without deadlocks."""
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Oppose Alpha"})
    src_a = res_a.json()["id"]
    res_b = await async_client.post("/api/v1/accounts", json={"name": "Oppose Beta"})
    src_b = res_b.json()["id"]

    # Seed both accounts with 10,000 paise (₹100.00) each (total 20,000 paise)
    await async_client.post(
        f"/api/v1/accounts/{src_a}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "Seed A",
            "reference_id": f"REF-A-{uuid.uuid4()}",
        },
    )
    await async_client.post(
        f"/api/v1/accounts/{src_b}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "Seed B",
            "reference_id": f"REF-B-{uuid.uuid4()}",
        },
    )

    # Concurrently fire:
    # Transfer 1: A -> B of 2,000 paise (₹20.00)
    # Transfer 2: B -> A of 3,000 paise (₹30.00)
    t1 = async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_a,
            "destination_account_id": src_b,
            "amount": "20.00",
            "description": "Alpha to Beta",
            "reference_id": f"XFER-A-B-{uuid.uuid4()}",
        },
    )
    t2 = async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_b,
            "destination_account_id": src_a,
            "amount": "30.00",
            "description": "Beta to Alpha",
            "reference_id": f"XFER-B-A-{uuid.uuid4()}",
        },
    )

    r1, r2 = await asyncio.gather(t1, t2)
    assert r1.status_code == 201
    assert r2.status_code == 201

    # Balances:
    # A: 100.00 - 20.00 + 30.00 = 110.00
    # B: 100.00 + 20.00 - 30.00 = 90.00
    bal_a = (await async_client.get(f"/api/v1/accounts/{src_a}/balance")).json()["balance"]
    bal_b = (await async_client.get(f"/api/v1/accounts/{src_b}/balance")).json()["balance"]
    assert bal_a == "110.00"
    assert bal_b == "90.00"


@pytest.mark.asyncio
async def test_integration_ledger_reconciliation_and_history_consistency(
    async_client: AsyncClient,
) -> None:
    """19 & 20. Stored balance reconciles with transaction ledger, and history remains mutually consistent."""
    res_a = await async_client.post("/api/v1/accounts", json={"name": "Recon Alpha"})
    acc_a = res_a.json()["id"]
    res_b = await async_client.post("/api/v1/accounts", json={"name": "Recon Beta"})
    acc_b = res_b.json()["id"]

    # 1. Credit A: 15,000 paise (₹150.00)
    await async_client.post(
        f"/api/v1/accounts/{acc_a}/transactions",
        json={
            "type": "credit",
            "amount": "150.00",
            "description": "Deposit",
            "reference_id": f"REF-REC-1-{uuid.uuid4().hex[:8]}",
        },
    )
    # 2. Transfer A -> B: 4,000 paise (₹40.00)
    xfer1_ref = f"XFER-REC-1-{uuid.uuid4().hex[:8]}"
    xfer_res = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": acc_a,
            "destination_account_id": acc_b,
            "amount": "40.00",
            "description": "Transfer to Beta",
            "reference_id": xfer1_ref,
        },
    )
    assert xfer_res.status_code == 201
    assert xfer_res.json()["transfer_id"] is not None

    # 3. Debit A: 2,000 paise (₹20.00)
    await async_client.post(
        f"/api/v1/accounts/{acc_a}/transactions",
        json={
            "type": "debit",
            "amount": "20.00",
            "description": "ATM",
            "reference_id": f"REF-REC-2-{uuid.uuid4().hex[:8]}",
        },
    )

    # Verify Account A balance: 150.00 - 40.00 - 20.00 = 90.00
    # Verify Account B balance: 40.00
    bal_a_resp = (await async_client.get(f"/api/v1/accounts/{acc_a}/balance")).json()["balance"]
    bal_b_resp = (await async_client.get(f"/api/v1/accounts/{acc_b}/balance")).json()["balance"]
    assert bal_a_resp == "90.00"
    assert bal_b_resp == "40.00"

    # 19. SQL Ledger Reconciliation Audit (internal paise storage remains integer)
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.connect() as conn:
            reconciled_a = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT COALESCE(SUM(CASE WHEN type = 'credit' THEN amount_minor ELSE -amount_minor END), 0)
                        FROM transactions WHERE account_id = :id
                        """
                    ),
                    {"id": uuid.UUID(acc_a)},
                )
            ).scalar()
            assert reconciled_a == 9000

            reconciled_b = (
                await conn.execute(
                    sa.text(
                        """
                        SELECT COALESCE(SUM(CASE WHEN type = 'credit' THEN amount_minor ELSE -amount_minor END), 0)
                        FROM transactions WHERE account_id = :id
                        """
                    ),
                    {"id": uuid.UUID(acc_b)},
                )
            ).scalar()
            assert reconciled_b == 4000
    finally:
        await engine.dispose()

    # 20. Mutual consistency in history endpoint
    hist_a = (await async_client.get(f"/api/v1/accounts/{acc_a}/transactions")).json()
    assert len(hist_a["items"]) == 3
    xfer_debit = [item for item in hist_a["items"] if item["reference_id"] == xfer1_ref][0]
    assert xfer_debit["type"] == "debit"
    assert xfer_debit["amount"] == "40.00"
    assert xfer_debit["balance_after"] == "110.00"  # 150.00 - 40.00
    assert xfer_debit["currency"] == "INR"

    hist_b = (await async_client.get(f"/api/v1/accounts/{acc_b}/transactions")).json()
    assert len(hist_b["items"]) == 1
    xfer_credit = hist_b["items"][0]
    assert xfer_credit["reference_id"] == xfer1_ref
    assert xfer_credit["type"] == "credit"
    assert xfer_credit["amount"] == "40.00"
    assert xfer_credit["balance_after"] == "40.00"
    assert xfer_credit["currency"] == "INR"


@pytest.mark.asyncio
async def test_integration_transfer_currency_mismatch_rejected(
    async_client: AsyncClient,
) -> None:
    """Non-INR currency or mismatched currency between accounts is rejected with 422 CURRENCY_MISMATCH."""
    res_a = await async_client.post("/api/v1/accounts", json={"name": "INR Account"})
    src_a = res_a.json()["id"]
    res_b = await async_client.post("/api/v1/accounts", json={"name": "USD Account"})
    dst_b = res_b.json()["id"]

    # Seed source A with 100.00
    await async_client.post(
        f"/api/v1/accounts/{src_a}/transactions",
        json={
            "type": "credit",
            "amount": "100.00",
            "description": "Seed",
            "reference_id": f"REF-{uuid.uuid4()}",
        },
    )

    # Directly set destination account currency to USD in PostgreSQL
    engine = create_async_engine(_get_test_db_url())
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE accounts SET currency = 'USD' WHERE id = :id"),
                {"id": uuid.UUID(dst_b)},
            )
    finally:
        await engine.dispose()

    # Attempt transfer from INR to USD account
    res = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_a,
            "destination_account_id": dst_b,
            "amount": "10.00",
            "description": "Mismatch transfer",
            "reference_id": f"XFER-CURR-{uuid.uuid4()}",
        },
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "CURRENCY_MISMATCH"

    # Verify source balance remains untouched
    bal_a = (await async_client.get(f"/api/v1/accounts/{src_a}/balance")).json()["balance"]
    assert bal_a == "100.00"
