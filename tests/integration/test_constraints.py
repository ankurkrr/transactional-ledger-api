"""Database constraint and foreign key integration tests executed against PostgreSQL.

Validates that PostgreSQL database engine guarantees (CHECK constraints, UNIQUE constraints,
foreign keys, and ON DELETE RESTRICT) reject invalid operations directly at the storage level.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ledger.core.config import get_settings
from ledger.models import Account, Base, Transaction, Transfer


def _get_test_db_url() -> str:
    """Returns the isolated test database URL from settings or derived from DATABASE_URL."""
    settings = get_settings()
    if settings.TEST_DATABASE_URL:
        return settings.TEST_DATABASE_URL
    if settings.DATABASE_URL.endswith("/ledger"):
        return settings.DATABASE_URL[:-7] + "/ledger_test"
    return settings.DATABASE_URL


def _probe_postgres_reachable() -> bool:
    """Synchronously probes if PostgreSQL is reachable with application credentials."""

    async def _probe() -> bool:
        try:
            test_url = _get_test_db_url()
            engine = create_async_engine(test_url)
            async with engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
            await engine.dispose()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


POSTGRES_AVAILABLE = _probe_postgres_reachable()

pytestmark = pytest.mark.skipif(
    not POSTGRES_AVAILABLE,
    reason=(
        "Database integration tests blocked: PostgreSQL service not available "
        "(Docker Desktop or local PostgreSQL with configured credentials is not running)"
    ),
)


@pytest_asyncio.fixture()
async def pg_engine() -> AsyncGenerator[AsyncEngine]:
    """Function-scoped async engine matching the test event loop."""
    test_url = _get_test_db_url()
    engine = create_async_engine(test_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE TABLE transfers, transactions, accounts CASCADE"))
    await engine.dispose()


@pytest_asyncio.fixture()
async def db_session(pg_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Provides an isolated database session rolled back after each test."""
    session_factory = async_sessionmaker(
        bind=pg_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


async def test_pg_check_constraint_balance_non_negative(db_session: AsyncSession) -> None:
    """Verifies ck_accounts_balance_non_negative rejects negative balances."""
    invalid_account = Account(
        name="Invalid Overdraft Account",
        balance_minor=-500,
        currency="INR",
    )
    db_session.add(invalid_account)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_pg_check_constraint_name_non_empty(db_session: AsyncSession) -> None:
    """Verifies ck_accounts_name_non_empty rejects whitespace-only names."""
    invalid_account = Account(
        name="   ",
        balance_minor=0,
        currency="INR",
    )
    db_session.add(invalid_account)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_pg_check_constraint_amount_positive(db_session: AsyncSession) -> None:
    """Verifies ck_transactions_amount_positive rejects zero or negative amounts."""
    account = Account(name="Test Account", balance_minor=1000)
    db_session.add(account)
    await db_session.commit()
    account_id = account.id

    # Test 0 amount
    tx_zero = Transaction(
        account_id=account_id,
        type="credit",
        amount_minor=0,
        balance_after_minor=1000,
        description="Zero amount test",
        reference_id="REF-ZERO-1",
    )
    db_session.add(tx_zero)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Test negative amount
    tx_negative = Transaction(
        account_id=account_id,
        type="credit",
        amount_minor=-500,
        balance_after_minor=1000,
        description="Negative amount test",
        reference_id="REF-NEG-1",
    )
    db_session.add(tx_negative)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_pg_check_constraint_type(db_session: AsyncSession) -> None:
    """Verifies ck_transactions_type rejects invalid transaction types."""
    account = Account(name="Test Account", balance_minor=1000)
    db_session.add(account)
    await db_session.commit()

    invalid_tx = Transaction(
        account_id=account.id,
        type="refund",
        amount_minor=500,
        balance_after_minor=1500,
        description="Invalid type test",
        reference_id="REF-TYPE-1",
    )
    db_session.add(invalid_tx)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_pg_unique_reference_per_account(db_session: AsyncSession) -> None:
    """Verifies uq_account_reference rejects duplicate references for the same account."""
    account = Account(name="Test Account", balance_minor=2000)
    db_session.add(account)
    await db_session.commit()

    tx1 = Transaction(
        account_id=account.id,
        type="credit",
        amount_minor=500,
        balance_after_minor=2500,
        description="First credit",
        reference_id="REF-DUP-1",
    )
    db_session.add(tx1)
    await db_session.commit()

    tx2 = Transaction(
        account_id=account.id,
        type="credit",
        amount_minor=500,
        balance_after_minor=3000,
        description="Duplicate credit",
        reference_id="REF-DUP-1",
    )
    db_session.add(tx2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_pg_same_reference_different_accounts(db_session: AsyncSession) -> None:
    """Verifies that identical reference_id is permitted across different accounts."""
    account_a = Account(name="Account A", balance_minor=1000)
    account_b = Account(name="Account B", balance_minor=1000)
    db_session.add_all([account_a, account_b])
    await db_session.commit()

    tx_a = Transaction(
        account_id=account_a.id,
        type="credit",
        amount_minor=500,
        balance_after_minor=1500,
        description="Credit A",
        reference_id="REF-SHARED-1",
    )
    tx_b = Transaction(
        account_id=account_b.id,
        type="credit",
        amount_minor=500,
        balance_after_minor=1500,
        description="Credit B",
        reference_id="REF-SHARED-1",
    )
    db_session.add_all([tx_a, tx_b])
    await db_session.commit()

    # Both transactions must exist
    res_a = await db_session.get(Transaction, tx_a.id)
    res_b = await db_session.get(Transaction, tx_b.id)
    assert res_a is not None
    assert res_b is not None


async def test_pg_foreign_key_account_id(db_session: AsyncSession) -> None:
    """Verifies foreign key failure when referencing a non-existent account."""
    orphan_tx = Transaction(
        account_id=uuid.uuid4(),
        type="credit",
        amount_minor=1000,
        balance_after_minor=1000,
        description="Orphan transaction",
        reference_id="REF-ORPHAN-1",
    )
    db_session.add(orphan_tx)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_pg_foreign_key_on_delete_restrict(db_session: AsyncSession) -> None:
    """Verifies ON DELETE RESTRICT prevents deleting accounts with transaction history."""
    account = Account(name="Account to Delete", balance_minor=1000)
    db_session.add(account)
    await db_session.commit()

    tx = Transaction(
        account_id=account.id,
        type="credit",
        amount_minor=1000,
        balance_after_minor=1000,
        description="Credit preventing deletion",
        reference_id="REF-RESTRICT-1",
    )
    db_session.add(tx)
    await db_session.commit()

    # Attempting to delete account must raise IntegrityError
    await db_session.delete(account)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_pg_transfer_distinct_accounts(db_session: AsyncSession) -> None:
    """Verifies ck_transfers_distinct_accounts prevents transfer to self."""
    account = Account(name="Self Transfer Account", balance_minor=1000)
    db_session.add(account)
    await db_session.commit()

    transfer = Transfer(
        source_account_id=account.id,
        destination_account_id=account.id,
        amount_minor=100,
        reference_id="TXF-SELF-1",
        description="Transfer to same account",
    )
    db_session.add(transfer)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_pg_transfer_unique_reference(db_session: AsyncSession) -> None:
    """Verifies uq_transfers_reference prevents duplicate transfer references."""
    acc_a = Account(name="Sender", balance_minor=2000)
    acc_b = Account(name="Receiver", balance_minor=500)
    db_session.add_all([acc_a, acc_b])
    await db_session.commit()

    t1 = Transfer(
        source_account_id=acc_a.id,
        destination_account_id=acc_b.id,
        amount_minor=500,
        reference_id="TXF-DUP-REF",
        description="Transfer 1",
    )
    db_session.add(t1)
    await db_session.commit()

    t2 = Transfer(
        source_account_id=acc_a.id,
        destination_account_id=acc_b.id,
        amount_minor=500,
        reference_id="TXF-DUP-REF",
        description="Transfer 2",
    )
    db_session.add(t2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_pg_atomic_rollback_on_failure(db_session: AsyncSession) -> None:
    """Verifies that multi-step operations roll back completely on failure."""
    test_account_name = f"Rollback Test Account {uuid.uuid4()}"
    account = Account(name=test_account_name, balance_minor=5000)
    db_session.add(account)
    await db_session.commit()
    account_id = account.id

    # Step 1: Update account balance
    account.balance_minor += 2000

    # Step 2: Insert new ledger transaction
    tx = Transaction(
        account_id=account_id,
        type="credit",
        amount_minor=2000,
        balance_after_minor=7000,
        description="Tentative credit",
        reference_id=f"REF-ROLLBACK-{uuid.uuid4()}",
    )
    db_session.add(tx)
    await db_session.flush()

    # Simulate an unexpected failure prior to commit and execute rollback
    await db_session.rollback()

    # Verify that account balance reverted to 5000 and zero transactions exist
    refreshed_account = await db_session.get(Account, account_id)
    assert refreshed_account is not None
    assert refreshed_account.balance_minor == 5000

    stmt = sa.select(Transaction).where(Transaction.account_id == account_id)
    tx_results = (await db_session.scalars(stmt)).all()
    assert len(tx_results) == 0


async def test_pg_bigint_bounds_amount_minor(db_session: AsyncSession) -> None:
    """Verifies that PostgreSQL BIGINT column rejects amounts exceeding signed 64-bit max."""
    account = Account(name="Bigint Amount Account", balance_minor=1000)
    db_session.add(account)
    await db_session.commit()

    # Attempt to insert amount_minor exceeding signed BIGINT maximum (9_223_372_036_854_775_808)
    invalid_tx = Transaction(
        account_id=account.id,
        type="credit",
        amount_minor=9_223_372_036_854_775_808,
        balance_after_minor=9_223_372_036_854_776_808,
        description="Out of range amount",
        reference_id="REF-OVER-BIGINT",
    )
    db_session.add(invalid_tx)
    with pytest.raises(DBAPIError) as exc_info:
        await db_session.commit()
    err_str = str(exc_info.value).lower()
    assert "out of int64 range" in err_str or "out of range for type bigint" in err_str
    await db_session.rollback()


async def test_pg_bigint_bounds_balance_minor(db_session: AsyncSession) -> None:
    """Verifies that PostgreSQL BIGINT column rejects balances exceeding signed 64-bit max."""
    account = Account(name="Bigint Balance Account", balance_minor=1000)
    db_session.add(account)
    await db_session.commit()

    account.balance_minor = 9_223_372_036_854_775_808
    with pytest.raises(DBAPIError) as exc_info:
        await db_session.commit()
    err_str = str(exc_info.value).lower()
    assert "out of int64 range" in err_str or "out of range for type bigint" in err_str
    await db_session.rollback()


async def test_pg_max_bigint_succeeds(db_session: AsyncSession) -> None:
    """Verifies that exact maximum signed BIGINT (9_223_372_036_854_775_807) succeeds in PostgreSQL."""
    account = Account(name="Max Bigint Account", balance_minor=9_223_372_036_854_775_807)
    db_session.add(account)
    await db_session.commit()

    tx = Transaction(
        account_id=account.id,
        type="credit",
        amount_minor=9_223_372_036_854_775_807,
        balance_after_minor=9_223_372_036_854_775_807,
        description="Max BIGINT amount",
        reference_id="REF-EXACT-MAX",
    )
    db_session.add(tx)
    await db_session.commit()

    persisted = await db_session.get(Transaction, tx.id)
    assert persisted is not None
    assert persisted.amount_minor == 9_223_372_036_854_775_807
    assert persisted.balance_after_minor == 9_223_372_036_854_775_807
