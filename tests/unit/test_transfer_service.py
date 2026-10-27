"""Unit tests for TransferService business logic, locking, and invariants."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from ledger.core.exceptions import (
    AccountNotFoundError,
    ConcurrencyConflictError,
    CurrencyMismatchError,
    DuplicateReferenceError,
    InsufficientFundsError,
    InvalidAmountError,
    TransferSameAccountError,
)
from ledger.models.account import Account
from ledger.models.transfer import Transfer
from ledger.schemas.transfer import TransferCreateRequest, TransferResponse
from ledger.services.transfer_service import MAX_BIGINT, TransferService


@pytest.mark.asyncio
async def test_execute_transfer_success_happy_path() -> None:
    """Successful transfer debits source, credits destination, inserts transfer and 2 tx records."""
    src_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    dst_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    now = datetime.now(UTC)

    src_acc = Account(
        id=src_id,
        name="Source User",
        balance_minor=10000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )
    dst_acc = Account(
        id=dst_id,
        name="Dest User",
        balance_minor=2000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # Query 1: src_acc (since src_id < dst_id)
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = src_acc

    # Query 2: dst_acc
    res2 = MagicMock()
    res2.scalar_one_or_none.return_value = dst_acc

    # Query 3: transfer reference check (returns None -> no duplicate)
    res_ref = MagicMock()
    res_ref.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[res1, res2, res_ref])

    async def mock_refresh(instance: Transfer) -> None:
        instance.created_at = datetime.now(UTC)
        if not instance.id:
            instance.id = uuid.uuid4()

    mock_session.refresh = AsyncMock(side_effect=mock_refresh)

    service = TransferService(mock_session)
    req = TransferCreateRequest(
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="30.00",
        description="P2P reimbursement",
        reference_id="XFER-SUCCESS-01",
    )

    res = await service.execute_transfer(req)

    assert isinstance(res, TransferResponse)
    assert res.source_account_id == src_id
    assert res.destination_account_id == dst_id
    assert res.amount == "30.00"
    assert res.currency == "INR"
    assert res.status == "completed"
    assert res.reference_id == "XFER-SUCCESS-01"

    # Balances updated
    assert src_acc.balance_minor == 7000
    assert dst_acc.balance_minor == 5000

    # 3 entities added: 1 transfer, 2 transactions
    assert mock_session.add.call_count == 3
    mock_session.commit.assert_awaited_once()
    mock_session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_transfer_same_account_rejected() -> None:
    """Transfer targeting identical source and destination raises TransferSameAccountError."""
    same_id = uuid.uuid4()
    mock_session = AsyncMock()
    service = TransferService(mock_session)

    # Bypass Pydantic validator via object.__new__ to test service-level defense
    req = object.__new__(TransferCreateRequest)
    object.__setattr__(req, "source_account_id", same_id)
    object.__setattr__(req, "destination_account_id", same_id)
    object.__setattr__(req, "amount", "10.00")
    object.__setattr__(req, "description", "Self transfer")
    object.__setattr__(req, "reference_id", "XFER-SAME-SVC")

    with pytest.raises(TransferSameAccountError) as exc_info:
        await service.execute_transfer(req)

    assert exc_info.value.code == "TRANSFER_SAME_ACCOUNT"
    assert exc_info.value.http_status_code == 422


@pytest.mark.asyncio
async def test_execute_transfer_missing_source_account_raises_404() -> None:
    """Missing source account raises AccountNotFoundError."""
    src_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    dst_id = uuid.UUID("22222222-2222-4222-8222-222222222222")

    mock_session = AsyncMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = None  # Missing account
    mock_session.execute = AsyncMock(return_value=res1)

    service = TransferService(mock_session)
    req = TransferCreateRequest(
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="10.00",
        description="Transfer",
        reference_id="XFER-MISSING-SRC",
    )

    with pytest.raises(AccountNotFoundError) as exc_info:
        await service.execute_transfer(req)

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"
    assert exc_info.value.http_status_code == 404
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_transfer_missing_destination_account_raises_404() -> None:
    """Missing destination account raises AccountNotFoundError."""
    src_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    dst_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    now = datetime.now(UTC)

    src_acc = Account(
        id=src_id,
        name="Source User",
        balance_minor=10000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = src_acc
    res2 = MagicMock()
    res2.scalar_one_or_none.return_value = None  # Missing dest

    mock_session.execute = AsyncMock(side_effect=[res1, res2])

    service = TransferService(mock_session)
    req = TransferCreateRequest(
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="10.00",
        description="Transfer",
        reference_id="XFER-MISSING-DST",
    )

    with pytest.raises(AccountNotFoundError) as exc_info:
        await service.execute_transfer(req)

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"
    assert exc_info.value.http_status_code == 404
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_transfer_currency_mismatch_raises_422() -> None:
    """Accounts with mismatched or unsupported currencies raise CurrencyMismatchError."""
    src_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    dst_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    now = datetime.now(UTC)

    src_acc = Account(
        id=src_id, name="Src", balance_minor=5000, currency="INR", created_at=now, updated_at=now
    )
    dst_acc = Account(
        id=dst_id, name="Dst", balance_minor=5000, currency="USD", created_at=now, updated_at=now
    )

    mock_session = AsyncMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = src_acc
    res2 = MagicMock()
    res2.scalar_one_or_none.return_value = dst_acc

    mock_session.execute = AsyncMock(side_effect=[res1, res2])

    service = TransferService(mock_session)
    req = TransferCreateRequest(
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="10.00",
        description="Currency mismatch test",
        reference_id="XFER-CURR-TEST",
    )

    with pytest.raises(CurrencyMismatchError) as exc_info:
        await service.execute_transfer(req)

    assert exc_info.value.code == "CURRENCY_MISMATCH"
    assert exc_info.value.http_status_code == 422
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_transfer_insufficient_funds_raises_422() -> None:
    """Transfer exceeding source balance raises InsufficientFundsError and preserves state."""
    src_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    dst_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    now = datetime.now(UTC)

    src_acc = Account(
        id=src_id, name="Src", balance_minor=1500, currency="INR", created_at=now, updated_at=now
    )
    dst_acc = Account(
        id=dst_id, name="Dst", balance_minor=5000, currency="INR", created_at=now, updated_at=now
    )

    mock_session = AsyncMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = src_acc
    res2 = MagicMock()
    res2.scalar_one_or_none.return_value = dst_acc
    res_ref = MagicMock()
    res_ref.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[res1, res2, res_ref])

    service = TransferService(mock_session)
    req = TransferCreateRequest(
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="20.00",  # 2000 paise > 1500 paise
        description="Overdraft transfer",
        reference_id="XFER-OVER-01",
    )

    with pytest.raises(InsufficientFundsError) as exc_info:
        await service.execute_transfer(req)

    assert exc_info.value.code == "INSUFFICIENT_FUNDS"
    assert exc_info.value.http_status_code == 422
    assert src_acc.balance_minor == 1500  # Untouched
    assert dst_acc.balance_minor == 5000  # Untouched
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_transfer_duplicate_reference_precheck_raises_409() -> None:
    """Duplicate reference_id caught in pre-check raises DuplicateReferenceError."""
    src_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    dst_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    now = datetime.now(UTC)

    src_acc = Account(
        id=src_id, name="Src", balance_minor=5000, currency="INR", created_at=now, updated_at=now
    )
    dst_acc = Account(
        id=dst_id, name="Dst", balance_minor=5000, currency="INR", created_at=now, updated_at=now
    )

    mock_session = AsyncMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = src_acc
    res2 = MagicMock()
    res2.scalar_one_or_none.return_value = dst_acc
    res_ref = MagicMock()
    res_ref.scalar_one_or_none.return_value = uuid.uuid4()  # Reference already exists

    mock_session.execute = AsyncMock(side_effect=[res1, res2, res_ref])

    service = TransferService(mock_session)
    req = TransferCreateRequest(
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="10.00",
        description="Duplicate attempt",
        reference_id="XFER-DUP-01",
    )

    with pytest.raises(DuplicateReferenceError) as exc_info:
        await service.execute_transfer(req)

    assert exc_info.value.code == "DUPLICATE_REFERENCE"
    assert exc_info.value.http_status_code == 409
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_transfer_destination_balance_overflow_raises_422() -> None:
    """Destination balance exceeding MAX_BIGINT raises InvalidAmountError and rolls back."""
    src_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    dst_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    now = datetime.now(UTC)

    src_acc = Account(
        id=src_id, name="Src", balance_minor=5000, currency="INR", created_at=now, updated_at=now
    )
    dst_acc = Account(
        id=dst_id,
        name="Dst",
        balance_minor=MAX_BIGINT - 10,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = src_acc
    res2 = MagicMock()
    res2.scalar_one_or_none.return_value = dst_acc
    res_ref = MagicMock()
    res_ref.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[res1, res2, res_ref])

    service = TransferService(mock_session)
    req = TransferCreateRequest(
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="1.00",  # MAX_BIGINT - 10 + 100 > MAX_BIGINT
        description="Overflow attempt",
        reference_id="XFER-OVERFLOW-01",
    )

    with pytest.raises(InvalidAmountError) as exc_info:
        await service.execute_transfer(req)

    assert exc_info.value.code == "INVALID_AMOUNT"
    assert exc_info.value.http_status_code == 422
    assert src_acc.balance_minor == 5000  # Untouched
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_transfer_catches_integrity_error_for_uq_transfers_reference() -> None:
    """IntegrityError on uq_transfers_reference translates to DuplicateReferenceError."""
    src_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    dst_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    now = datetime.now(UTC)

    src_acc = Account(
        id=src_id, name="Src", balance_minor=5000, currency="INR", created_at=now, updated_at=now
    )
    dst_acc = Account(
        id=dst_id, name="Dst", balance_minor=2000, currency="INR", created_at=now, updated_at=now
    )

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = src_acc
    res2 = MagicMock()
    res2.scalar_one_or_none.return_value = dst_acc
    res_ref = MagicMock()
    res_ref.scalar_one_or_none.return_value = None

    mock_session.execute = AsyncMock(side_effect=[res1, res2, res_ref])
    mock_session.flush.side_effect = IntegrityError(
        statement="INSERT INTO transfers...",
        params={},
        orig=Exception("duplicate key value violates unique constraint 'uq_transfers_reference'"),
    )

    service = TransferService(mock_session)
    req = TransferCreateRequest(
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="10.00",
        description="Race duplicate",
        reference_id="XFER-RACE-01",
    )

    with pytest.raises(DuplicateReferenceError):
        await service.execute_transfer(req)

    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_transfer_deadlock_translates_to_concurrency_conflict() -> None:
    """PostgreSQL SQLSTATE 40P01 deadlock translates to ConcurrencyConflictError (HTTP 503)."""
    src_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    dst_id = uuid.UUID("22222222-2222-4222-8222-222222222222")

    class MockDeadlockError(Exception):
        sqlstate = "40P01"

    mock_session = AsyncMock()
    orig_exc = MockDeadlockError("deadlock detected")
    mock_session.execute = AsyncMock(
        side_effect=DBAPIError(statement="SELECT ... FOR UPDATE", params={}, orig=orig_exc)
    )

    service = TransferService(mock_session)
    req = TransferCreateRequest(
        source_account_id=src_id,
        destination_account_id=dst_id,
        amount="10.00",
        description="Deadlock transfer",
        reference_id="XFER-DEADLOCK-01",
    )

    with pytest.raises(ConcurrencyConflictError) as exc_info:
        await service.execute_transfer(req)

    assert exc_info.value.code == "CONCURRENCY_CONFLICT"
    assert exc_info.value.http_status_code == 503
    mock_session.rollback.assert_awaited_once()
