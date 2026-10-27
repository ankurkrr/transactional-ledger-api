"""Service layer transaction boundary manager.

Enforces ADR-009: Explicit single database transaction boundary where the service layer
owns the database session, issues immediate rollbacks on failure, and re-raises exceptions
using bare raise to preserve tracebacks.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def transaction_scope(session: AsyncSession) -> AsyncGenerator[AsyncSession]:
    """Context manager governing an explicit, single database transaction boundary.

    Commits on success; rolls back immediately on any exception and re-raises
    using bare raise to preserve the original exception context.
    """
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
