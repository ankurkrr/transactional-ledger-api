"""Pytest fixtures and configuration."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ledger.api.dependencies import get_session
from ledger.core.config import Settings, get_settings
from ledger.main import create_application
from ledger.models import Base


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing isolated test settings."""
    app_settings = get_settings()
    test_db_url = app_settings.TEST_DATABASE_URL
    if not test_db_url:
        if app_settings.DATABASE_URL.endswith("/ledger"):
            test_db_url = app_settings.DATABASE_URL[:-7] + "/ledger_test"
        else:
            test_db_url = app_settings.DATABASE_URL

    return Settings(
        ENVIRONMENT="test",
        ALLOW_UNAUTHENTICATED_ACCESS=True,
        DATABASE_URL=test_db_url,
        SECRET_KEY="test_secret_key_minimum_32_characters_long_isolated",
        LOG_LEVEL="DEBUG",
    )


@pytest.fixture
def app(test_settings: Settings) -> FastAPI:
    """Creates a fresh test application instance."""
    app_instance = create_application()
    app_instance.dependency_overrides[get_settings] = lambda: test_settings
    return app_instance


@pytest_asyncio.fixture()
async def async_client(app: FastAPI, test_settings: Settings) -> AsyncGenerator[AsyncClient]:
    """Provides an asynchronous HTTP client configured against the FastAPI test app.

    Ensures database session dependency explicitly targets the isolated test database
    and ensures schema tables exist.
    """
    test_engine = create_async_engine(
        test_settings.DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async def _test_get_session() -> AsyncGenerator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _test_get_session

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    await test_engine.dispose()
