"""Service layer implementation for transaction execution and ledger history."""

import logging
import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ledger.core.exceptions import (
    AccountNotFoundError,
    DuplicateReferenceError,
    InsufficientFundsError,
    InvalidAmountError,
)
from ledger.models.account import Account
from ledger.models.transaction import Transaction
from ledger.schemas.transaction import (
    PaginationMetadata,
    TransactionCreateRequest,
    TransactionHistoryResponse,
    TransactionResponse,
    TransactionType,
)
from ledger.services.transaction_manager import transaction_scope

logger = logging.getLogger(__name__)


MAX_BIGINT: int = 9_223_372_036_854_775_807


class LedgerService:
    """Coordinates atomic credit/debit operations and transaction ledger queries.

    Owns transaction boundaries, pessimistic row locks, and ledger invariant verification.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_transaction(
        self,
        account_id: uuid.UUID,
        data: TransactionCreateRequest,
    ) -> TransactionResponse:
        """Executes an atomic credit or debit operation against an account.

        Guarantees that balance updates and ledger entries succeed or fail together.
        Acquires a pessimistic row lock (SELECT ... FOR UPDATE) on the account row
        to eliminate race conditions under concurrent requests.

        Args:
            account_id: Identifier of the target account.
            data: Validated transaction payload (type, amount_minor, description, reference_id).

        Returns:
            TransactionResponse with recorded ledger entry and balance snapshot.

        Raises:
            AccountNotFoundError: If the account does not exist.
            InsufficientFundsError: If a debit exceeds available balance.
            DuplicateReferenceError: If reference_id has already been used for this account.
            InvalidAmountError: If amount is non-positive or resulting balance overflows.
        """
        try:
            async with transaction_scope(self.session):
                # 1. Acquire pessimistic row lock on account to serialize concurrent operations
                stmt = select(Account).where(Account.id == account_id).with_for_update()
                result = await self.session.execute(stmt)
                account = result.scalar_one_or_none()
                if account is None:
                    raise AccountNotFoundError()

                # Validate amount bounds
                if data.amount_minor <= 0 or data.amount_minor > MAX_BIGINT:
                    raise InvalidAmountError()

                # 2. Check reference_id uniqueness for this account
                ref_stmt = select(Transaction.id).where(
                    Transaction.account_id == account_id,
                    Transaction.reference_id == data.reference_id,
                )
                existing_ref = await self.session.execute(ref_stmt)
                if existing_ref.scalar_one_or_none() is not None:
                    raise DuplicateReferenceError()

                # 3. Check debit balance sufficiency and compute new balance
                if data.type == "debit":
                    if account.balance_minor < data.amount_minor:
                        raise InsufficientFundsError()
                    new_balance = account.balance_minor - data.amount_minor
                else:
                    new_balance = account.balance_minor + data.amount_minor
                    if new_balance > MAX_BIGINT:
                        raise InvalidAmountError(
                            "Resulting balance exceeds maximum supported limit."
                        )

                # 4. Update account balance
                account.balance_minor = new_balance

                # 5. Insert immutable transaction ledger record
                tx = Transaction(
                    account_id=account_id,
                    type=data.type,
                    amount_minor=data.amount_minor,
                    balance_after_minor=new_balance,
                    description=data.description,
                    reference_id=data.reference_id,
                )
                self.session.add(tx)
                await self.session.flush()
                await self.session.refresh(tx)

            return TransactionResponse.model_validate(tx)
        except IntegrityError as exc:
            msg = str(exc).lower()
            if (
                "uq_transactions_account_reference" in msg
                or "uq_account_reference" in msg
                or "unique" in msg
            ):
                raise DuplicateReferenceError() from exc
            if "accounts_balance_cents_non_negative" in msg or "balance_non_negative" in msg:
                raise InsufficientFundsError() from exc
            if "amount_positive" in msg:
                raise InvalidAmountError() from exc
            raise
        except DataError as exc:
            msg = str(exc).lower()
            if "out of range" in msg or "numericvalueoutofrange" in msg:
                raise InvalidAmountError("Monetary value exceeds supported limits.") from exc
            raise
        except DBAPIError as exc:
            msg = str(exc).lower()
            if "invalid byte sequence" in msg or "0x00" in msg:
                raise InvalidAmountError(
                    "Input contains invalid byte sequence or encoding."
                ) from exc
            raise

    async def get_history(
        self,
        account_id: uuid.UUID,
        type_filter: TransactionType | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> TransactionHistoryResponse:
        """Retrieves paginated, immutable transaction history for an account in latest-first order.

        Args:
            account_id: Identifier of the target account.
            type_filter: Optional filter by 'credit' or 'debit'.
            page: 1-based page index.
            limit: Page size limit (clamped between 1 and 100).

        Returns:
            TransactionHistoryResponse containing ledger entries and pagination metadata.

        Raises:
            AccountNotFoundError: If the account does not exist.
        """
        # 1. Verify target account exists
        acc_stmt = select(Account.id).where(Account.id == account_id)
        acc_res = await self.session.execute(acc_stmt)
        if acc_res.scalar_one_or_none() is None:
            raise AccountNotFoundError()

        # 2. Build filter conditions
        conditions = [Transaction.account_id == account_id]
        if type_filter is not None:
            conditions.append(Transaction.type == type_filter)

        # 3. Efficient total count in database respecting filters
        count_stmt = select(func.count()).select_from(Transaction).where(*conditions)
        count_res = await self.session.execute(count_stmt)
        total_records = count_res.scalar() or 0

        # 4. Compute pagination bounds
        offset = (page - 1) * limit
        total_pages = math.ceil(total_records / limit) if total_records > 0 else 0

        # 5. Fetch paginated records using composite indexes with deterministic tie-breaker
        items_stmt = (
            select(Transaction)
            .where(*conditions)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .offset(offset)
            .limit(limit)
        )
        items_res = await self.session.execute(items_stmt)
        transactions = items_res.scalars().all()

        return TransactionHistoryResponse(
            items=[TransactionResponse.model_validate(tx) for tx in transactions],
            pagination=PaginationMetadata(
                current_page=page,
                page_size=limit,
                total_records=total_records,
                total_pages=total_pages,
            ),
        )
