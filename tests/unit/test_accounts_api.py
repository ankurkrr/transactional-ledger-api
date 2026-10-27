"""Unit tests for Account API HTTP endpoints and contract compliance."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ledger.api.dependencies import get_account_service
from ledger.core.exceptions import AccountNotFoundError
from ledger.schemas.account import AccountResponse, BalanceResponse


@pytest.mark.asyncio
async def test_create_account_endpoint_success(app: FastAPI, async_client: AsyncClient) -> None:
    """POST /api/v1/accounts returns 201 with generated ID, 0 balance, and timestamps."""
    mock_service = AsyncMock()
    generated_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_service.create_account.return_value = AccountResponse(
        id=generated_id,
        name="Jane Doe",
        balance="0.00",
        currency="INR",
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_account_service] = lambda: mock_service

    try:
        response = await async_client.post(
            "/api/v1/accounts",
            json={"name": "Jane Doe"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == str(generated_id)
        assert data["name"] == "Jane Doe"
        assert data["balance"] == "0.00"
        assert "balance_minor" not in data
        assert data["currency"] == "INR"
        assert "created_at" in data
        assert "updated_at" in data
        assert "X-Request-ID" in response.headers
    finally:
        app.dependency_overrides.pop(get_account_service, None)


@pytest.mark.asyncio
async def test_create_account_rejects_client_supplied_balance(
    async_client: AsyncClient,
) -> None:
    """Client cannot supply balance or balance_minor in account creation payload."""
    # Attempting to supply 'balance'
    res1 = await async_client.post(
        "/api/v1/accounts",
        json={"name": "Jane", "balance": "100.00"},
    )
    assert res1.status_code == 422
    err1 = res1.json()["error"]
    assert err1["code"] == "VALIDATION_ERROR"

    # Attempting to supply 'balance_minor'
    res2 = await async_client.post(
        "/api/v1/accounts",
        json={"name": "Jane", "balance_minor": 1000},
    )
    assert res2.status_code == 422
    err2 = res2.json()["error"]
    assert err2["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_account_rejects_client_supplied_id(
    async_client: AsyncClient,
) -> None:
    """Client cannot supply custom ID; must be server-generated."""
    response = await async_client.post(
        "/api/v1/accounts",
        json={"name": "Jane", "id": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    err = response.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_account_rejects_empty_and_whitespace_names(
    async_client: AsyncClient,
) -> None:
    """Account creation requires a non-empty, non-whitespace name."""
    # Empty string
    res1 = await async_client.post("/api/v1/accounts", json={"name": ""})
    assert res1.status_code == 422
    assert res1.json()["error"]["code"] == "VALIDATION_ERROR"

    # Whitespace only
    res2 = await async_client.post("/api/v1/accounts", json={"name": "    "})
    assert res2.status_code == 422
    assert res2.json()["error"]["code"] == "VALIDATION_ERROR"

    # Missing name field entirely
    res3 = await async_client.post("/api/v1/accounts", json={})
    assert res3.status_code == 422
    assert res3.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_account_rejects_excessive_name_length(
    async_client: AsyncClient,
) -> None:
    """Account name cannot exceed 255 characters."""
    response = await async_client.post(
        "/api/v1/accounts",
        json={"name": "X" * 256},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_get_balance_endpoint_success(app: FastAPI, async_client: AsyncClient) -> None:
    """GET /api/v1/accounts/{id}/balance returns 200 with BalanceResponse schema."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_service = AsyncMock()
    mock_service.get_balance.return_value = BalanceResponse(
        account_id=account_id,
        balance="250.00",
        currency="INR",
        updated_at=now,
    )
    app.dependency_overrides[get_account_service] = lambda: mock_service

    try:
        response = await async_client.get(f"/api/v1/accounts/{account_id}/balance")
        assert response.status_code == 200
        data = response.json()
        assert data["account_id"] == str(account_id)
        assert data["balance"] == "250.00"
        assert "balance_minor" not in data
        assert data["currency"] == "INR"
        assert "updated_at" in data
    finally:
        app.dependency_overrides.pop(get_account_service, None)


@pytest.mark.asyncio
async def test_get_balance_endpoint_not_found(app: FastAPI, async_client: AsyncClient) -> None:
    """GET /api/v1/accounts/{id}/balance returns 404 when account does not exist."""
    account_id = uuid.uuid4()
    mock_service = AsyncMock()
    mock_service.get_balance.side_effect = AccountNotFoundError()
    app.dependency_overrides[get_account_service] = lambda: mock_service

    try:
        response = await async_client.get(f"/api/v1/accounts/{account_id}/balance")
        assert response.status_code == 404
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "ACCOUNT_NOT_FOUND"
        assert data["error"]["message"] == "The requested account does not exist."
        assert "request_id" in data["error"]
        assert "timestamp" in data["error"]
    finally:
        app.dependency_overrides.pop(get_account_service, None)


@pytest.mark.asyncio
async def test_get_balance_endpoint_invalid_uuid(
    async_client: AsyncClient,
) -> None:
    """GET /api/v1/accounts/{id}/balance returns 422 when path param is not a valid UUID."""
    response = await async_client.get("/api/v1/accounts/not-a-valid-uuid/balance")
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "request_id" in data["error"]


@pytest.mark.asyncio
async def test_get_account_endpoint_success(app: FastAPI, async_client: AsyncClient) -> None:
    """GET /api/v1/accounts/{id} returns 200 with full AccountResponse schema."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_service = AsyncMock()
    mock_service.get_account_by_id.return_value = AccountResponse(
        id=account_id,
        name="Alice Wonderland",
        balance="10.00",
        currency="INR",
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_account_service] = lambda: mock_service

    try:
        response = await async_client.get(f"/api/v1/accounts/{account_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(account_id)
        assert data["name"] == "Alice Wonderland"
        assert data["balance"] == "10.00"
        assert "balance_minor" not in data
        assert data["currency"] == "INR"
    finally:
        app.dependency_overrides.pop(get_account_service, None)


@pytest.mark.asyncio
async def test_get_account_endpoint_not_found(app: FastAPI, async_client: AsyncClient) -> None:
    """GET /api/v1/accounts/{id} returns 404 when account does not exist."""
    account_id = uuid.uuid4()
    mock_service = AsyncMock()
    mock_service.get_account_by_id.side_effect = AccountNotFoundError()
    app.dependency_overrides[get_account_service] = lambda: mock_service

    try:
        response = await async_client.get(f"/api/v1/accounts/{account_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "ACCOUNT_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_account_service, None)


@pytest.mark.asyncio
async def test_custom_request_id_propagation(async_client: AsyncClient) -> None:
    """Client-provided X-Request-ID header is propagated to response headers and body."""
    custom_id = "evaluator-trace-token-9988"
    response = await async_client.get(
        f"/api/v1/accounts/{uuid.uuid4()}",
        headers={"X-Request-ID": custom_id},
    )
    assert response.headers.get("X-Request-ID") == custom_id
    if response.status_code >= 400:
        data = response.json()
        assert data["error"]["request_id"] == custom_id
