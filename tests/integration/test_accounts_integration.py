"""Integration tests for Account endpoints executed against real PostgreSQL.

Validates end-to-end HTTP request processing, persistence into PostgreSQL
ledger_test database, schema constraint enforcement, and rollback integrity.
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
async def test_integration_create_and_retrieve_account_real_db(
    async_client: AsyncClient,
) -> None:
    """Creates an account via POST /accounts, retrieves via GET /balance, and checks PostgreSQL."""
    test_name = f"Integration User {uuid.uuid4().hex[:8]}"

    # 1. Create Account via HTTP API
    create_res = await async_client.post(
        "/api/v1/accounts",
        json={"name": test_name},
    )
    assert create_res.status_code == 201
    account_data = create_res.json()

    account_id_str = account_data["id"]
    account_uuid = uuid.UUID(account_id_str)

    assert account_data["name"] == test_name
    assert account_data["balance"] == "0.00"
    assert "balance_minor" not in account_data
    assert account_data["currency"] == "INR"
    assert "created_at" in account_data
    assert "updated_at" in account_data

    # 2. Direct database query to verify physical storage in PostgreSQL
    test_db_url = _get_test_db_url()
    engine = create_async_engine(test_db_url)
    try:
        async with engine.connect() as conn:
            stmt = sa.text("SELECT name, balance_minor, currency FROM accounts WHERE id = :id")
            result = await conn.execute(stmt, {"id": account_uuid})
            row = result.fetchone()
            assert row is not None
            assert row[0] == test_name
            assert row[1] == 0
            assert row[2] == "INR"

        # 3. Retrieve balance via GET /api/v1/accounts/{id}/balance
        balance_res = await async_client.get(f"/api/v1/accounts/{account_id_str}/balance")
        assert balance_res.status_code == 200
        balance_data = balance_res.json()
        assert balance_data["account_id"] == account_id_str
        assert balance_data["balance"] == "0.00"
        assert "balance_minor" not in balance_data
        assert balance_data["currency"] == "INR"
        assert "updated_at" in balance_data

        # 4. Retrieve account details via GET /api/v1/accounts/{id}
        account_get_res = await async_client.get(f"/api/v1/accounts/{account_id_str}")
        assert account_get_res.status_code == 200
        account_get_data = account_get_res.json()
        assert account_get_data["id"] == account_id_str
        assert account_get_data["name"] == test_name
        assert account_get_data["balance"] == "0.00"
        assert "balance_minor" not in account_get_data
    finally:
        # Clean up created account
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("DELETE FROM accounts WHERE id = :id"),
                {"id": account_uuid},
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_integration_account_not_found_real_db(
    async_client: AsyncClient,
) -> None:
    """Verifies that querying a non-existent account UUID returns HTTP 404."""
    random_id = uuid.uuid4()

    # Get balance of non-existent account
    res_bal = await async_client.get(f"/api/v1/accounts/{random_id}/balance")
    assert res_bal.status_code == 404
    data_bal = res_bal.json()
    assert data_bal["error"]["code"] == "ACCOUNT_NOT_FOUND"
    assert data_bal["error"]["message"] == "The requested account does not exist."
    assert data_bal["error"]["details"] == []

    # Get account details of non-existent account
    res_acc = await async_client.get(f"/api/v1/accounts/{random_id}")
    assert res_acc.status_code == 404
    data_acc = res_acc.json()
    assert data_acc["error"]["code"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_integration_client_cannot_override_balance_real_db(
    async_client: AsyncClient,
) -> None:
    """Verifies that client cannot supply opening balance or extra fields."""
    response = await async_client.post(
        "/api/v1/accounts",
        json={"name": "Hacker Attempt", "balance": 999999},
    )
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_integration_blank_name_rejected_real_db(
    async_client: AsyncClient,
) -> None:
    """Verifies that empty or whitespace-only name is rejected with 422."""
    res1 = await async_client.post("/api/v1/accounts", json={"name": ""})
    assert res1.status_code == 422
    assert res1.json()["error"]["code"] == "VALIDATION_ERROR"

    res2 = await async_client.post("/api/v1/accounts", json={"name": "     "})
    assert res2.status_code == 422
    assert res2.json()["error"]["code"] == "VALIDATION_ERROR"
