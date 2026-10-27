"""Transactional Ledger API — Main Application Entry Point.

Bootstrap phase entry point initializing FastAPI, validating configuration
safety gates, registering core infrastructure routes, and configuring
centralized error handling.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from ledger.api.error_handlers import register_error_handlers
from ledger.api.v1.accounts import router as accounts_router
from ledger.api.v1.health import router as health_router
from ledger.api.v1.transactions import router as transactions_router
from ledger.api.v1.transfers import router as transfers_router
from ledger.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan context.

    Validates configuration safety gates at startup before accepting traffic.
    """
    settings = get_settings()
    # Explicitly invoke safety check at boot
    settings.check_production_safety()
    yield


def create_application() -> FastAPI:
    """Application factory initializing FastAPI with settings and routes."""
    settings = get_settings()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description="Transactional Ledger API engineered for financial correctness and concurrency.",
        lifespan=lifespan,
        docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
        redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    )

    # Middleware: Request correlation identifier
    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming_id = request.headers.get("X-Request-ID")
        request_id = (
            incoming_id.strip()
            if incoming_id and incoming_id.strip()
            else f"req_{uuid.uuid4().hex}"
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # Register centralized exception handlers
    register_error_handlers(app)

    # Register infrastructure routes
    app.include_router(health_router, prefix=settings.API_V1_PREFIX)
    app.include_router(health_router)  # Direct /health for container probes

    # Register financial domain routers
    app.include_router(accounts_router, prefix=settings.API_V1_PREFIX)
    app.include_router(transactions_router, prefix=settings.API_V1_PREFIX)
    app.include_router(transfers_router, prefix=settings.API_V1_PREFIX)

    return app


app = create_application()
