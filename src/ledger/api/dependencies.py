"""FastAPI dependency injection providers.

Supplies database sessions, request correlation identifiers, and service instances
to API endpoints with clear boundaries and test mockability.
"""

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ledger.core.database import async_session_factory
from ledger.services.account_service import AccountService
from ledger.services.ledger_service import LedgerService
from ledger.services.transfer_service import TransferService


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Dependency providing an asynchronous database session.

    Session is managed within the request lifecycle and closed upon completion.
    """
    async with async_session_factory() as session:
        yield session


def get_request_id(request: Request) -> str:
    """Extracts client-supplied X-Request-ID or generates a traceable correlation identifier."""
    incoming_id = request.headers.get("X-Request-ID")
    if incoming_id and isinstance(incoming_id, str) and incoming_id.strip():
        return incoming_id.strip()

    state_id = getattr(request.state, "request_id", None)
    if state_id and isinstance(state_id, str) and state_id.strip():
        return state_id.strip()

    return f"req_{uuid.uuid4().hex}"


def get_account_service(
    session: AsyncSession = Depends(get_session),
) -> AccountService:
    """Dependency providing an AccountService instance bound to the request session."""
    return AccountService(session)


def get_ledger_service(
    session: AsyncSession = Depends(get_session),
) -> LedgerService:
    """Dependency providing a LedgerService instance bound to the request session."""
    return LedgerService(session)


def get_transfer_service(
    session: AsyncSession = Depends(get_session),
) -> TransferService:
    """Dependency providing a TransferService instance bound to the request session."""
    return TransferService(session)
