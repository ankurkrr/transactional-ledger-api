"""Service layer implementation for account management and balance inquiries.

Coordinates account creation, balance lookups, and transaction boundaries.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ledger.core.exceptions import AccountNotFoundError
from ledger.models.account import Account
from ledger.schemas.account import (
    AccountCreateRequest,
    AccountResponse,
    BalanceResponse,
)
from ledger.services.transaction_manager import transaction_scope


class AccountService:
    """Coordinates account creation and balance queries.

    Owns database session interactions and boundaries for account operations.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_account(self, data: AccountCreateRequest) -> AccountResponse:
        """Creates a new ledger account initialized with a zero balance.

        Client cannot choose initial balance, ID, or timestamps; these are strictly
        server-managed.

        Args:
            data: Validated account creation parameters.

        Returns:
            AccountResponse with generated UUID and timestamp metadata.
        """
        async with transaction_scope(self.session):
            account = Account(
                name=data.name,
                balance_minor=0,
                currency="INR",
            )
            self.session.add(account)
            await self.session.flush()
            await self.session.refresh(account)

        return AccountResponse.model_validate(account)

    async def get_account_by_id(self, account_id: uuid.UUID) -> AccountResponse:
        """Retrieves an existing account by its unique identifier.

        Args:
            account_id: RFC 4122 UUIDv4 of the account.

        Returns:
            AccountResponse for the requested account.

        Raises:
            AccountNotFoundError: If no account exists with the given ID.
        """
        stmt = select(Account).where(Account.id == account_id)
        result = await self.session.execute(stmt)
        account = result.scalar_one_or_none()
        if account is None:
            raise AccountNotFoundError()
        return AccountResponse.model_validate(account)

    async def get_balance(self, account_id: uuid.UUID) -> BalanceResponse:
        """Retrieves the current reconciled balance for an account.

        Args:
            account_id: RFC 4122 UUIDv4 of the account.

        Returns:
            BalanceResponse with balance_minor and updated_at timestamp.

        Raises:
            AccountNotFoundError: If no account exists with the given ID.
        """
        stmt = select(Account).where(Account.id == account_id)
        result = await self.session.execute(stmt)
        account = result.scalar_one_or_none()
        if account is None:
            raise AccountNotFoundError()
        return BalanceResponse.model_validate(account)
