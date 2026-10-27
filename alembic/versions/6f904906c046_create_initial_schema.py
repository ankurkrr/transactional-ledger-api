"""create_initial_schema

Revision ID: 6f904906c046
Revises:
Create Date: 2026-10-27 10:30:04.889141

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6f904906c046"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema to initial state."""
    # 1. Accounts Table
    op.create_table(
        "accounts",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("balance_minor", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default=sa.text("'INR'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("balance_minor >= 0", name="balance_non_negative"),
        sa.CheckConstraint("length(trim(name)) > 0", name="name_non_empty"),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
    )

    # 2. Transfers Table (Bonus Feature)
    op.create_table(
        "transfers",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source_account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("destination_account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("reference_id", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'completed'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("amount_minor > 0", name="amount_positive"),
        sa.CheckConstraint(
            "source_account_id <> destination_account_id",
            name="distinct_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["source_account_id"],
            ["accounts.id"],
            name="fk_transfers_source_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["destination_account_id"],
            ["accounts.id"],
            name="fk_transfers_destination_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transfers"),
        sa.UniqueConstraint("reference_id", name="uq_transfers_reference"),
    )

    # 3. Transactions Table (Immutable Financial Ledger)
    op.create_table(
        "transactions",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=10), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_minor", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reference_id", sa.String(length=128), nullable=False),
        sa.Column("transfer_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("type IN ('credit', 'debit')", name="type"),
        sa.CheckConstraint("amount_minor > 0", name="amount_positive"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_transactions_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["transfers.id"],
            name="fk_transactions_transfer_id_transfers",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
        sa.UniqueConstraint("account_id", "reference_id", name="uq_account_reference"),
    )

    # 4. Composite Indexes on Transactions
    op.create_index(
        "idx_transactions_history",
        "transactions",
        ["account_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "idx_transactions_type_history",
        "transactions",
        ["account_id", "type", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )

    # 5. Defense-in-depth immutability (ADR-006): Revoke UPDATE and DELETE on transactions from runtime role if present
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ledger_app') THEN
                REVOKE UPDATE, DELETE ON TABLE transactions FROM ledger_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """Downgrade schema to revert all tables and indexes."""
    op.drop_index("idx_transactions_type_history", table_name="transactions")
    op.drop_index("idx_transactions_history", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("transfers")
    op.drop_table("accounts")
