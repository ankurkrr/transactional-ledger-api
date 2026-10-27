"""Unit tests for centralized error and exception handlers."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from ledger.api.error_handlers import register_error_handlers
from ledger.core.exceptions import AccountNotFoundError, InsufficientFundsError, LedgerError


@pytest.fixture
def test_error_app() -> FastAPI:
    """Creates a minimal FastAPI app with registered error handlers for exception testing."""
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/raise-domain-404")
    async def raise_domain_404() -> None:
        raise AccountNotFoundError()

    @app.get("/raise-domain-422")
    async def raise_domain_422() -> None:
        raise InsufficientFundsError()

    @app.get("/raise-custom-ledger-error")
    async def raise_custom_ledger_error() -> None:
        class CustomServerLedgerError(LedgerError):
            code = "DATABASE_UNAVAILABLE"
            http_status_code = 503
            message = "Service temporarily unavailable."

        raise CustomServerLedgerError()

    @app.get("/raise-starlette-404")
    async def raise_starlette_404() -> None:
        raise StarletteHTTPException(status_code=404, detail="Resource not found")

    @app.get("/raise-starlette-405")
    async def raise_starlette_405() -> None:
        raise StarletteHTTPException(status_code=405, detail="Method not allowed")

    @app.get("/raise-unhandled-500")
    async def raise_unhandled_500() -> None:
        raise ZeroDivisionError("division by zero sensitive internal calculation")

    return app


@pytest.mark.asyncio
async def test_ledger_domain_exception_handled(test_error_app: FastAPI) -> None:
    """Domain exceptions map to configured status code, code token, and unified envelope."""
    transport = ASGITransport(app=test_error_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 404
        res404 = await client.get("/raise-domain-404")
        assert res404.status_code == 404
        data404 = res404.json()["error"]
        assert data404["code"] == "ACCOUNT_NOT_FOUND"
        assert data404["message"] == "The requested account does not exist."
        assert data404["details"] == []
        assert "request_id" in data404
        assert "timestamp" in data404

        # 422
        res422 = await client.get("/raise-domain-422")
        assert res422.status_code == 422
        data422 = res422.json()["error"]
        assert data422["code"] == "INSUFFICIENT_FUNDS"
        assert (
            data422["message"]
            == "Account has insufficient balance to complete the requested debit."
        )

        # 503
        res503 = await client.get("/raise-custom-ledger-error")
        assert res503.status_code == 503
        data503 = res503.json()["error"]
        assert data503["code"] == "DATABASE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_starlette_http_exceptions_handled(test_error_app: FastAPI) -> None:
    """Standard HTTP exceptions return unified error envelope."""
    transport = ASGITransport(app=test_error_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res404 = await client.get("/raise-starlette-404")
        assert res404.status_code == 404
        assert res404.json()["error"]["code"] == "NOT_FOUND"

        res405 = await client.get("/raise-starlette-405")
        assert res405.status_code == 405
        assert res405.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_unhandled_server_exception_does_not_leak_internals(
    test_error_app: FastAPI,
) -> None:
    """Unhandled exceptions return sanitized 500 error envelope without stack traces."""
    transport = ASGITransport(app=test_error_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res500 = await client.get("/raise-unhandled-500")
        assert res500.status_code == 500
        data500 = res500.json()["error"]
        assert data500["code"] == "INTERNAL_SERVER_ERROR"
        assert (
            data500["message"]
            == "An unexpected error occurred. Please contact support with request_id."
        )
        assert data500["details"] == []
        assert "division by zero" not in str(res500.content)
        assert "ZeroDivisionError" not in str(res500.content)
