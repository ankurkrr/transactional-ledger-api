"""Unit tests for service foundation, dependency injection, and transaction management."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request

from ledger.api.dependencies import (
    get_account_service,
    get_ledger_service,
    get_request_id,
    get_transfer_service,
)
from ledger.core.exceptions import (
    AccountNotFoundError,
    DuplicateReferenceError,
    InsufficientFundsError,
    InvalidAmountError,
    InvalidTransactionTypeError,
    LedgerError,
    TransferSameAccountError,
)
from ledger.services.account_service import AccountService
from ledger.services.ledger_service import LedgerService
from ledger.services.transaction_manager import transaction_scope
from ledger.services.transfer_service import TransferService


class TestServiceBoundaries:
    """Tests verifying service layer instantiation and method boundary definitions."""

    @pytest.mark.asyncio
    async def test_account_service_interface(self) -> None:
        """AccountService binds session and exposes required account methods."""
        mock_session = AsyncMock()
        service = AccountService(mock_session)
        assert service.session is mock_session
        assert callable(service.create_account)
        assert callable(service.get_account_by_id)
        assert callable(service.get_balance)

    @pytest.mark.asyncio
    async def test_ledger_service_interface(self) -> None:
        """LedgerService binds session and exposes required ledger methods."""
        mock_session = AsyncMock()
        service = LedgerService(mock_session)
        assert service.session is mock_session
        assert callable(service.add_transaction)
        assert callable(service.get_history)

    @pytest.mark.asyncio
    async def test_transfer_service_interface(self) -> None:
        """TransferService binds session and exposes required transfer execution method."""
        mock_session = AsyncMock()
        service = TransferService(mock_session)
        assert service.session is mock_session
        assert callable(service.execute_transfer)


class TestTransactionScope:
    """Tests verifying ADR-009 transaction_scope rollback ownership and traceback preservation."""

    @pytest.mark.asyncio
    async def test_transaction_scope_commits_on_success(self) -> None:
        """transaction_scope calls commit on normal exit."""
        mock_session = AsyncMock()
        async with transaction_scope(mock_session) as session:
            assert session is mock_session

        mock_session.commit.assert_awaited_once()
        mock_session.rollback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_transaction_scope_rolls_back_and_reraises_on_error(self) -> None:
        """transaction_scope calls rollback immediately on error and re-raises."""
        mock_session = AsyncMock()

        class CustomDomainError(Exception):
            pass

        with pytest.raises(CustomDomainError):
            async with transaction_scope(mock_session):
                raise CustomDomainError("Simulated write failure")

        mock_session.rollback.assert_awaited_once()
        mock_session.commit.assert_not_awaited()


class TestDependencyInjectionWiring:
    """Tests verifying FastAPI dependency wiring."""

    def test_get_account_service_factory(self) -> None:
        """get_account_service provides an AccountService bound to session."""
        mock_session = AsyncMock()
        svc = get_account_service(mock_session)
        assert isinstance(svc, AccountService)
        assert svc.session is mock_session

    def test_get_ledger_service_factory(self) -> None:
        """get_ledger_service provides a LedgerService bound to session."""
        mock_session = AsyncMock()
        svc = get_ledger_service(mock_session)
        assert isinstance(svc, LedgerService)
        assert svc.session is mock_session

    def test_get_transfer_service_factory(self) -> None:
        """get_transfer_service provides a TransferService bound to session."""
        mock_session = AsyncMock()
        svc = get_transfer_service(mock_session)
        assert isinstance(svc, TransferService)
        assert svc.session is mock_session

    def test_get_request_id_from_header(self) -> None:
        """Extracts X-Request-ID when present in request headers."""
        request = MagicMock(spec=Request)
        request.headers = {"X-Request-ID": "custom-trace-id-123"}
        extracted = get_request_id(request)
        assert extracted == "custom-trace-id-123"

    def test_get_request_id_generated_when_missing(self) -> None:
        """Generates a traceable req_<hex> identifier when header is missing or blank."""
        request_no_header = MagicMock(spec=Request)
        request_no_header.headers = {}
        gen1 = get_request_id(request_no_header)
        assert gen1.startswith("req_")
        assert len(gen1) > 10

        request_empty_header = MagicMock(spec=Request)
        request_empty_header.headers = {"X-Request-ID": "   "}
        gen2 = get_request_id(request_empty_header)
        assert gen2.startswith("req_")


class TestDomainExceptions:
    """Tests verifying domain exception hierarchy codes and HTTP mappings."""

    def test_exception_status_codes_and_codes(self) -> None:
        """All domain exceptions define correct error codes and HTTP statuses."""
        assert issubclass(AccountNotFoundError, LedgerError)
        assert AccountNotFoundError.http_status_code == 404
        assert AccountNotFoundError.code == "ACCOUNT_NOT_FOUND"

        assert issubclass(InsufficientFundsError, LedgerError)
        assert InsufficientFundsError.http_status_code == 422
        assert InsufficientFundsError.code == "INSUFFICIENT_FUNDS"

        assert issubclass(DuplicateReferenceError, LedgerError)
        assert DuplicateReferenceError.http_status_code == 409
        assert DuplicateReferenceError.code == "DUPLICATE_REFERENCE"

        assert issubclass(InvalidAmountError, LedgerError)
        assert InvalidAmountError.http_status_code == 422
        assert InvalidAmountError.code == "INVALID_AMOUNT"

        assert issubclass(InvalidTransactionTypeError, LedgerError)
        assert InvalidTransactionTypeError.http_status_code == 422
        assert InvalidTransactionTypeError.code == "INVALID_TRANSACTION_TYPE"

        assert issubclass(TransferSameAccountError, LedgerError)
        assert TransferSameAccountError.http_status_code == 422
        assert TransferSameAccountError.code == "TRANSFER_SAME_ACCOUNT"
