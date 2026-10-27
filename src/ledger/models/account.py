"""Account entity model.

Represents an account holder's balance and currency information with database-level
non-negative balance guarantees.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ledger.models.base import Base

if TYPE_CHECKING:
    from ledger.models.transaction import Transaction


class Account(Base):
    """Account model enforcing integer minor-unit balance and non-negative constraints."""

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("balance_minor >= 0", name="balance_non_negative"),
        CheckConstraint("length(trim(name)) > 0", name="name_non_empty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    balance_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="INR",
        server_default=text("'INR'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
    )

    # Relationships - strictly preserves immutable transactions; no cascading deletes
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="account",
        passive_deletes=True,
    )
