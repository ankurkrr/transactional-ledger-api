# Transaction Ledger API — REST API Contract Specification

**Document Version:** 1.1.0  
**Status:** DRAFT — AWAITING REVIEW (Phase 0 Revised)  
**Author:** Principal Backend Engineer & Application Security Architect  
**OpenAPI Standard:** OpenAPI 3.1.0 Compatible  
**Base URL:** `/api/v1`  

---

## 1. Global Conventions & Standards

1. **Protocol:** HTTPS (Transport Layer Security TLS 1.3 enforced in production).
2. **Payload Format:** JSON (`application/json; charset=utf-8`).
3. **Monetary Representation & Currency (Option B):**
   - **Public API Contract:** All public request amounts and response balances are denominated in **INR Rupees** formatted as strings.
     - **Request inputs:** Monetary values must be supplied as decimal strings (e.g., `"1.10"`, `"450.30"`, `"5000.00"`). JSON numbers (e.g., `1.10`), booleans, nulls, negative amounts, zero, malformed strings, strings with more than two decimal places, and non-finite values (NaN, Infinity) are strictly rejected with HTTP 422.
     - **Response outputs:** Monetary amounts and balances are returned as fixed two-decimal-place strings (e.g., `"1.10"`, `"450.30"`, `"5000.00"`, `"0.00"`). Floating-point values are never returned.
   - **Internal Persistence & Arithmetic:** The database and internal domain logic continue to operate exclusively on **64-bit signed integer paise (`BIGINT`)** (`amount_minor`, `balance_minor`, `balance_after_minor`). Exactly 1 INR = 100 paise. Safe exact conversion occurs strictly at the public API boundary using Python's `Decimal` without binary floating-point arithmetic.
   - **Currency:** The system is single-currency `INR`. Mismatched or unsupported currencies are rejected (`CURRENCY_MISMATCH`).
4. **Identifier Format:** RFC 4122 UUIDv4 strings (e.g., `3fa85f64-5717-4562-b3fc-2c963f66afa6`).
5. **Timestamps:** ISO 8601 UTC extended format (e.g., `2026-10-27T02:50:00.000000Z`).
6. **Unified Error Envelope:** All 4xx and 5xx responses strictly adhere to a single documented error schema.
7. **Access Control Assumption:** For local assessment evaluation, endpoints operate in evaluation mode (`ALLOW_UNAUTHENTICATED_ACCESS=true`). In production, this mode is blocked at startup, requiring explicit authentication.

---

## 2. Global Schemas

### 2.1 Standard Error Response Schema
```json
{
  "error": {
    "code": "INSUFFICIENT_FUNDS",
    "message": "Account has insufficient balance to complete the requested debit.",
    "details": [],
    "request_id": "req_01j7bca3e8f8v9a0b1c2d3e4f5",
    "timestamp": "2026-10-27T02:50:00.000000Z"
  }
}
```

### 2.2 Pagination Query Parameters
| Parameter | Type | In | Required | Default | Bounds | Description |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `page` | `integer` | query | No | `1` | `page >= 1` | 1-based page index. |
| `limit` | `integer` | query | No | `20` | `1 <= limit <= 100` | Items per page (clamped to max 100). |

### 2.3 Pagination Metadata Envelope
```json
{
  "current_page": 1,
  "page_size": 20,
  "total_records": 142,
  "total_pages": 8
}
```

---

## 3. API Endpoints Specification

### 3.1 Create Account
Create a new ledger account. Initial balance is system-managed and initialized to zero.

- **Method & Path:** `POST /api/v1/accounts`
- **Success Status:** `201 Created`
- **Relevant Financial Invariants:** Invariant 1 (Starts with 0 balance), Invariant 2 (Client cannot choose initial balance).

#### Request Body
```json
{
  "name": "Jane Doe"
}
```
*Validation Rules:*
- `name`: String, non-empty, stripped length between 1 and 255 characters (`min_length=1, max_length=255`), no null bytes.
- Note: Any client-supplied `balance` or `balance_minor` field is forbidden (`extra='forbid'`).

#### Success Response (`201 Created`)
```json
{
  "id": "7b045112-9c3a-442f-8da2-9b2f6efba221",
  "name": "Jane Doe",
  "balance": "0.00",
  "currency": "INR",
  "created_at": "2026-10-27T02:50:00.000000Z",
  "updated_at": "2026-10-27T02:50:00.000000Z"
}
```

#### Possible Error Statuses
- `422 Unprocessable Entity`: Request body is malformed, missing `name`, or contains whitespace-only name (`VALIDATION_ERROR`).

---

### 3.2 Get Account Balance
Retrieve the current reconciled balance for a specified account.

- **Method & Path:** `GET /api/v1/accounts/{account_id}/balance`
- **Success Status:** `200 OK`
- **Relevant Financial Invariants:** Invariant 10 (Stored balance reflects all valid ledger transactions).

#### Path Parameters
- `account_id` (UUIDv4): Unique identifier of the account.

#### Success Response (`200 OK`)
```json
{
  "account_id": "7b045112-9c3a-442f-8da2-9b2f6efba221",
  "balance": "150.00",
  "currency": "INR",
  "updated_at": "2026-10-27T02:52:10.000000Z"
}
```

#### Possible Error Statuses
- `404 Not Found`: Account with specified ID does not exist (`ACCOUNT_NOT_FOUND`).
- `422 Unprocessable Entity`: Invalid UUID format provided in path (`VALIDATION_ERROR`).

---

### 3.3 Add Transaction (Credit or Debit)
Post a credit or debit transaction to an account. Executes balance update and ledger entry atomically.

- **Method & Path:** `POST /api/v1/accounts/{account_id}/transactions`
- **Success Status:** `201 Created`
- **Relevant Financial Invariants:**
  - Invariant 3 (Amount must be positive).
  - Invariant 4 (Rejected transaction does not change balance).
  - Invariant 5 & 6 (Balance update and ledger record succeed together atomically).
  - Invariant 7 (Debit must not overdraw account).
  - Invariant 8 (Per-account reference uniqueness).
  - Invariant 9 (Same reference permitted on different accounts).

#### Path Parameters
- `account_id` (UUIDv4): Unique identifier of the target account.

#### Request Body
```json
{
  "type": "credit",
  "amount": "50.00",
  "description": "Consulting invoice settlement #1024",
  "reference_id": "REF-INV-2026-001"
}
```
*Validation Rules:*
- `type`: String, must be strictly `'credit'` or `'debit'` (case-sensitive).
- `amount`: Decimal string in INR rupees (e.g. `"450.30"`, `"1.10"`). Must be strictly positive, at most 2 decimal places, and within PostgreSQL signed 64-bit BIGINT paise limit. Numbers, booleans, negatives, zero, and malformed strings are rejected.
- `description`: String, length between 1 and 1000 characters, no null bytes.
- `reference_id`: String, length between 1 and 128 characters, unique within this account, no null bytes.

#### Success Response (`201 Created`)
```json
{
  "id": "31b26c04-f58c-4a37-b4d6-c9586144e512",
  "account_id": "7b045112-9c3a-442f-8da2-9b2f6efba221",
  "type": "credit",
  "amount": "50.00",
  "balance_after": "150.00",
  "currency": "INR",
  "description": "Consulting invoice settlement #1024",
  "reference_id": "REF-INV-2026-001",
  "created_at": "2026-10-27T02:53:00.000000Z"
}
```

#### Duplicate-Reference Conflict Response (`409 Conflict`)
If a transaction with the same `reference_id` is submitted again for the same account, the API returns:
```json
{
  "error": {
    "code": "DUPLICATE_REFERENCE",
    "message": "A transaction with this reference_id already exists for this account.",
    "details": [],
    "request_id": "req_01j7bca3e8f8v9a0b1c2d3e4f5",
    "timestamp": "2026-10-27T02:53:15.000000Z"
  }
}
```
*Design Note on Security:* The 409 response intentionally omits internal details (such as the existing transaction ID, timestamp, or amount) to avoid leaking financial history across callers in unauthenticated or shared environments.

#### Other Possible Error Statuses
- `404 Not Found`: Target account does not exist (`ACCOUNT_NOT_FOUND`).
- `422 Unprocessable Entity`:
  - Insufficient account balance for a debit transaction (`INSUFFICIENT_FUNDS`).
  - Non-positive or invalid amount (`INVALID_AMOUNT`).
  - Invalid transaction type (`INVALID_TRANSACTION_TYPE`).
  - Validation failures on description or reference (`VALIDATION_ERROR`).

---

### 3.4 Get Transaction History
Retrieve the chronological transaction ledger for an account, with optional type filtering and pagination.

- **Method & Path:** `GET /api/v1/accounts/{account_id}/transactions`
- **Success Status:** `200 OK`
- **Sorting Policy:** Strict newest-first ordering: `ORDER BY created_at DESC, id DESC`.
  - *Secondary Sort Key Rationale:* `id DESC` serves as a stable tie-breaker, guaranteeing fully deterministic pagination even when transactions share identical timestamps.

#### Path Parameters
- `account_id` (UUIDv4): Unique identifier of the target account.

#### Query Parameters
- `type` (optional): Filter by transaction type: `'credit'` or `'debit'`. If omitted, both types are returned.
- `page` (optional, default `1`): 1-based page number.
- `limit` (optional, default `20`, max `100`): Page size.

#### Success Response (`200 OK`)
```json
{
  "items": [
    {
      "id": "31b26c04-f58c-4a37-b4d6-c9586144e512",
      "account_id": "7b045112-9c3a-442f-8da2-9b2f6efba221",
      "type": "credit",
      "amount": "50.00",
      "balance_after": "150.00",
      "currency": "INR",
      "description": "Consulting invoice settlement #1024",
      "reference_id": "REF-INV-2026-001",
      "created_at": "2026-10-27T02:53:00.000000Z"
    },
    {
      "id": "18f9e023-e11a-4289-9a77-a8d29b114300",
      "account_id": "7b045112-9c3a-442f-8da2-9b2f6efba221",
      "type": "debit",
      "amount": "20.00",
      "balance_after": "100.00",
      "currency": "INR",
      "description": "Cloud hosting renewal",
      "reference_id": "REF-AWS-2026-09",
      "created_at": "2026-10-27T02:51:30.000000Z"
    }
  ],
  "pagination": {
    "current_page": 1,
    "page_size": 20,
    "total_records": 2,
    "total_pages": 1
  }
}
```

#### Possible Error Statuses
- `404 Not Found`: Account does not exist (`ACCOUNT_NOT_FOUND`).
- `422 Unprocessable Entity`: Invalid query parameters (e.g., `page < 1`, `limit > 100`, invalid type filter) (`VALIDATION_ERROR`).

---

### 3.5 Bonus: Money Transfer Between Accounts
Atomically transfers funds between two distinct accounts: debited from source and credited to destination.

- **Method & Path:** `POST /api/v1/transfers`
- **Success Status:** `201 Created`
- **Relevant Financial Invariants:**
  - Invariant 7 (Source account must not overdraw).
  - Invariant 12 (Atomicity: Debit and credit succeed or fail as a single unit).
  - Invariant 13 (Deadlock-risk reduction via deterministic ascending UUID locking).

#### Request Body
```json
{
  "source_account_id": "7b045112-9c3a-442f-8da2-9b2f6efba221",
  "destination_account_id": "9ca21f64-1111-4562-b3fc-2c963f66abbb",
  "amount": "30.00",
  "description": "Peer-to-peer reimbursement",
  "reference_id": "XFER-2026-9901"
}
```
*Validation Rules:*
- `source_account_id` != `destination_account_id` (Cannot transfer to same account).
- `amount`: Decimal string in INR rupees (e.g. `"450.30"`). Must be strictly positive, at most 2 decimal places, and within PostgreSQL signed 64-bit BIGINT paise limit.
- `reference_id`: String (1..128 chars), globally unique across transfers, no null bytes.

#### Success Response (`201 Created`)
```json
{
  "transfer_id": "55c41f64-4444-4562-b3fc-2c963f66a000",
  "source_account_id": "7b045112-9c3a-442f-8da2-9b2f6efba221",
  "destination_account_id": "9ca21f64-1111-4562-b3fc-2c963f66abbb",
  "amount": "30.00",
  "currency": "INR",
  "status": "completed",
  "reference_id": "XFER-2026-9901",
  "description": "Peer-to-peer reimbursement",
  "created_at": "2026-10-27T02:54:00.000000Z"
}
```

#### Possible Error Statuses
- `404 Not Found`: Either source account or destination account does not exist (`ACCOUNT_NOT_FOUND`).
- `409 Conflict`: Transfer with this `reference_id` already exists (`DUPLICATE_REFERENCE`).
- `422 Unprocessable Entity`:
  - Source and destination account IDs are identical (`TRANSFER_SAME_ACCOUNT`).
  - Source account has insufficient funds (`INSUFFICIENT_FUNDS`).
  - Amount <= 0 or invalid decimal (`INVALID_AMOUNT` / `VALIDATION_ERROR`).
  - Mismatched or non-INR currencies (`CURRENCY_MISMATCH`).
