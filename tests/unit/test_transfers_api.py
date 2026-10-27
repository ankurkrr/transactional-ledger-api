"""Unit tests for Transfer API HTTP endpoints and contract compliance."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ledger.api.dependencies import get_transfer_service
from ledger.core.exceptions import (
    AccountNotFoundError,
    ConcurrencyConflictError,
    CurrencyMismatchError,
    DuplicateReferenceError,
    InsufficientFundsError,
)
from ledger.schemas.transfer import TransferResponse


@pytest.mark.asyncio
async def test_create_transfer_endpoint_success(app: FastAPI, async_client: AsyncClient) -> None:
    """POST /api/v1/transfers returns 201 with valid TransferResponse in rupees."""
    src_id = uuid.uuid4()
    dst_id = uuid.uuid4()
    xfer_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_service = AsyncMock()
    mock_service.execute_transfer.return_value = TransferResponse(
        transfer_id=xfer_id,
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="30.00",
        currency="INR",
        status="completed",
        reference_id="XFER-API-01",
        description="Reimbursement",
        created_at=now,
    )
    app.dependency_overrides[get_transfer_service] = lambda: mock_service

    try:
        response = await async_client.post(
            "/api/v1/transfers",
            json={
                "source_account_id": str(src_id),
                "destination_account_id": str(dst_id),
                "amount": "30.00",
                "description": "Reimbursement",
                "reference_id": "XFER-API-01",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["transfer_id"] == str(xfer_id)
        assert data["source_account_id"] == str(src_id)
        assert data["destination_account_id"] == str(dst_id)
        assert data["amount"] == "30.00"
        assert data["currency"] == "INR"
        assert "amount_minor" not in data
        assert data["status"] == "completed"
        assert data["reference_id"] == "XFER-API-01"
        assert data["description"] == "Reimbursement"
        assert "created_at" in data
        assert "X-Request-ID" in response.headers
    finally:
        app.dependency_overrides.pop(get_transfer_service, None)


@pytest.mark.asyncio
async def test_create_transfer_same_account_returns_422(
    async_client: AsyncClient,
) -> None:
    """POST /api/v1/transfers with same source and destination returns 422 TRANSFER_SAME_ACCOUNT."""
    same_id = str(uuid.uuid4())
    response = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": same_id,
            "destination_account_id": same_id,
            "amount": "10.00",
            "description": "Self transfer",
            "reference_id": "XFER-SAME-API",
        },
    )
    assert response.status_code == 422
    data = response.json()
    assert data["error"]["code"] == "TRANSFER_SAME_ACCOUNT"
    assert data["error"]["message"] == "Source and destination accounts cannot be identical."


@pytest.mark.asyncio
async def test_create_transfer_missing_account_returns_404(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """POST /api/v1/transfers when account missing returns 404 ACCOUNT_NOT_FOUND."""
    src_id = uuid.uuid4()
    dst_id = uuid.uuid4()

    mock_service = AsyncMock()
    mock_service.execute_transfer.side_effect = AccountNotFoundError()
    app.dependency_overrides[get_transfer_service] = lambda: mock_service

    try:
        response = await async_client.post(
            "/api/v1/transfers",
            json={
                "source_account_id": str(src_id),
                "destination_account_id": str(dst_id),
                "amount": "10.00",
                "description": "Missing account transfer",
                "reference_id": "XFER-MISSING-API",
            },
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "ACCOUNT_NOT_FOUND"
        assert data["error"]["message"] == "The requested account does not exist."
    finally:
        app.dependency_overrides.pop(get_transfer_service, None)


@pytest.mark.asyncio
async def test_create_transfer_insufficient_funds_returns_422(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """POST /api/v1/transfers when insufficient funds returns 422 INSUFFICIENT_FUNDS."""
    src_id = uuid.uuid4()
    dst_id = uuid.uuid4()

    mock_service = AsyncMock()
    mock_service.execute_transfer.side_effect = InsufficientFundsError()
    app.dependency_overrides[get_transfer_service] = lambda: mock_service

    try:
        response = await async_client.post(
            "/api/v1/transfers",
            json={
                "source_account_id": str(src_id),
                "destination_account_id": str(dst_id),
                "amount": "500.00",
                "description": "Overdraft transfer",
                "reference_id": "XFER-OVER-API",
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == "INSUFFICIENT_FUNDS"
    finally:
        app.dependency_overrides.pop(get_transfer_service, None)


@pytest.mark.asyncio
async def test_create_transfer_duplicate_reference_returns_409(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """POST /api/v1/transfers with duplicate reference returns 409 DUPLICATE_REFERENCE."""
    src_id = uuid.uuid4()
    dst_id = uuid.uuid4()

    mock_service = AsyncMock()
    mock_service.execute_transfer.side_effect = DuplicateReferenceError()
    app.dependency_overrides[get_transfer_service] = lambda: mock_service

    try:
        response = await async_client.post(
            "/api/v1/transfers",
            json={
                "source_account_id": str(src_id),
                "destination_account_id": str(dst_id),
                "amount": "10.00",
                "description": "Duplicate transfer",
                "reference_id": "XFER-DUP-API",
            },
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "DUPLICATE_REFERENCE"
        assert data["error"]["details"] == []
    finally:
        app.dependency_overrides.pop(get_transfer_service, None)


@pytest.mark.asyncio
async def test_create_transfer_currency_mismatch_returns_422(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """POST /api/v1/transfers between incompatible currencies returns 422 CURRENCY_MISMATCH."""
    src_id = uuid.uuid4()
    dst_id = uuid.uuid4()

    mock_service = AsyncMock()
    mock_service.execute_transfer.side_effect = CurrencyMismatchError()
    app.dependency_overrides[get_transfer_service] = lambda: mock_service

    try:
        response = await async_client.post(
            "/api/v1/transfers",
            json={
                "source_account_id": str(src_id),
                "destination_account_id": str(dst_id),
                "amount": "10.00",
                "description": "Currency mismatch test",
                "reference_id": "XFER-CURR-API",
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == "CURRENCY_MISMATCH"
    finally:
        app.dependency_overrides.pop(get_transfer_service, None)


@pytest.mark.asyncio
async def test_create_transfer_concurrency_conflict_returns_503(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """POST /api/v1/transfers on deadlock conflict returns 503 CONCURRENCY_CONFLICT."""
    src_id = uuid.uuid4()
    dst_id = uuid.uuid4()

    mock_service = AsyncMock()
    mock_service.execute_transfer.side_effect = ConcurrencyConflictError()
    app.dependency_overrides[get_transfer_service] = lambda: mock_service

    try:
        response = await async_client.post(
            "/api/v1/transfers",
            json={
                "source_account_id": str(src_id),
                "destination_account_id": str(dst_id),
                "amount": "10.00",
                "description": "Deadlock transfer",
                "reference_id": "XFER-CONFLICT-API",
            },
        )
        assert response.status_code == 503
        data = response.json()
        assert data["error"]["code"] == "CONCURRENCY_CONFLICT"
    finally:
        app.dependency_overrides.pop(get_transfer_service, None)


@pytest.mark.asyncio
async def test_create_transfer_validates_amounts_strictly(
    async_client: AsyncClient,
) -> None:
    """Rejects zero, negative, float, boolean, and out-of-range amounts with 422 VALIDATION_ERROR."""
    src_id = str(uuid.uuid4())
    dst_id = str(uuid.uuid4())

    invalid_amounts = [
        0,
        -1,
        -500,
        25.50,
        True,
        False,
        1000,
        "0",
        "0.00",
        "-10.00",
        "25.555",
        "NaN",
        "Inf",
        "92233720368547758.08",
    ]

    for val in invalid_amounts:
        response = await async_client.post(
            "/api/v1/transfers",
            json={
                "source_account_id": src_id,
                "destination_account_id": dst_id,
                "amount": val,
                "description": "Validation test",
                "reference_id": f"XFER-VAL-{uuid.uuid4()}",
            },
        )
        assert response.status_code == 422, (
            f"Expected 422 for amount={val}, got {response.status_code}"
        )
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_create_transfer_forbids_extra_fields(
    async_client: AsyncClient,
) -> None:
    """Client cannot supply extra fields like status, transfer_id, or amount_minor."""
    src_id = str(uuid.uuid4())
    dst_id = str(uuid.uuid4())

    response = await async_client.post(
        "/api/v1/transfers",
        json={
            "source_account_id": src_id,
            "destination_account_id": dst_id,
            "amount": "10.00",
            "description": "Extra fields attempt",
            "reference_id": "XFER-EXTRA-01",
            "status": "completed",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
