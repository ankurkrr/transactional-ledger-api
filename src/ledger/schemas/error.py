"""Pydantic v2 schemas for standardized error envelopes."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Granular error detail indicating field-specific validation failures."""

    field: str | None = Field(
        default=None,
        description="Name of the invalid request field, if applicable.",
    )
    message: str = Field(
        description="Human-readable explanation of the validation failure.",
    )


class ErrorBody(BaseModel):
    """Payload representing a structured error response."""

    code: str = Field(
        description="Machine-readable stable error code in SCREAMING_SNAKE_CASE.",
        examples=["INSUFFICIENT_FUNDS", "DUPLICATE_REFERENCE", "VALIDATION_ERROR"],
    )
    message: str = Field(
        description="Sanitized, human-readable description of the error.",
    )
    details: list[Any] = Field(
        default_factory=list,
        description="List of specific error details. Kept empty for security-sensitive errors.",
    )
    request_id: str = Field(
        description="Unique request correlation identifier for diagnostic tracing.",
        examples=["req_01j7bca3e8f8v9a0b1c2d3e4f5"],
    )
    timestamp: datetime = Field(
        description="Timestamp when the error occurred in ISO 8601 UTC format.",
    )


class ErrorResponse(BaseModel):
    """Unified error response envelope adhering to API contract Section 2.1."""

    error: ErrorBody
