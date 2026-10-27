# Transaction Ledger API — Centralized Error & Exception Design

**Document Version:** 1.1.0  
**Status:** DRAFT — AWAITING REVIEW (Phase 0 Revised)  
**Author:** Principal Backend Engineer & Application Security Architect  

---

## 1. Core Principles of Financial Error Handling

In financial ledger engineering, failure handling is a critical correctness boundary:

1. **Zero Leaked Internal State:** Never expose stack traces, SQL syntax, table/constraint names, internal file paths, or raw payload dumps in HTTP responses.
2. **Explicit Rollback at Transaction Boundary:** Database rollback must be handled explicitly by the service transaction boundary (`await session.rollback()`). It must **never** be assumed that an outer HTTP exception handler will automatically or safely roll back an active session.
3. **Preserve Original Tracebacks via Bare `raise`:** The transaction manager context must re-raise exceptions using bare `raise` so that the original stack trace is preserved intact for server-side diagnostic logging.
4. **Ambiguous Outcomes from Write Connection Drops:** If a connection fails or times out while awaiting a `COMMIT` acknowledgment, the outcome is ambiguous (the transaction may or may not have committed). The system must never assume rollback, and clients must not blindly retry financial writes.
5. **Deterministic Error Taxonomy:** Every error returns a stable, machine-readable `code` (e.g., `INSUFFICIENT_FUNDS`), an RFC 9110 compliant HTTP status, and a sanitized, safe message.
6. **No Silent Swallowing:** Never swallow exceptions without structured diagnostic logging and proper propagation.
7. **Correlation without Disclosure:** Every error response contains a unique `request_id` allowing instant correlation with internal server logs while preventing sensitive data disclosure.

---

## 2. Standardized Error Envelope Schema

All errors (client and server) return a single, unified JSON response structure:

```json
{
  "error": {
    "code": "STRING",
    "message": "STRING",
    "details": [],
    "request_id": "STRING",
    "timestamp": "ISO-8601 UTC"
  }
}
```

### Schema Field Definitions
- `code` (string, required): A stable, SCREAMING_SNAKE_CASE machine-readable error token.
- `message` (string, required): A clear, sanitized explanation of the failure.
- `details` (array, required): Granular field-level errors (e.g., for schema validation). Intentionally kept empty (`[]`) for security-sensitive errors like `DUPLICATE_REFERENCE` to prevent data leakage.
- `request_id` (string, required): Correlation ID generated per request (e.g., `req_01j7bca3e8f8v9a...`).
- `timestamp` (string, required): UTC timestamp when the failure occurred.

---

## 3. Comprehensive Error Taxonomy & Classification Matrix

| Error Category | Internal Domain Exception | HTTP Status | Stable Error Code | Safe Client Message | Logging Level & Behavior | DB Rollback Handled By | Retry Safe? |
| :--- | :--- | :---: | :--- | :--- | :--- | :---: | :---: |
| **Request Parsing & Schema Validation** | `RequestValidationError` (Pydantic / FastAPI) | `422 Unprocessable Entity` | `VALIDATION_ERROR` | "Request body or query parameters failed validation." | `INFO`: Log invalid fields and constraints; sanitize inputs. | N/A (Pre-transaction) | No (Payload must be corrected) |
| **Missing Account** | `AccountNotFoundError` | `404 Not Found` | `ACCOUNT_NOT_FOUND` | "The requested account does not exist." | `INFO`: Log `account_id` and calling endpoint. | Transaction Boundary | No |
| **Insufficient Balance** | `InsufficientFundsError` | `422 Unprocessable Entity` | `INSUFFICIENT_FUNDS` | "Account has insufficient balance to complete the debit." | `WARNING`: Log `account_id`, `requested_amount`, `available_balance`. | Transaction Boundary | No (Requires credit first) |
| **Duplicate Reference Conflict** | `DuplicateReferenceError` | `409 Conflict` | `DUPLICATE_REFERENCE` | "A transaction with this reference_id already exists for this account." | `WARNING`: Log `account_id`, `reference_id`. | Transaction Boundary | No (Reconcile using reference) |
| **Negative / Invalid Amount** | `InvalidAmountError` | `422 Unprocessable Entity` | `INVALID_AMOUNT` | "Transaction amount must be a positive integer in minor units." | `INFO`: Log rejected amount value. | Transaction Boundary | No |
| **Invalid Transaction Type** | `InvalidTransactionTypeError` | `422 Unprocessable Entity` | `INVALID_TRANSACTION_TYPE` | "Transaction type must be strictly 'credit' or 'debit'." | `INFO`: Log invalid string received. | N/A (Pre-transaction) | No |
| **Self Transfer Prohibited** | `TransferSameAccountError` | `422 Unprocessable Entity` | `TRANSFER_SAME_ACCOUNT` | "Source and destination accounts cannot be identical." | `INFO`: Log account ID. | N/A (Pre-transaction) | No |
| **Authentication Failure** | `AuthenticationFailedError` | `401 Unauthorized` | `AUTHENTICATION_REQUIRED` | "Valid authentication credentials are required." | `WARNING`: Log IP, path. (Never log raw credentials). | N/A (Pre-transaction) | Yes (With valid credentials) |
| **Authorization / BOLA Violation** | `AuthorizationFailedError` | `403 Forbidden` | `FORBIDDEN` | "You do not have permission to access or modify this account." | `SECURITY WARNING`: Log caller identity, attempted account ID, IP. | N/A (Pre-transaction) | No |
| **Database Constraint Conflict (Race)** | `SQLAlchemy.IntegrityError` (`uq_account_reference`) | `409 Conflict` | `DUPLICATE_REFERENCE` | "A transaction with this reference_id already exists for this account." | `INFO`: Log handled race condition. | Transaction Boundary | No |
| **Ambiguous Write Timeout / Disconnect** | `DBAPIError` / `OperationalError` during commit | `503 Service Unavailable` | `SERVICE_UNAVAILABLE` | "Database connection timed out during commit. Outcome is indeterminate; reconcile using reference_id." | `ERROR`: Full exception traceback logged with DB pool stats. | Transaction Boundary (Connection recycled) | **No blind retry.** Client must reconcile reference. |
| **Deadlock Detected** | `DBAPIError` (SQLSTATE `40P01`) | `503 Service Unavailable` | `CONCURRENCY_CONFLICT` | "The transaction encountered a concurrency conflict. Please retry." | `WARNING`: Log conflicting transaction signatures. | Transaction Boundary | Yes (With jittered backoff) |
| **Unexpected Internal Error** | `Exception` (catch-all) | `500 Internal Server Error` | `INTERNAL_SERVER_ERROR` | "An unexpected error occurred. Please contact support with request_id." | `CRITICAL`: Full traceback logged with correlation `request_id`. | Transaction Boundary | No (Until root cause investigated) |

---

## 4. Architectural Exception & Rollback Lifecycle

```mermaid
flowchart TD
    Req[Incoming HTTP Request] --> MW[RequestID & Logging Middleware]
    MW --> Route[FastAPI Route Handler]
    Route --> SvcTx[Service Transaction Scope]
    
    subgraph Transaction_Boundary [Explicit Transaction Scope (Service Layer)]
        SvcTx --> Exec[Execute Queries & Operations]
        Exec -- Error Raised --> Catch[except Exception:]
        Catch --> Rollback[await session.rollback()]
        Rollback --> BareRaise[bare raise]
    end
    
    BareRaise --> Handlers[Central FastAPI Exception Handlers]
    Handlers --> Sanitizer[Sanitize Details & Strip Internals]
    Sanitizer --> Resp[Unified JSON Error Response with request_id]
```

### 4.1 Transaction Scope Implementation Pattern
```python
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession

@asynccontextmanager
async def transaction_scope(session: AsyncSession):
    """
    Manages explicit database transaction lifecycle.
    Rolls back immediately upon any exception and preserves the original traceback.
    """
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise  # Bare raise preserves original traceback
```

---

## 5. Diagnostic Logging vs. Client Information Disclosure

### Client Response Sanitization
- Clients receive only: `code`, sanitized `message`, client-correctable `details`, `request_id`, and `timestamp`.
- **Validation Errors:** The handler extracts Pydantic validation failures and strips raw input payloads, returning only safe field names and validation rules (e.g., `{"field": "amount_minor", "issue": "Value must be greater than 0"}`).
- **Zero Database Internals:** Table names, column names, constraint names, and SQL statements are never sent to the client.

### Server-Side Structured Logging
- Logs are emitted in structured JSON.
- **Header & Secret Scrubbing:** An automated logging filter scrubs authorization tokens, passwords, and sensitive credentials.
- **Traceback Preservation:** Full tracebacks are logged strictly internally at `ERROR` or `CRITICAL` levels along with `request_id`.
