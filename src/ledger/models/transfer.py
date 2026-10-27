"""Transfer entity model (Bonus Feature).

Represents an atomic cross-account transfer record coordinating debits and credits
between two distinct accounts.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
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
    from ledger.models.transaction import Transaction


class Transfer(Base):
    """Transfer entity representing atomic money transfer between accounts."""

    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint(
            "source_account_id <> destination_account_id",
            name="distinct_accounts",
        ),
        UniqueConstraint("reference_id", name="uq_transfers_reference"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    source_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    reference_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="completed",
        server_default=text("'completed'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )

    # Relationships
    source_account: Mapped["Account"] = relationship(
        "Account",
        foreign_keys=[source_account_id],
    )
    destination_account: Mapped["Account"] = relationship(
        "Account",
        foreign_keys=[destination_account_id],
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="transfer",
        passive_deletes=True,
    )
