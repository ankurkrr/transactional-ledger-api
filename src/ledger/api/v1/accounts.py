"""API endpoints for ledger account creation and balance inquiry."""

import uuid

from fastapi import APIRouter, Depends, Path, status

from ledger.api.dependencies import get_account_service
from ledger.schemas.account import (
    AccountCreateRequest,
    AccountResponse,
    BalanceResponse,
)
from ledger.schemas.error import ErrorResponse
from ledger.services.account_service import AccountService

router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=AccountResponse,
    responses={
        status.HTTP_201_CREATED: {
            "model": AccountResponse,
            "description": "Account created successfully.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Request body failed validation.",
        },
    },
    summary="Create account",
    description="Creates a new ledger account initialized with a zero balance.",
)
async def create_account(
    data: AccountCreateRequest,
    service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """Create a new account with a system-managed zero initial balance."""
    return await service.create_account(data)


@router.get(
    "/{account_id}/balance",
    status_code=status.HTTP_200_OK,
    response_model=BalanceResponse,
    responses={
        status.HTTP_200_OK: {
            "model": BalanceResponse,
            "description": "Current account balance retrieved successfully.",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "The requested account does not exist.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Invalid account UUID format.",
        },
    },
    summary="Get account balance",
    description="Retrieves the current reconciled balance for a specified account.",
)
async def get_account_balance(
    account_id: uuid.UUID = Path(
        ...,
        description="Unique account identifier (RFC 4122 UUIDv4).",
    ),
    service: AccountService = Depends(get_account_service),
) -> BalanceResponse:
    """Fetch current balance for the given account ID."""
    return await service.get_balance(account_id)


@router.get(
    "/{account_id}",
    status_code=status.HTTP_200_OK,
    response_model=AccountResponse,
    responses={
        status.HTTP_200_OK: {
            "model": AccountResponse,
            "description": "Account details and balance retrieved successfully.",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "The requested account does not exist.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Invalid account UUID format.",
        },
    },
    summary="Get account details",
    description="Retrieves an existing account and its current balance by its unique identifier.",
)
async def get_account(
    account_id: uuid.UUID = Path(
        ...,
        description="Unique account identifier (RFC 4122 UUIDv4).",
    ),
    service: AccountService = Depends(get_account_service),
) -> AccountResponse:
    """Fetch account details and balance for the given account ID."""
    return await service.get_account_by_id(account_id)
