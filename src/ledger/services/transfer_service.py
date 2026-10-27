"""Service layer interface for atomic cross-account transfers."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
from ledger.models.transaction import Transaction
from ledger.models.transfer import Transfer
from ledger.schemas.transfer import (
    TransferCreateRequest,
    TransferResponse,
)
from ledger.services.transaction_manager import transaction_scope

logger = logging.getLogger(__name__)

MAX_BIGINT: int = 9_223_372_036_854_775_807


class TransferService:
    """Coordinates atomic two-account money transfers with deadlock mitigation.

    Owns multi-account locking order, invariant verification, and dual-ledger entry writes.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute_transfer(self, data: TransferCreateRequest) -> TransferResponse:
        """Executes an atomic transfer between two distinct accounts.

        Acquires pessimistic locks in deterministic order (ascending UUID) to minimize
        deadlock risks, validates source funds, updates both balances, and records matching
        ledger entries.

        Args:
            data: Validated transfer request payload.

        Returns:
            TransferResponse with transfer details and completion status.

        Raises:
            TransferSameAccountError: If source and destination accounts are identical.
            AccountNotFoundError: If either source or destination account does not exist.
            InsufficientFundsError: If source account lacks sufficient funds.
            DuplicateReferenceError: If transfer reference_id already exists.
            InvalidAmountError: If amount is non-positive or destination balance overflows.
            ConcurrencyConflictError: If a database deadlock (SQLSTATE 40P01) is encountered.
        """
        # 1. Reject identical accounts defensively
        if data.source_account_id == data.destination_account_id:
            raise TransferSameAccountError()

        # 2. Reject non-positive amounts or amounts exceeding signed 64-bit BIGINT
        if data.amount_minor <= 0 or data.amount_minor > MAX_BIGINT:
            raise InvalidAmountError()

        try:
            async with transaction_scope(self.session):
                # 3. Deterministic lock acquisition order by UUID ascending (ADR-007)
                first_id, second_id = (
                    (data.source_account_id, data.destination_account_id)
                    if data.source_account_id < data.destination_account_id
                    else (data.destination_account_id, data.source_account_id)
                )

                stmt1 = select(Account).where(Account.id == first_id).with_for_update()
                res1 = await self.session.execute(stmt1)
                acc1 = res1.scalar_one_or_none()

                stmt2 = select(Account).where(Account.id == second_id).with_for_update()
                res2 = await self.session.execute(stmt2)
                acc2 = res2.scalar_one_or_none()

                # 4. Require both accounts to exist
                if acc1 is None or acc2 is None:
                    raise AccountNotFoundError()

                source_account = acc1 if acc1.id == data.source_account_id else acc2
                dest_account = acc2 if acc2.id == data.destination_account_id else acc1

                # 5. Require both accounts to be INR-denominated
                if (
                    source_account.currency != "INR"
                    or dest_account.currency != "INR"
                    or source_account.currency != dest_account.currency
                ):
                    raise CurrencyMismatchError()

                # 6. Check transfer reference uniqueness (global scope across transfers)
                ref_stmt = select(Transfer.id).where(Transfer.reference_id == data.reference_id)
                existing_ref = await self.session.execute(ref_stmt)
                if existing_ref.scalar_one_or_none() is not None:
                    raise DuplicateReferenceError(
                        "A transfer with this reference_id already exists."
                    )

                # 6. Check source funds sufficiency under row lock
                if source_account.balance_minor < data.amount_minor:
                    raise InsufficientFundsError()

                # 7. Check destination balance overflow under row lock
                new_dest_balance = dest_account.balance_minor + data.amount_minor
                if new_dest_balance > MAX_BIGINT:
                    raise InvalidAmountError(
                        "Resulting destination balance exceeds maximum supported limit."
                    )

                # 8. Compute new balances and update account rows
                new_source_balance = source_account.balance_minor - data.amount_minor
                source_account.balance_minor = new_source_balance
                dest_account.balance_minor = new_dest_balance

                # 9. Insert transfer record
                transfer = Transfer(
                    source_account_id=data.source_account_id,
                    destination_account_id=data.destination_account_id,
                    amount_minor=data.amount_minor,
                    reference_id=data.reference_id,
                    description=data.description,
                    status="completed",
                )
                self.session.add(transfer)
                await self.session.flush()

                # 10. Insert two immutable ledger transaction records
                # Leg 1: Debit from source account
                debit_tx = Transaction(
                    account_id=data.source_account_id,
                    type="debit",
                    amount_minor=data.amount_minor,
                    balance_after_minor=new_source_balance,
                    description=data.description,
                    reference_id=data.reference_id,
                    transfer_id=transfer.id,
                )
                self.session.add(debit_tx)

                # Leg 2: Credit to destination account
                credit_tx = Transaction(
                    account_id=data.destination_account_id,
                    type="credit",
                    amount_minor=data.amount_minor,
                    balance_after_minor=new_dest_balance,
                    description=data.description,
                    reference_id=data.reference_id,
                    transfer_id=transfer.id,
                )
                self.session.add(credit_tx)

                await self.session.flush()
                await self.session.refresh(transfer)

            return TransferResponse.model_validate(transfer)
        except IntegrityError as exc:
            msg = str(exc).lower()
            if "uq_transfers_reference" in msg or "uq_account_reference" in msg or "unique" in msg:
                raise DuplicateReferenceError(
                    "A transfer with this reference_id already exists."
                ) from exc
            if "accounts_balance_cents_non_negative" in msg or "balance_non_negative" in msg:
                raise InsufficientFundsError() from exc
            if "amount_positive" in msg or "amount_minor_positive" in msg:
                raise InvalidAmountError() from exc
            if "distinct_accounts" in msg:
                raise TransferSameAccountError() from exc
            if "foreign key" in msg or "fk_" in msg:
                raise AccountNotFoundError() from exc
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
            orig = getattr(exc, "orig", None)
            sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            if sqlstate == "40P01" or "deadlock detected" in str(exc).lower():
                raise ConcurrencyConflictError() from exc
            raise
