"""Unit tests for LedgerService financial transaction execution logic."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import DataError, IntegrityError

from ledger.core.exceptions import (
    AccountNotFoundError,
    DuplicateReferenceError,
    InsufficientFundsError,
    InvalidAmountError,
)
from ledger.models.account import Account
from ledger.models.transaction import Transaction
from ledger.schemas.transaction import (
    TransactionCreateRequest,
    TransactionHistoryResponse,
    TransactionResponse,
)
from ledger.services.ledger_service import LedgerService


@pytest.mark.asyncio
async def test_add_transaction_credit_success() -> None:
    """A credit transaction atomically increases account balance and records ledger entry."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    account = Account(
        id=account_id,
        name="John Doe",
        balance_minor=10000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # Mock execute: first query returns account, second query returns None (no duplicate ref)
    mock_account_result = MagicMock()
    mock_account_result.scalar_one_or_none.return_value = account

    mock_ref_result = MagicMock()
    mock_ref_result.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[mock_account_result, mock_ref_result])

    async def mock_refresh(instance: Transaction) -> None:
        instance.created_at = datetime.now(UTC)
        if not instance.id:
            instance.id = uuid.uuid4()

    mock_session.refresh = AsyncMock(side_effect=mock_refresh)

    service = LedgerService(mock_session)
    req = TransactionCreateRequest(
        type="credit",
        amount="50.00",
        description="Salary deposit",
        reference_id="REF-SALARY-01",
    )

    res = await service.add_transaction(account_id, req)

    assert isinstance(res, TransactionResponse)
    assert res.account_id == account_id
    assert res.type == "credit"
    assert res.amount == "50.00"
    assert res.balance_after == "150.00"
    assert res.currency == "INR"
    assert res.description == "Salary deposit"
    assert res.reference_id == "REF-SALARY-01"
    assert account.balance_minor == 15000

    mock_session.commit.assert_awaited_once()
    mock_session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_transaction_debit_success() -> None:
    """A debit transaction atomically decreases account balance and records ledger entry."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    account = Account(
        id=account_id,
        name="Jane Debit",
        balance_minor=20000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_account_result = MagicMock()
    mock_account_result.scalar_one_or_none.return_value = account

    mock_ref_result = MagicMock()
    mock_ref_result.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[mock_account_result, mock_ref_result])

    async def mock_refresh(instance: Transaction) -> None:
        instance.created_at = datetime.now(UTC)
        if not instance.id:
            instance.id = uuid.uuid4()

    mock_session.refresh = AsyncMock(side_effect=mock_refresh)

    service = LedgerService(mock_session)
    req = TransactionCreateRequest(
        type="debit",
        amount="75.00",
        description="Subscription renewal",
        reference_id="REF-SUB-01",
    )

    res = await service.add_transaction(account_id, req)

    assert isinstance(res, TransactionResponse)
    assert res.account_id == account_id
    assert res.type == "debit"
    assert res.amount == "75.00"
    assert res.balance_after == "125.00"
    assert res.currency == "INR"
    assert account.balance_minor == 12500

    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_transaction_debit_insufficient_funds() -> None:
    """Debit exceeding available balance raises InsufficientFundsError and rolls back."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    account = Account(
        id=account_id,
        name="Low Balance User",
        balance_minor=3000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_account_result = MagicMock()
    mock_account_result.scalar_one_or_none.return_value = account

    mock_ref_result = MagicMock()
    mock_ref_result.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[mock_account_result, mock_ref_result])

    service = LedgerService(mock_session)
    req = TransactionCreateRequest(
        type="debit",
        amount="50.00",
        description="Overdraft attempt",
        reference_id="REF-OVER-01",
    )

    with pytest.raises(InsufficientFundsError) as exc_info:
        await service.add_transaction(account_id, req)

    assert exc_info.value.code == "INSUFFICIENT_FUNDS"
    assert exc_info.value.http_status_code == 422
    assert account.balance_minor == 3000  # Balance unchanged

    mock_session.rollback.assert_awaited_once()
    mock_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_transaction_missing_account() -> None:
    """Transaction targeting a non-existent account raises AccountNotFoundError."""
    account_id = uuid.uuid4()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    service = LedgerService(mock_session)
    req = TransactionCreateRequest(
        type="credit",
        amount="10.00",
        description="Deposit",
        reference_id="REF-01",
    )

    with pytest.raises(AccountNotFoundError) as exc_info:
        await service.add_transaction(account_id, req)

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"
    assert exc_info.value.http_status_code == 404
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_transaction_duplicate_reference() -> None:
    """Transaction with an existing reference_id on same account raises DuplicateReferenceError."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    account = Account(
        id=account_id,
        name="User",
        balance_minor=10000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_account_result = MagicMock()
    mock_account_result.scalar_one_or_none.return_value = account

    # Reference query returns an existing transaction ID
    mock_ref_result = MagicMock()
    mock_ref_result.scalar_one_or_none.return_value = uuid.uuid4()

    mock_session.execute = AsyncMock(side_effect=[mock_account_result, mock_ref_result])

    service = LedgerService(mock_session)
    req = TransactionCreateRequest(
        type="credit",
        amount="20.00",
        description="Repeated payment",
        reference_id="REF-DUP-01",
    )

    with pytest.raises(DuplicateReferenceError) as exc_info:
        await service.add_transaction(account_id, req)

    assert exc_info.value.code == "DUPLICATE_REFERENCE"
    assert exc_info.value.http_status_code == 409
    assert account.balance_minor == 10000  # Balance untouched

    mock_session.rollback.assert_awaited_once()
    mock_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_transaction_catches_integrity_error_duplicate_race() -> None:
    """Unique constraint IntegrityError race condition maps to DuplicateReferenceError."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    account = Account(
        id=account_id,
        name="User",
        balance_minor=10000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_account_result = MagicMock()
    mock_account_result.scalar_one_or_none.return_value = account

    mock_ref_result = MagicMock()
    mock_ref_result.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[mock_account_result, mock_ref_result])

    # Flush raises an IntegrityError representing unique violation race
    mock_session.flush.side_effect = IntegrityError(
        statement="INSERT INTO transactions...",
        params={},
        orig=Exception(
            'duplicate key value violates unique constraint "uq_transactions_account_reference"'
        ),
    )

    service = LedgerService(mock_session)
    req = TransactionCreateRequest(
        type="credit",
        amount="50.00",
        description="Concurrent duplicate",
        reference_id="REF-RACE-01",
    )

    with pytest.raises(DuplicateReferenceError):
        await service.add_transaction(account_id, req)

    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_transaction_rejects_balance_overflow() -> None:
    """A credit causing balance to exceed signed BIGINT max raises InvalidAmountError and rolls back."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    account = Account(
        id=account_id,
        name="Wealthy Account",
        balance_minor=9_223_372_036_854_775_800,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_account_result = MagicMock()
    mock_account_result.scalar_one_or_none.return_value = account

    mock_ref_result = MagicMock()
    mock_ref_result.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[mock_account_result, mock_ref_result])

    service = LedgerService(mock_session)
    # Credit of 100 paise would push balance to 9_223_372_036_854_775_900 > MAX_BIGINT
    req = TransactionCreateRequest(
        type="credit",
        amount="1.00",
        description="Overflow attempt",
        reference_id="REF-OVERFLOW-01",
    )

    with pytest.raises(InvalidAmountError) as exc_info:
        await service.add_transaction(account_id, req)

    assert exc_info.value.code == "INVALID_AMOUNT"
    assert exc_info.value.http_status_code == 422
    assert account.balance_minor == 9_223_372_036_854_775_800  # Untouched

    mock_session.rollback.assert_awaited_once()
    mock_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_transaction_catches_data_error_overflow() -> None:
    """PostgreSQL DataError (out of range for type bigint) translates to InvalidAmountError."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    account = Account(
        id=account_id,
        name="User",
        balance_minor=10000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_account_result = MagicMock()
    mock_account_result.scalar_one_or_none.return_value = account

    mock_ref_result = MagicMock()
    mock_ref_result.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[mock_account_result, mock_ref_result])

    # Flush raises a DataError representing integer out of range
    mock_session.flush.side_effect = DataError(
        statement="INSERT INTO transactions...",
        params={},
        orig=Exception("value out of range for type bigint"),
    )

    service = LedgerService(mock_session)
    req = TransactionCreateRequest(
        type="credit",
        amount="50.00",
        description="DataError trigger",
        reference_id="REF-DATA-ERR-01",
    )

    with pytest.raises(InvalidAmountError):
        await service.add_transaction(account_id, req)

    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_transaction_rejects_invalid_amount_service_level() -> None:
    """Service independently enforces strictly positive amounts and MAX_BIGINT."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    account = Account(
        id=account_id,
        name="User",
        balance_minor=10000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_account_result = MagicMock()
    mock_account_result.scalar_one_or_none.return_value = account

    mock_session.execute = AsyncMock(return_value=mock_account_result)

    service = LedgerService(mock_session)

    # Construct request bypassing Pydantic validation via MagicMock
    req_zero = MagicMock(spec=TransactionCreateRequest)
    req_zero.type = "credit"
    req_zero.amount_minor = 0
    req_zero.description = "Zero"
    req_zero.reference_id = "REF-ZERO"

    with pytest.raises(InvalidAmountError):
        await service.add_transaction(account_id, req_zero)

    req_exceeding = MagicMock(spec=TransactionCreateRequest)
    req_exceeding.type = "credit"
    req_exceeding.amount_minor = 9_223_372_036_854_775_808
    req_exceeding.description = "Exceeding"
    req_exceeding.reference_id = "REF-EXCEED"

    with pytest.raises(InvalidAmountError):
        await service.add_transaction(account_id, req_exceeding)


@pytest.mark.asyncio
async def test_get_history_missing_account_raises_404() -> None:
    """get_history raises AccountNotFoundError when account does not exist."""
    account_id = uuid.uuid4()
    mock_session = AsyncMock()

    mock_acc_res = MagicMock()
    mock_acc_res.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_acc_res)

    service = LedgerService(mock_session)
    with pytest.raises(AccountNotFoundError) as exc_info:
        await service.get_history(account_id)

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"
    assert exc_info.value.http_status_code == 404


@pytest.mark.asyncio
async def test_get_history_empty_returns_zero_totals() -> None:
    """get_history on account with zero transactions returns empty items and zero pagination totals."""
    account_id = uuid.uuid4()
    mock_session = AsyncMock()

    # 1. Account exists
    mock_acc_res = MagicMock()
    mock_acc_res.scalar_one_or_none.return_value = account_id

    # 2. Count returns 0
    mock_count_res = MagicMock()
    mock_count_res.scalar.return_value = 0

    # 3. Items query returns empty list
    mock_items_res = MagicMock()
    mock_items_res.scalars.return_value.all.return_value = []

    mock_session.execute = AsyncMock(side_effect=[mock_acc_res, mock_count_res, mock_items_res])

    service = LedgerService(mock_session)
    res = await service.get_history(account_id, page=1, limit=20)

    assert isinstance(res, TransactionHistoryResponse)
    assert res.items == []
    assert res.pagination.current_page == 1
    assert res.pagination.page_size == 20
    assert res.pagination.total_records == 0
    assert res.pagination.total_pages == 0


@pytest.mark.asyncio
async def test_get_history_with_records_and_pagination_math() -> None:
    """get_history computes correct total_pages and returns validated transaction items."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_session = AsyncMock()

    tx1 = Transaction(
        id=uuid.uuid4(),
        account_id=account_id,
        type="credit",
        amount_minor=5000,
        balance_after_minor=5000,
        description="Initial deposit",
        reference_id="REF-01",
        created_at=now,
    )

    # 1. Account exists
    mock_acc_res = MagicMock()
    mock_acc_res.scalar_one_or_none.return_value = account_id

    # 2. Total count is 25 items
    mock_count_res = MagicMock()
    mock_count_res.scalar.return_value = 25

    # 3. Page returns [tx1]
    mock_items_res = MagicMock()
    mock_items_res.scalars.return_value.all.return_value = [tx1]

    mock_session.execute = AsyncMock(side_effect=[mock_acc_res, mock_count_res, mock_items_res])

    service = LedgerService(mock_session)
    res = await service.get_history(account_id, page=2, limit=10)

    assert len(res.items) == 1
    assert res.items[0].id == tx1.id
    assert res.items[0].amount == "50.00"
    assert res.items[0].balance_after == "50.00"
    assert res.items[0].currency == "INR"
    assert res.pagination.current_page == 2
    assert res.pagination.page_size == 10
    assert res.pagination.total_records == 25
    assert res.pagination.total_pages == 3  # ceil(25 / 10) = 3


@pytest.mark.asyncio
async def test_get_history_with_type_filter() -> None:
    """get_history forwards type filter correctly in count and items query."""
    account_id = uuid.uuid4()
    mock_session = AsyncMock()

    mock_acc_res = MagicMock()
    mock_acc_res.scalar_one_or_none.return_value = account_id

    mock_count_res = MagicMock()
    mock_count_res.scalar.return_value = 5

    mock_items_res = MagicMock()
    mock_items_res.scalars.return_value.all.return_value = []

    mock_session.execute = AsyncMock(side_effect=[mock_acc_res, mock_count_res, mock_items_res])

    service = LedgerService(mock_session)
    res = await service.get_history(account_id, type_filter="credit", page=1, limit=5)

    assert res.pagination.total_records == 5
    assert res.pagination.total_pages == 1
    assert mock_session.execute.call_count == 3
