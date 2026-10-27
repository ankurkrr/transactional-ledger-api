"""API endpoints for cross-account money transfers."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from ledger.api.dependencies import get_transfer_service
from ledger.schemas.error import ErrorResponse
from ledger.schemas.transfer import TransferCreateRequest, TransferResponse
from ledger.services.transfer_service import TransferService

router = APIRouter(tags=["Transfers"])


@router.post(
    "/transfers",
    status_code=status.HTTP_201_CREATED,
    response_model=TransferResponse,
    responses={
        status.HTTP_201_CREATED: {
            "model": TransferResponse,
            "description": "Transfer executed successfully.",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Either source or destination account does not exist.",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Transfer with this reference_id already exists.",
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Validation failure, same-account transfer, currency mismatch, or insufficient funds.",
        },
    },
    summary="Transfer money between accounts",
    description="Atomically transfers funds between two distinct accounts: debited from source and credited to destination.",
)
async def create_transfer(
    data: TransferCreateRequest,
    service: Annotated[TransferService, Depends(get_transfer_service)],
) -> TransferResponse:
    """Execute an atomic cross-account money transfer."""
    return await service.execute_transfer(data)
