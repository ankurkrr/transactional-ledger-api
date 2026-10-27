"""Revoke public schema CREATE privilege from runtime role ledger_app.

Revision ID: a17f2231b8d0
Revises: 6f904906c046
Create Date: 2026-10-27 19:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a17f2231b8d0"
down_revision: str | None = "6f904906c046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Revoke CREATE on public schema from runtime role ledger_app if present."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ledger_app') THEN
                REVOKE CREATE ON SCHEMA public FROM ledger_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    """Restore CREATE on public schema for runtime role ledger_app if present."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ledger_app') THEN
                GRANT CREATE ON SCHEMA public TO ledger_app;
            END IF;
        END
        $$;
        """
    )
