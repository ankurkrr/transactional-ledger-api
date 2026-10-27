"""API endpoints for ledger credit and debit transaction execution."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from ledger.api.dependencies import get_ledger_service
from ledger.schemas.error import ErrorResponse
from ledger.schemas.transaction import (
    TransactionCreateRequest,
    TransactionHistoryResponse,
    TransactionResponse,
    TransactionType,
)
from ledger.services.ledger_service import LedgerService

router = APIRouter(tags=["Transactions"])


@router.post(
    "/accounts/{account_id}/transactions",
    status_code=status.HTTP_201_CREATED,
    response_model=TransactionResponse,
    responses={
        status.HTTP_201_CREATED: {
            "model": TransactionResponse,
            "description": "Transaction recorded successfully.",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Target account does not exist.",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Duplicate reference ID for this account.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Validation failure or insufficient funds.",
        },
    },
    summary="Add transaction",
    description="Posts an atomic credit or debit transaction to an account.",
)
async def add_transaction(
    account_id: Annotated[
        uuid.UUID,
        Path(description="Unique account identifier (RFC 4122 UUIDv4)."),
    ],
    data: TransactionCreateRequest,
    service: Annotated[LedgerService, Depends(get_ledger_service)],
) -> TransactionResponse:
    """Execute an atomic credit or debit against the specified account."""
    return await service.add_transaction(account_id, data)


@router.get(
    "/accounts/{account_id}/transactions",
    status_code=status.HTTP_200_OK,
    response_model=TransactionHistoryResponse,
    responses={
        status.HTTP_200_OK: {
            "model": TransactionHistoryResponse,
            "description": "Paginated transaction history retrieved successfully.",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Target account does not exist.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Invalid query parameters or account identifier.",
        },
    },
    summary="Get transaction history",
    description="Retrieves the chronological transaction ledger for an account, with optional type filtering and pagination.",
)
async def get_transaction_history(
    account_id: Annotated[
        uuid.UUID,
        Path(description="Unique account identifier (RFC 4122 UUIDv4)."),
    ],
    service: Annotated[LedgerService, Depends(get_ledger_service)],
    type: Annotated[
        TransactionType | None,
        Query(description="Filter by transaction direction ('credit' or 'debit')."),
    ] = None,
    page: Annotated[
        int,
        Query(ge=1, description="1-based page number."),
    ] = 1,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Number of items per page (1 to 100)."),
    ] = 20,
) -> TransactionHistoryResponse:
    """Fetch paginated, newest-first transaction records for the given account."""
    return await service.get_history(
        account_id=account_id,
        type_filter=type,
        page=page,
        limit=limit,
    )
