"""Pydantic v2 schemas for Bonus Transfer request and response models."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ledger.core.exceptions import TransferSameAccountError
from ledger.schemas.transaction import paise_to_rupees, rupees_to_paise


class TransferCreateRequest(BaseModel):
    """Request schema for initiating an atomic cross-account money transfer."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    source_account_id: uuid.UUID = Field(
        description="Identifier of the debited source account.",
        examples=["7b045112-9c3a-442f-8da2-9b2f6efba221"],
    )
    destination_account_id: uuid.UUID = Field(
        description="Identifier of the credited destination account.",
        examples=["9ca21f64-1111-4562-b3fc-2c963f66abbb"],
    )
    amount: str = Field(
        strict=True,
        description="Transfer amount in INR rupees as a decimal string (e.g. '450.30'). Must be strictly positive with at most two decimal places.",
        examples=["450.30"],
    )
    description: str = Field(
        min_length=1,
        max_length=1000,
        description="Narrative describing the transfer reason.",
        examples=["Peer-to-peer reimbursement"],
    )
    reference_id: str = Field(
        min_length=1,
        max_length=128,
        description="Global unique idempotency reference for the transfer.",
        examples=["XFER-2026-9901"],
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

    @model_validator(mode="after")
    def validate_distinct_accounts(self) -> "TransferCreateRequest":
        """Enforces that source and destination accounts are distinct."""
        if self.source_account_id == self.destination_account_id:
            raise TransferSameAccountError()
        return self


class TransferResponse(BaseModel):
    """Response schema returned after successful transfer completion."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    transfer_id: uuid.UUID = Field(
        validation_alias=AliasChoices("transfer_id", "id"),
        description="Unique transfer identifier (RFC 4122 UUIDv4).",
        examples=["55c41f64-4444-4562-b3fc-2c963f66a000"],
    )
    source_account_id: uuid.UUID = Field(
        description="Source account debited in this transfer.",
        examples=["7b045112-9c3a-442f-8da2-9b2f6efba221"],
    )
    destination_account_id: uuid.UUID = Field(
        description="Destination account credited in this transfer.",
        examples=["9ca21f64-1111-4562-b3fc-2c963f66abbb"],
    )
    amount: str = Field(
        validation_alias=AliasChoices("amount", "amount_minor"),
        description="Transferred amount in INR rupees formatted to 2 decimal places.",
        examples=["450.30"],
    )
    currency: str = Field(
        default="INR",
        description="ISO 4217 3-letter currency code (strictly INR).",
        examples=["INR"],
    )
    status: str = Field(
        default="completed",
        description="Transfer status.",
        examples=["completed"],
    )
    reference_id: str = Field(
        description="Global unique transfer reference identifier.",
        examples=["XFER-2026-9901"],
    )
    description: str = Field(
        description="Transfer narrative.",
        examples=["Peer-to-peer reimbursement"],
    )
    created_at: datetime = Field(
        description="Timestamp of transfer execution in ISO 8601 UTC format.",
    )

    @field_validator("amount", mode="before")
    @classmethod
    def format_amount(cls, v: Any) -> str:
        """Formats integer minor units to fixed 2-decimal rupee strings."""
        if isinstance(v, int):
            return paise_to_rupees(v)
        if isinstance(v, str):
            return v
        return str(v)
