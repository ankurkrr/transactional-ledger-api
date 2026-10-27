"""Unit tests for SQLAlchemy 2.x declarative models and metadata schema."""

from sqlalchemy import BigInteger, CheckConstraint, Index, UniqueConstraint

from ledger.models import Base


def test_tables_registered_in_metadata() -> None:
    """Verifies that all required financial tables are registered in Base metadata."""
    assert "accounts" in Base.metadata.tables
    assert "transactions" in Base.metadata.tables
    assert "transfers" in Base.metadata.tables


def test_accounts_table_definition() -> None:
    """Verifies table columns, types, defaults, and constraints for Account."""
    table = Base.metadata.tables["accounts"]

    assert table.c.id.primary_key is True
    assert table.c.id.server_default is not None
    assert isinstance(table.c.balance_minor.type, BigInteger)
    assert table.c.balance_minor.nullable is False
    assert table.c.currency.type.length == 3  # type: ignore[reportAttributeAccessIssue]

    # Verify check constraints
    check_constraints = {
        c.name: str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)
    }
    assert "ck_accounts_balance_non_negative" in check_constraints
    assert "balance_minor >= 0" in check_constraints["ck_accounts_balance_non_negative"]
    assert "ck_accounts_name_non_empty" in check_constraints
    assert "length(trim(name)) > 0" in check_constraints["ck_accounts_name_non_empty"]


def test_transactions_table_definition() -> None:
    """Verifies table columns, types, foreign keys, unique constraints, and indexes for Transaction."""
    table = Base.metadata.tables["transactions"]

    assert table.c.id.primary_key is True
    assert table.c.id.server_default is not None
    assert isinstance(table.c.amount_minor.type, BigInteger)
    assert isinstance(table.c.balance_after_minor.type, BigInteger)
    assert table.c.amount_minor.nullable is False

    # Foreign keys and ON DELETE RESTRICT
    fk_by_col = {fk.parent.name: fk for fk in table.foreign_keys}
    assert "account_id" in fk_by_col
    assert fk_by_col["account_id"].target_fullname == "accounts.id"
    assert fk_by_col["account_id"].ondelete == "RESTRICT"

    assert "transfer_id" in fk_by_col
    assert fk_by_col["transfer_id"].target_fullname == "transfers.id"
    assert fk_by_col["transfer_id"].ondelete == "RESTRICT"

    # Check constraints
    check_constraints = {
        c.name: str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)
    }
    assert "ck_transactions_type" in check_constraints
    assert "type IN ('credit', 'debit')" in check_constraints["ck_transactions_type"]
    assert "ck_transactions_amount_positive" in check_constraints
    assert "amount_minor > 0" in check_constraints["ck_transactions_amount_positive"]

    # Composite Unique Constraint (account_id, reference_id)
    unique_constraints = {
        c.name: [col.name for col in c.columns]
        for c in table.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert "uq_account_reference" in unique_constraints
    assert unique_constraints["uq_account_reference"] == ["account_id", "reference_id"]

    # Indexes
    index_names = {idx.name for idx in table.indexes if isinstance(idx, Index)}
    assert "idx_transactions_history" in index_names
    assert "idx_transactions_type_history" in index_names


def test_transfers_table_definition() -> None:
    """Verifies table columns, types, foreign keys, and constraints for Transfer."""
    table = Base.metadata.tables["transfers"]

    assert table.c.id.primary_key is True
    assert table.c.id.server_default is not None
    assert isinstance(table.c.amount_minor.type, BigInteger)

    # Foreign keys
    fk_targets = {fk.parent.name: (fk.target_fullname, fk.ondelete) for fk in table.foreign_keys}
    assert fk_targets["source_account_id"] == ("accounts.id", "RESTRICT")
    assert fk_targets["destination_account_id"] == ("accounts.id", "RESTRICT")

    # Check constraints
    check_constraints = {
        c.name: str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)
    }
    assert "ck_transfers_amount_positive" in check_constraints
    assert "amount_minor > 0" in check_constraints["ck_transfers_amount_positive"]
    assert "ck_transfers_distinct_accounts" in check_constraints
    assert (
        "source_account_id <> destination_account_id"
        in check_constraints["ck_transfers_distinct_accounts"]
    )

    # Unique constraint on reference_id
    unique_constraints = {
        c.name: [col.name for col in c.columns]
        for c in table.constraints
        if isinstance(c, UniqueConstraint)
    }
    assert "uq_transfers_reference" in unique_constraints
    assert unique_constraints["uq_transfers_reference"] == ["reference_id"]
