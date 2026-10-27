"""Database engine and session management using SQLAlchemy 2.x and asyncpg."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ledger.core.config import get_settings


def create_engine_and_session_factory(
    database_url: str | None = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Initializes and returns an async engine and session factory."""
    settings = get_settings()
    url = database_url or settings.DATABASE_URL
    engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return engine, session_factory


# Default module-level singletons configured from environment settings
engine, async_session_factory = create_engine_and_session_factory()


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Dependency for providing an asynchronous database session."""
    async with async_session_factory() as session:
        yield session
