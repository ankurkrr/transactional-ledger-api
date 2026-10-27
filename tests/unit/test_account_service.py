"""Unit tests for AccountService logic and error handling."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ledger.core.exceptions import AccountNotFoundError
from ledger.models.account import Account
from ledger.schemas.account import AccountCreateRequest, AccountResponse, BalanceResponse
from ledger.services.account_service import AccountService


@pytest.mark.asyncio
async def test_create_account_initializes_zero_balance() -> None:
    """Verifies that create_account always creates an account with 0 balance and INR currency."""
    mock_session = AsyncMock()

    # When refresh is called, set server timestamps and id
    async def mock_refresh(instance: Account) -> None:
        now = datetime.now(UTC)
        instance.created_at = now
        instance.updated_at = now
        if not instance.id:
            instance.id = uuid.uuid4()

    mock_session.refresh = AsyncMock(side_effect=mock_refresh)
    mock_session.add = MagicMock()

    service = AccountService(mock_session)
    req = AccountCreateRequest(name="Alice Bob")
    res = await service.create_account(req)

    assert isinstance(res, AccountResponse)
    assert res.name == "Alice Bob"
    assert res.balance == "0.00"
    assert res.currency == "INR"
    assert res.id is not None
    assert res.created_at is not None
    assert res.updated_at is not None

    mock_session.add.assert_called_once()
    mock_session.flush.assert_awaited_once()
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_account_rollback_on_failure() -> None:
    """Verifies that create_account triggers session.rollback when a flush fails."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush.side_effect = RuntimeError("Simulated DB flush crash")

    service = AccountService(mock_session)
    req = AccountCreateRequest(name="Failing Account")

    with pytest.raises(RuntimeError, match="Simulated DB flush crash"):
        await service.create_account(req)

    mock_session.rollback.assert_awaited_once()
    mock_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_account_by_id_success() -> None:
    """Verifies get_account_by_id returns AccountResponse when account exists."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    fake_account = Account(
        id=account_id,
        name="John Doe",
        balance_minor=5000,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_account
    mock_session.execute.return_value = mock_result

    service = AccountService(mock_session)
    res = await service.get_account_by_id(account_id)

    assert isinstance(res, AccountResponse)
    assert res.id == account_id
    assert res.name == "John Doe"
    assert res.balance == "50.00"
    assert res.currency == "INR"


@pytest.mark.asyncio
async def test_get_account_by_id_not_found() -> None:
    """Verifies get_account_by_id raises AccountNotFoundError when account does not exist."""
    account_id = uuid.uuid4()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    service = AccountService(mock_session)
    with pytest.raises(AccountNotFoundError) as exc_info:
        await service.get_account_by_id(account_id)

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"
    assert exc_info.value.http_status_code == 404


@pytest.mark.asyncio
async def test_get_balance_success() -> None:
    """Verifies get_balance returns BalanceResponse when account exists."""
    account_id = uuid.uuid4()
    now = datetime.now(UTC)
    fake_account = Account(
        id=account_id,
        name="Jane Balance",
        balance_minor=12500,
        currency="INR",
        created_at=now,
        updated_at=now,
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_account
    mock_session.execute.return_value = mock_result

    service = AccountService(mock_session)
    res = await service.get_balance(account_id)

    assert isinstance(res, BalanceResponse)
    assert res.account_id == account_id
    assert res.balance == "125.00"
    assert res.currency == "INR"
    assert res.updated_at == now


@pytest.mark.asyncio
async def test_get_balance_not_found() -> None:
    """Verifies get_balance raises AccountNotFoundError when account does not exist."""
    account_id = uuid.uuid4()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    service = AccountService(mock_session)
    with pytest.raises(AccountNotFoundError) as exc_info:
        await service.get_balance(account_id)

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"
    assert exc_info.value.http_status_code == 404
