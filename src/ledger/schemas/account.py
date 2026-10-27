"""Pydantic v2 schemas for Account request and response models."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from ledger.schemas.transaction import paise_to_rupees


class AccountCreateRequest(BaseModel):
    """Request schema for creating a new ledger account.

    Initial balance is strictly system-managed (0.00 INR). Clients are prevented
    from specifying initial balances, custom identifiers, or timestamps.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Full name of the account holder.",
        examples=["Jane Doe"],
    )

    @field_validator("name")
    @classmethod
    def validate_name_not_whitespace(cls, v: str) -> str:
        """Enforces that account name contains non-whitespace characters and no null bytes."""
        if "\x00" in v:
            raise ValueError("Account name cannot contain null bytes.")
        stripped = v.strip()
        if not stripped:
            raise ValueError("Account name cannot be empty or whitespace-only.")
        return stripped


class AccountResponse(BaseModel):
    """Response schema returned after successful account creation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(
        description="Unique account identifier (RFC 4122 UUIDv4).",
        examples=["7b045112-9c3a-442f-8da2-9b2f6efba221"],
    )
    name: str = Field(
        description="Account holder name.",
        examples=["Jane Doe"],
    )
    balance: str = Field(
        validation_alias=AliasChoices("balance", "balance_minor"),
        description="Reconciled account balance in INR rupees formatted to 2 decimal places.",
        examples=["0.00"],
    )
    currency: str = Field(
        default="INR",
        description="ISO 4217 3-letter currency code.",
        examples=["INR"],
    )
    created_at: datetime = Field(
        description="Timestamp of account creation in ISO 8601 UTC format.",
    )
    updated_at: datetime = Field(
        description="Timestamp of last balance modification in ISO 8601 UTC format.",
    )

    @field_validator("balance", mode="before")
    @classmethod
    def format_balance(cls, v: Any) -> str:
        """Formats integer minor units to fixed 2-decimal rupee strings."""
        if isinstance(v, int):
            return paise_to_rupees(v)
        if isinstance(v, str):
            return v
        return str(v)


class BalanceResponse(BaseModel):
    """Response schema for account balance inquiry."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    account_id: uuid.UUID = Field(
        validation_alias=AliasChoices("account_id", "id"),
        description="Unique account identifier (RFC 4122 UUIDv4).",
        examples=["7b045112-9c3a-442f-8da2-9b2f6efba221"],
    )
    balance: str = Field(
        validation_alias=AliasChoices("balance", "balance_minor"),
        description="Current reconciled account balance in INR rupees formatted to 2 decimal places.",
        examples=["150.00"],
    )
    currency: str = Field(
        default="INR",
        description="ISO 4217 3-letter currency code.",
        examples=["INR"],
    )
    updated_at: datetime = Field(
        description="Timestamp of last balance modification in ISO 8601 UTC format.",
    )

    @field_validator("balance", mode="before")
    @classmethod
    def format_balance(cls, v: Any) -> str:
        """Formats integer minor units to fixed 2-decimal rupee strings."""
        if isinstance(v, int):
            return paise_to_rupees(v)
        if isinstance(v, str):
            return v
        return str(v)
