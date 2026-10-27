"""Unit tests for Transaction API HTTP endpoints and contract compliance."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ledger.api.dependencies import get_ledger_service
from ledger.core.exceptions import (
    AccountNotFoundError,
    DuplicateReferenceError,
    InsufficientFundsError,
)
from ledger.schemas.transaction import (
    PaginationMetadata,
    TransactionHistoryResponse,
    TransactionResponse,
)


@pytest.mark.asyncio
async def test_add_transaction_credit_endpoint_success(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """POST /api/v1/accounts/{id}/transactions credit returns 201 with TransactionResponse in rupees."""
    account_id = uuid.uuid4()
    tx_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_service = AsyncMock()
    mock_service.add_transaction.return_value = TransactionResponse(
        id=tx_id,
        account_id=account_id,
        type="credit",
        amount="50.00",
        balance_after="150.00",
        currency="INR",
        description="Invoice payment",
        reference_id="REF-INV-001",
        created_at=now,
    )
    app.dependency_overrides[get_ledger_service] = lambda: mock_service

    try:
        response = await async_client.post(
            f"/api/v1/accounts/{account_id}/transactions",
            json={
                "type": "credit",
                "amount": "50.00",
                "description": "Invoice payment",
                "reference_id": "REF-INV-001",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == str(tx_id)
        assert data["account_id"] == str(account_id)
        assert data["type"] == "credit"
        assert data["amount"] == "50.00"
        assert data["balance_after"] == "150.00"
        assert data["currency"] == "INR"
        assert "amount_minor" not in data
        assert "balance_after_minor" not in data
        assert data["description"] == "Invoice payment"
        assert data["reference_id"] == "REF-INV-001"
        assert "created_at" in data
        assert "X-Request-ID" in response.headers
    finally:
        app.dependency_overrides.pop(get_ledger_service, None)


@pytest.mark.asyncio
async def test_add_transaction_debit_endpoint_success(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """POST /api/v1/accounts/{id}/transactions debit returns 201 with decreased balance in rupees."""
    account_id = uuid.uuid4()
    tx_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_service = AsyncMock()
    mock_service.add_transaction.return_value = TransactionResponse(
        id=tx_id,
        account_id=account_id,
        type="debit",
        amount="20.00",
        balance_after="80.00",
        currency="INR",
        description="Hosting renewal",
        reference_id="REF-AWS-01",
        created_at=now,
    )
    app.dependency_overrides[get_ledger_service] = lambda: mock_service

    try:
        response = await async_client.post(
            f"/api/v1/accounts/{account_id}/transactions",
            json={
                "type": "debit",
                "amount": "20.00",
                "description": "Hosting renewal",
                "reference_id": "REF-AWS-01",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "debit"
        assert data["amount"] == "20.00"
        assert data["balance_after"] == "80.00"
        assert data["currency"] == "INR"
    finally:
        app.dependency_overrides.pop(get_ledger_service, None)


@pytest.mark.asyncio
async def test_add_transaction_insufficient_funds_returns_422(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """Debit exceeding balance returns HTTP 422 with INSUFFICIENT_FUNDS error code."""
    account_id = uuid.uuid4()
    mock_service = AsyncMock()
    mock_service.add_transaction.side_effect = InsufficientFundsError()
    app.dependency_overrides[get_ledger_service] = lambda: mock_service

    try:
        response = await async_client.post(
            f"/api/v1/accounts/{account_id}/transactions",
            json={
                "type": "debit",
                "amount": "500.00",
                "description": "Overdraft attempt",
                "reference_id": "REF-OVERDRAFT-01",
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert data["error"]["code"] == "INSUFFICIENT_FUNDS"
        assert (
            data["error"]["message"]
            == "Account has insufficient balance to complete the requested debit."
        )
        assert data["error"]["details"] == []
    finally:
        app.dependency_overrides.pop(get_ledger_service, None)


@pytest.mark.asyncio
async def test_add_transaction_duplicate_reference_returns_409(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """Duplicate reference ID returns HTTP 409 with DUPLICATE_REFERENCE and empty details."""
    account_id = uuid.uuid4()
    mock_service = AsyncMock()
    mock_service.add_transaction.side_effect = DuplicateReferenceError()
    app.dependency_overrides[get_ledger_service] = lambda: mock_service

    try:
        response = await async_client.post(
            f"/api/v1/accounts/{account_id}/transactions",
            json={
                "type": "credit",
                "amount": "10.00",
                "description": "Duplicate payment",
                "reference_id": "REF-ALREADY-USED",
            },
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "DUPLICATE_REFERENCE"
        assert (
            data["error"]["message"]
            == "A transaction with this reference_id already exists for this account."
        )
        assert data["error"]["details"] == []
    finally:
        app.dependency_overrides.pop(get_ledger_service, None)


@pytest.mark.asyncio
async def test_add_transaction_missing_account_returns_404(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """Transaction targeting a non-existent account returns HTTP 404."""
    account_id = uuid.uuid4()
    mock_service = AsyncMock()
    mock_service.add_transaction.side_effect = AccountNotFoundError()
    app.dependency_overrides[get_ledger_service] = lambda: mock_service

    try:
        response = await async_client.post(
            f"/api/v1/accounts/{account_id}/transactions",
            json={
                "type": "credit",
                "amount": "10.00",
                "description": "Deposit",
                "reference_id": "REF-001",
            },
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "ACCOUNT_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_ledger_service, None)


@pytest.mark.asyncio
async def test_add_transaction_invalid_uuid_returns_422(
    async_client: AsyncClient,
) -> None:
    """Invalid account UUID in path returns HTTP 422 VALIDATION_ERROR."""
    response = await async_client.post(
        "/api/v1/accounts/not-a-valid-uuid/transactions",
        json={
            "type": "credit",
            "amount": "10.00",
            "description": "Deposit",
            "reference_id": "REF-001",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_add_transaction_rejects_non_positive_amounts(
    async_client: AsyncClient,
) -> None:
    """Non-positive amounts (0 and negative) are rejected with HTTP 422."""
    account_id = uuid.uuid4()

    # Amount 0
    res0 = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "0.00",
            "description": "Zero test",
            "reference_id": "REF-ZERO",
        },
    )
    assert res0.status_code == 422
    assert res0.json()["error"]["code"] == "VALIDATION_ERROR"

    # Negative amount
    res_neg = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "-5.00",
            "description": "Negative test",
            "reference_id": "REF-NEG",
        },
    )
    assert res_neg.status_code == 422
    assert res_neg.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_add_transaction_rejects_floats_strictly(
    async_client: AsyncClient,
) -> None:
    """Numeric floats and integers are strictly rejected under string contract."""
    account_id = uuid.uuid4()
    response = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": 15.50,
            "description": "Float attempt",
            "reference_id": "REF-FLOAT",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize("bool_val", [True, False])
async def test_add_transaction_rejects_boolean_amount(
    async_client: AsyncClient,
    bool_val: bool,
) -> None:
    """Boolean values for amount are strictly rejected with HTTP 422."""
    account_id = uuid.uuid4()
    response = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": bool_val,
            "description": "Bool attempt",
            "reference_id": f"REF-BOOL-{bool_val}",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_add_transaction_rejects_exceeding_max_bigint(
    async_client: AsyncClient,
) -> None:
    """Amounts exceeding PostgreSQL signed BIGINT max are rejected with HTTP 422."""
    account_id = uuid.uuid4()
    response = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "92233720368547758.08",
            "description": "Exceeding signed BIGINT",
            "reference_id": "REF-EXCEED-MAX",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_add_transaction_accepts_max_bigint(
    app: FastAPI,
    async_client: AsyncClient,
) -> None:
    """Valid positive signed BIGINT max passes validation."""
    account_id = uuid.uuid4()
    tx_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_service = AsyncMock()
    mock_service.add_transaction.return_value = TransactionResponse(
        id=tx_id,
        account_id=account_id,
        type="credit",
        amount="92233720368547758.07",
        balance_after="92233720368547758.07",
        currency="INR",
        description="Max BIGINT deposit",
        reference_id="REF-MAX-BIGINT",
        transfer_id=None,
        created_at=now,
    )
    app.dependency_overrides[get_ledger_service] = lambda: mock_service

    response = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "92233720368547758.07",
            "description": "Max BIGINT deposit",
            "reference_id": "REF-MAX-BIGINT",
        },
    )
    assert response.status_code == 201
    assert response.json()["amount"] == "92233720368547758.07"
    assert response.json()["currency"] == "INR"


@pytest.mark.asyncio
async def test_add_transaction_rejects_invalid_type(
    async_client: AsyncClient,
) -> None:
    """Type other than 'credit' or 'debit' is rejected with HTTP 422."""
    account_id = uuid.uuid4()
    response = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "refund",
            "amount": "10.00",
            "description": "Invalid type",
            "reference_id": "REF-INVALID-TYPE",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_add_transaction_rejects_whitespace_fields(
    async_client: AsyncClient,
) -> None:
    """Whitespace-only description, reference_id, or amount is rejected with HTTP 422."""
    account_id = uuid.uuid4()

    res_desc = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "10.00",
            "description": "    ",
            "reference_id": "REF-001",
        },
    )
    assert res_desc.status_code == 422
    assert res_desc.json()["error"]["code"] == "VALIDATION_ERROR"

    res_ref = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "10.00",
            "description": "Valid description",
            "reference_id": "    ",
        },
    )
    assert res_ref.status_code == 422
    assert res_ref.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_add_transaction_rejects_extra_fields(
    async_client: AsyncClient,
) -> None:
    """Client cannot supply balance_after, amount_minor, id, or arbitrary fields."""
    account_id = uuid.uuid4()
    response = await async_client.post(
        f"/api/v1/accounts/{account_id}/transactions",
        json={
            "type": "credit",
            "amount": "10.00",
            "description": "Valid description",
            "reference_id": "REF-001",
            "balance_after": "9999.99",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_transactions_immutability_no_update_or_delete_routes(
    async_client: AsyncClient,
) -> None:
    """API exposes no PUT, PATCH, or DELETE routes for transaction records."""
    account_id = uuid.uuid4()
    tx_id = uuid.uuid4()

    # Attempt PUT
    res_put = await async_client.put(
        f"/api/v1/accounts/{account_id}/transactions/{tx_id}",
        json={"description": "Tampered description"},
    )
    assert res_put.status_code in (404, 405)

    # Attempt PATCH
    res_patch = await async_client.patch(
        f"/api/v1/accounts/{account_id}/transactions/{tx_id}",
        json={"amount": "10.00"},
    )
    assert res_patch.status_code in (404, 405)

    # Attempt DELETE
    res_delete = await async_client.delete(f"/api/v1/accounts/{account_id}/transactions/{tx_id}")
    assert res_delete.status_code in (404, 405)


@pytest.mark.asyncio
async def test_get_transaction_history_endpoint_success(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """GET /api/v1/accounts/{id}/transactions returns 200 with items in rupees and pagination."""
    account_id = uuid.uuid4()
    tx_id = uuid.uuid4()
    now = datetime.now(UTC)

    mock_service = AsyncMock()
    mock_service.get_history.return_value = TransactionHistoryResponse(
        items=[
            TransactionResponse(
                id=tx_id,
                account_id=account_id,
                type="credit",
                amount="50.00",
                balance_after="50.00",
                currency="INR",
                description="Initial credit",
                reference_id="REF-HIST-01",
                created_at=now,
            )
        ],
        pagination=PaginationMetadata(
            current_page=1,
            page_size=20,
            total_records=1,
            total_pages=1,
        ),
    )
    app.dependency_overrides[get_ledger_service] = lambda: mock_service

    try:
        response = await async_client.get(
            f"/api/v1/accounts/{account_id}/transactions?type=credit&page=1&limit=20"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(tx_id)
        assert data["items"][0]["type"] == "credit"
        assert data["items"][0]["amount"] == "50.00"
        assert data["items"][0]["balance_after"] == "50.00"
        assert data["items"][0]["currency"] == "INR"
        assert "amount_minor" not in data["items"][0]
        assert "balance_after_minor" not in data["items"][0]
        assert data["pagination"]["current_page"] == 1
        assert data["pagination"]["page_size"] == 20
        assert data["pagination"]["total_records"] == 1
        assert data["pagination"]["total_pages"] == 1
        assert "X-Request-ID" in response.headers
    finally:
        app.dependency_overrides.pop(get_ledger_service, None)


@pytest.mark.asyncio
async def test_get_transaction_history_missing_account_returns_404(
    app: FastAPI, async_client: AsyncClient
) -> None:
    """GET /api/v1/accounts/{id}/transactions on non-existent account returns 404 ACCOUNT_NOT_FOUND."""
    account_id = uuid.uuid4()
    mock_service = AsyncMock()
    mock_service.get_history.side_effect = AccountNotFoundError()
    app.dependency_overrides[get_ledger_service] = lambda: mock_service

    try:
        response = await async_client.get(f"/api/v1/accounts/{account_id}/transactions")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "ACCOUNT_NOT_FOUND"
        assert data["error"]["message"] == "The requested account does not exist."
    finally:
        app.dependency_overrides.pop(get_ledger_service, None)


@pytest.mark.asyncio
async def test_get_transaction_history_invalid_query_params_returns_422(
    async_client: AsyncClient,
) -> None:
    """GET /api/v1/accounts/{id}/transactions rejects invalid type, page < 1, and limit > 100."""
    account_id = uuid.uuid4()

    # Invalid type
    res_type = await async_client.get(
        f"/api/v1/accounts/{account_id}/transactions?type=invalid_type"
    )
    assert res_type.status_code == 422
    assert res_type.json()["error"]["code"] == "VALIDATION_ERROR"

    # page < 1
    res_page_zero = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=0")
    assert res_page_zero.status_code == 422
    assert res_page_zero.json()["error"]["code"] == "VALIDATION_ERROR"

    res_page_neg = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?page=-5")
    assert res_page_neg.status_code == 422
    assert res_page_neg.json()["error"]["code"] == "VALIDATION_ERROR"

    # limit < 1
    res_limit_zero = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?limit=0")
    assert res_limit_zero.status_code == 422
    assert res_limit_zero.json()["error"]["code"] == "VALIDATION_ERROR"

    # limit > 100
    res_limit_101 = await async_client.get(f"/api/v1/accounts/{account_id}/transactions?limit=101")
    assert res_limit_101.status_code == 422
    assert res_limit_101.json()["error"]["code"] == "VALIDATION_ERROR"

    # invalid account UUID in path
    res_bad_uuid = await async_client.get("/api/v1/accounts/not-a-uuid/transactions")
    assert res_bad_uuid.status_code == 422
    assert res_bad_uuid.json()["error"]["code"] == "VALIDATION_ERROR"
