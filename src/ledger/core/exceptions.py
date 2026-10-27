"""Domain exceptions for the Transactional Ledger API.

Establishes a unified, predictable exception hierarchy mapping domain-level
financial failures directly to stable error codes and HTTP statuses.
"""


class LedgerError(Exception):
    """Base exception for all domain errors within the ledger system."""

    code: str = "INTERNAL_SERVER_ERROR"
    http_status_code: int = 500
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.__class__.message
        super().__init__(self.message)


class AccountNotFoundError(LedgerError):
    """Raised when an operation targets a non-existent account ID."""

    code = "ACCOUNT_NOT_FOUND"
    http_status_code = 404
    message = "The requested account does not exist."


class InsufficientFundsError(LedgerError):
    """Raised when an account lacks sufficient balance for a debit operation."""

    code = "INSUFFICIENT_FUNDS"
    http_status_code = 422
    message = "Account has insufficient balance to complete the requested debit."


class DuplicateReferenceError(LedgerError):
    """Raised when a transaction with the same reference_id already exists for the account."""

    code = "DUPLICATE_REFERENCE"
    http_status_code = 409
    message = "A transaction with this reference_id already exists for this account."


class InvalidAmountError(LedgerError):
    """Raised when a monetary amount is zero, negative, or not a positive integer."""

    code = "INVALID_AMOUNT"
    http_status_code = 422
    message = "Transaction amount must be a positive integer in minor units."


class InvalidTransactionTypeError(LedgerError):
    """Raised when transaction type is neither 'credit' nor 'debit'."""

    code = "INVALID_TRANSACTION_TYPE"
    http_status_code = 422
    message = "Transaction type must be strictly 'credit' or 'debit'."


class TransferSameAccountError(LedgerError, ValueError):
    """Raised when attempting a transfer between identical source and destination accounts."""

    code = "TRANSFER_SAME_ACCOUNT"
    http_status_code = 422
    message = "Source and destination accounts cannot be identical."


class CurrencyMismatchError(LedgerError):
    """Raised when transfer accounts have incompatible or unsupported currencies."""

    code = "CURRENCY_MISMATCH"
    http_status_code = 422
    message = "Transfers are only supported between accounts denominated in INR."


class ConcurrencyConflictError(LedgerError):
    """Raised when a deadlock or serialization conflict is detected."""

    code = "CONCURRENCY_CONFLICT"
    http_status_code = 503
    message = "The transaction encountered a concurrency conflict. Please retry."
