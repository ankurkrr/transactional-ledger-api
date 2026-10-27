"""Centralized HTTP exception handlers for the Transactional Ledger API.

Maps domain exceptions (LedgerError), Pydantic validation errors, and unhandled
server exceptions into the unified, sanitized error envelope specified by
ADR-004 and docs/error-handling.md.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ledger.api.dependencies import get_request_id
from ledger.core.exceptions import LedgerError
from ledger.schemas.error import ErrorBody, ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


async def ledger_error_handler(request: Request, exc: LedgerError) -> JSONResponse:
    """Handles domain-specific exceptions, returning documented error codes and status."""
    request_id = get_request_id(request)

    if exc.http_status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error(
            "Domain error [%s] %s: %s",
            request_id,
            exc.code,
            exc.message,
            exc_info=True,
        )
    else:
        logger.info(
            "Client error [%s] %s: %s",
            request_id,
            exc.code,
            exc.message,
        )

    response_data = ErrorResponse(
        error=ErrorBody(
            code=exc.code,
            message=exc.message,
            details=[],
            request_id=request_id,
            timestamp=datetime.now(UTC),
        )
    )
    return JSONResponse(
        status_code=exc.http_status_code,
        content=response_data.model_dump(mode="json"),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handles Pydantic and FastAPI validation errors with sanitized field messages."""
    request_id = get_request_id(request)
    details: list[dict[str, Any]] = []

    for err in exc.errors():
        ctx_err = err.get("ctx", {}).get("error")
        if isinstance(ctx_err, LedgerError):
            return await ledger_error_handler(request, ctx_err)

        loc_parts = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path")]
        field = ".".join(loc_parts) if loc_parts else None
        msg = str(err.get("msg", "Validation error"))
        details.append(ErrorDetail(field=field, message=msg).model_dump(mode="json"))

    logger.info("Validation failure [%s]: %s", request_id, details)

    response_data = ErrorResponse(
        error=ErrorBody(
            code="VALIDATION_ERROR",
            message="Request body or query parameters failed validation.",
            details=details,
            request_id=request_id,
            timestamp=datetime.now(UTC),
        )
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=response_data.model_dump(mode="json"),
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handles standard Starlette HTTP exceptions (e.g. 404 route not found, 405)."""
    request_id = get_request_id(request)

    if exc.status_code == status.HTTP_404_NOT_FOUND:
        code = "NOT_FOUND"
    elif exc.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
        code = "METHOD_NOT_ALLOWED"
    else:
        code = f"HTTP_{exc.status_code}"

    message = str(exc.detail) if exc.detail else "HTTP error occurred."

    response_data = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=[],
            request_id=request_id,
            timestamp=datetime.now(UTC),
        )
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=response_data.model_dump(mode="json"),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unexpected errors; logs traceback and hides internal details."""
    request_id = get_request_id(request)
    logger.exception(
        "Unhandled internal server exception [%s]: %s",
        request_id,
        str(exc),
    )

    response_data = ErrorResponse(
        error=ErrorBody(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected error occurred. Please contact support with request_id.",
            details=[],
            request_id=request_id,
            timestamp=datetime.now(UTC),
        )
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=response_data.model_dump(mode="json"),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Registers unified error handlers on the FastAPI application instance."""
    app.add_exception_handler(LedgerError, ledger_error_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
    app.add_exception_handler(Exception, unhandled_exception_handler)
