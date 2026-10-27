"""Transaction entity model.

Represents an immutable, append-only financial ledger record.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ledger.models.base import Base

if TYPE_CHECKING:
    from ledger.models.account import Account
    from ledger.models.transfer import Transfer


class Transaction(Base):
    """Immutable ledger record representing a credit or debit operation."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("type IN ('credit', 'debit')", name="type"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        UniqueConstraint("account_id", "reference_id", name="uq_account_reference"),
        Index(
            "idx_transactions_history",
            "account_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "idx_transactions_type_history",
            "account_id",
            "type",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )
    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    balance_after_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    reference_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("transfers.id", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )

    # Relationships
    account: Mapped["Account"] = relationship(
        "Account",
        back_populates="transactions",
    )
    transfer: Mapped["Transfer | None"] = relationship(
        "Transfer",
        back_populates="transactions",
    )
