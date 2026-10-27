"""Pydantic v2 schemas for Transaction request, response, and pagination models."""

import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

TransactionType = Literal["credit", "debit"]

MAX_BIGINT: int = 9_223_372_036_854_775_807
AMOUNT_REGEX = re.compile(r"^\d+(\.\d{1,2})?$")


def rupees_to_paise(v: str) -> int:
    """Converts a decimal string representation of INR rupees to integer paise.

    Enforces exact 2-decimal-place strings, rejects non-finite, zero, negative,
    or out-of-range values. Never uses floating-point arithmetic.
    """
    if not isinstance(v, str):
        raise ValueError("Amount must be a decimal string.")
    if not AMOUNT_REGEX.match(v):
        raise ValueError(
            "Amount must be a valid positive decimal string with at most 2 decimal places."
        )
    try:
        d = Decimal(v)
    except InvalidOperation:
        raise ValueError("Invalid monetary decimal format.") from None
    if not d.is_finite():
        raise ValueError("Amount must be a finite decimal.")
    if d <= 0:
        raise ValueError("Amount must be strictly positive.")
    paise = int(d * 100)
    if paise <= 0:
        raise ValueError("Amount must be strictly positive.")
    if paise > MAX_BIGINT:
        raise ValueError("Amount exceeds maximum supported monetary value.")
    return paise


def paise_to_rupees(paise: int) -> str:
    """Formats integer paise into a fixed two-decimal-place INR rupee string.

    Uses exact integer division and modulo to avoid binary float artifacts.
    """
    sign = "-" if paise < 0 else ""
    abs_paise = abs(paise)
    return f"{sign}{abs_paise // 100}.{abs_paise % 100:02d}"


class TransactionCreateRequest(BaseModel):
    """Request schema for creating a credit or debit transaction against an account."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    type: TransactionType = Field(
        description="Transaction direction: strictly 'credit' or 'debit'.",
        examples=["credit"],
    )
    amount: str = Field(
        strict=True,
        description="Transaction amount in INR rupees as a decimal string (e.g. '450.30'). Must be strictly positive with at most two decimal places.",
        examples=["450.30"],
    )
    description: str = Field(
        min_length=1,
        max_length=1000,
        description="Human-readable transaction narrative.",
        examples=["Consulting invoice settlement #1024"],
    )
    reference_id: str = Field(
        min_length=1,
        max_length=128,
        description="Client-supplied reference identifier, unique within this account.",
        examples=["REF-INV-2026-001"],
    )

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, v: Any) -> str:
        """Enforces that amount is a decimal string in INR rupees with at most 2 decimal places."""
        if not isinstance(v, str):
            raise ValueError("Amount must be a decimal string.")
        if "\x00" in v:
            raise ValueError("Amount cannot contain null bytes.")
        if v != v.strip() or not v:
            raise ValueError("Amount cannot be empty or contain whitespace.")
        rupees_to_paise(v)
        return v

    @property
    def amount_minor(self) -> int:
        """Internal integer minor units (paise) derived from amount."""
        return rupees_to_paise(self.amount)

    @field_validator("description")
    @classmethod
    def validate_description_not_whitespace(cls, v: str) -> str:
        """Enforces that description contains non-whitespace content and no null bytes."""
        if "\x00" in v:
            raise ValueError("Description cannot contain null bytes.")
        stripped = v.strip()
        if not stripped:
            raise ValueError("Description cannot be empty or whitespace-only.")
        return stripped

    @field_validator("reference_id")
    @classmethod
    def validate_reference_not_whitespace(cls, v: str) -> str:
        """Enforces that reference_id contains non-whitespace content and no null bytes."""
        if "\x00" in v:
            raise ValueError("Reference ID cannot contain null bytes.")
        stripped = v.strip()
        if not stripped:
            raise ValueError("Reference ID cannot be empty or whitespace-only.")
        return stripped


class TransactionResponse(BaseModel):
    """Response schema returned after successful transaction recording."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(
        description="Unique transaction identifier (RFC 4122 UUIDv4).",
        examples=["31b26c04-f58c-4a37-b4d6-c9586144e512"],
    )
    account_id: uuid.UUID = Field(
        description="Identifier of the associated account.",
        examples=["7b045112-9c3a-442f-8da2-9b2f6efba221"],
    )
    type: TransactionType = Field(
        description="Transaction direction: 'credit' or 'debit'.",
        examples=["credit"],
    )
    amount: str = Field(
        validation_alias=AliasChoices("amount", "amount_minor"),
        description="Transaction amount in INR rupees formatted to 2 decimal places.",
        examples=["450.30"],
    )
    balance_after: str = Field(
        validation_alias=AliasChoices("balance_after", "balance_after_minor"),
        description="Snapshot of the account balance immediately following this transaction in INR rupees.",
        examples=["5450.30"],
    )
    currency: str = Field(
        default="INR",
        description="ISO 4217 3-letter currency code (strictly INR).",
        examples=["INR"],
    )
    description: str = Field(
        description="Human-readable description.",
        examples=["Consulting invoice settlement #1024"],
    )
    reference_id: str = Field(
        description="Unique reference identifier within this account.",
        examples=["REF-INV-2026-001"],
    )
    transfer_id: uuid.UUID | None = Field(
        default=None,
        description="Optional transfer identifier if created as part of a cross-account transfer.",
        examples=[None],
    )
    created_at: datetime = Field(
        description="Timestamp of transaction creation in ISO 8601 UTC format.",
    )

    @field_validator("amount", "balance_after", mode="before")
    @classmethod
    def format_monetary_fields(cls, v: Any) -> str:
        """Formats integer minor units to fixed 2-decimal rupee strings."""
        if isinstance(v, int):
            return paise_to_rupees(v)
        if isinstance(v, str):
            return v
        return str(v)


class PaginationParams(BaseModel):
    """Query parameters for paginated queries adhering to bounds in Section 2.2."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(
        default=1,
        ge=1,
        description="1-based page number.",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of records per page (clamped between 1 and 100).",
    )


class PaginationMetadata(BaseModel):
    """Envelope metadata describing pagination state adhering to Section 2.3."""

    current_page: int = Field(
        description="Current 1-based page index.",
        examples=[1],
    )
    page_size: int = Field(
        description="Number of items per page.",
        examples=[20],
    )
    total_records: int = Field(
        description="Total number of matching records in the ledger.",
        examples=[142],
    )
    total_pages: int = Field(
        description="Total number of available pages.",
        examples=[8],
    )


class TransactionHistoryResponse(BaseModel):
    """Response schema for paginated transaction history adhering to Section 3.4."""

    items: list[TransactionResponse] = Field(
        description="Ordered list of transactions in latest-first order.",
    )
    pagination: PaginationMetadata = Field(
        description="Pagination metadata envelope.",
    )
