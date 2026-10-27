# Transactional Ledger API — Comprehensive API Documentation

**Document Title:** Standalone REST API Reference & Specification  
**API Version:** `v1`  
**OpenAPI Specification:** 3.1.0 Compatible  
**Base URL:** `http://localhost:8000/api/v1` (or `http://localhost:8001/api/v1`)  
**Interactive Swagger UI:** `http://localhost:8000/docs` (or `http://localhost:8001/docs`)  
**ReDoc Reference:** `http://localhost:8000/redoc` (or `http://localhost:8001/redoc`)  
**OpenAPI JSON:** `http://localhost:8000/openapi.json` (or `http://localhost:8001/openapi.json`)

---

## Table of Contents
1. [Global Standards & Conventions](#1-global-standards--conventions)
2. [Monetary Representation (Option B Contract)](#2-monetary-representation-option-b-contract)
3. [Global Error Envelope & Status Codes](#3-global-error-envelope--status-codes)
4. [API Endpoints Quick Reference](#4-api-endpoints-quick-reference)
5. [Detailed Endpoint Specifications](#5-detailed-endpoint-specifications)
   - [5.1 Create Account](#51-create-account)
   - [5.2 Get Account Balance](#52-get-account-balance)
   - [5.3 Get Account Details](#53-get-account-details)
   - [5.4 Add Transaction (Credit / Debit)](#54-add-transaction-credit--debit)
   - [5.5 Get Transaction History (Pagination & Filter)](#55-get-transaction-history-pagination--filter)
   - [5.6 Cross-Account Money Transfer](#56-cross-account-money-transfer)
   - [5.7 Health Check Probe](#57-health-check-probe)
6. [Idempotency & Concurrency Model](#6-idempotency--concurrency-model)
7. [Comprehensive Curl Test Suite](#7-comprehensive-curl-test-suite)

---

## 1. Global Standards & Conventions

| Standard | Specification | Description |
|---|---|---|
| **Protocol** | HTTP / 1.1 & HTTPS | RESTful JSON over HTTP. TLS 1.3 in production environments. |
| **Content-Type** | `application/json` | All request bodies and response bodies are JSON formatted in UTF-8. |
| **Identifier Format** | RFC 4122 UUIDv4 | Identifiers (`id`, `account_id`, `transfer_id`) are 36-character hexadecimal UUID strings (e.g. `2f309b73-855f-432b-ad27-ae62c3c677c9`). |
| **Timestamp Format** | ISO 8601 UTC | Formatted with microsecond precision and Zulu timezone indicator: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (e.g. `2026-10-28T01:05:00.123456Z`). |
| **Request Correlation** | `X-Request-ID` | Every request is assigned a unique tracking identifier (`req_...`) returned in the `X-Request-ID` response header and inside the error body. |
| **Evaluation Access** | Anonymous (Dev Mode) | For assessment evaluation, `ALLOW_UNAUTHENTICATED_ACCESS=true` allows requests without auth headers. In production, a boot-time safety gate strictly blocks startup if unauthenticated access is enabled. |

---

## 2. Monetary Representation (Option B Contract)

The application implements **Option B**: Public API inputs and outputs are formatted as decimal strings in Indian Rupees (INR), while the database and business logic compute exclusively on integer paise.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          MONETARY FLOW ARCHITECTURE                         │
│                                                                             │
│   Public Client                  API Boundary              PostgreSQL       │
│                                                                             │
│   [ "450.30" ] ──────────────►  Decimal("450.30")                           │
│   (Rupee Decimal String)              │                                     │
│                                  int(d * 100) ────────► [ 45030 ]           │
│                                                          (BIGINT Paise)     │
│                                                                │            │
│   [ "450.30" ] ◄──────────────  paise_to_rupees() ◄────────────┘            │
│   (Fixed 2-Decimal String)       f"{paise//100}.{paise%100:02d}"            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Exact Conversion Rules
1. **Public Inputs:**
   - Must be a JSON string (e.g. `"450.30"`, `"450.5"`, `"1.10"`, `"5000.00"`).
   - Must match regex: `^\d+(\.\d{1,2})?$`.
   - Converted using Python's `Decimal(v)` without binary floating-point math: `paise = int(Decimal(v) * 100)`.
2. **Public Outputs:**
   - Always formatted as a **fixed two-decimal-place string** (e.g. `"450.30"`, `"450.50"`, `"0.00"`, `"1.10"`).
3. **Internal Storage & Calculations:**
   - Stored in PostgreSQL `BIGINT` columns: `accounts.balance_minor`, `transactions.amount_minor`, `transactions.balance_after_minor`, `transfers.amount_minor`.
   - Stored as integer paise ($1 \text{ INR} = 100 \text{ paise}$).
4. **Input Rejection Rules (HTTP 422 `VALIDATION_ERROR`):**
   - ❌ JSON numeric floats or integers: `450.30` or `100` (must be quoted strings).
   - ❌ More than 2 decimal places: `"450.305"`, `"1.0001"`.
   - ❌ Negative values: `"-10.00"`, `"-0.01"`.
   - ❌ Zero: `"0"`, `"0.00"`.
   - ❌ Empty or whitespace-only strings: `""`, `"   "`, `" 450.30 "`.
   - ❌ Malformed strings or non-numerics: `"abc"`, `"10.5.5"`, `"\x00"`.
   - ❌ Non-finite values: `"NaN"`, `"Infinity"`, `"-Infinity"`.
   - ❌ Values exceeding signed 64-bit integer limit: $> 9,223,372,036,854,775,807$ paise ($> 92,233,720,368,547,758.07$ INR).
5. **Currency Guarantee:**
   - Strictly single-currency ISO 4217 `INR`. Non-INR operations or mismatched currencies return `422` with code `CURRENCY_MISMATCH`.

---

## 3. Global Error Envelope & Status Codes

All 4xx and 5xx responses strictly adhere to the unified JSON error envelope:

```json
{
  "error": {
    "code": "ERROR_CODE_STRING",
    "message": "Human-readable description of what occurred.",
    "details": [
      {
        "field": "field_name",
        "message": "Specific explanation of constraint violation."
      }
    ],
    "request_id": "req_01j7bca3e8f8v9a0b1c2d3e4f5",
    "timestamp": "2026-10-28T01:05:00.123456Z"
  }
}
```

### Standard Error Codes Glossary

| Error Code | HTTP Status | Description |
|---|:---:|---|
| `VALIDATION_ERROR` | `422` | Request body or query parameters failed Pydantic validation (e.g. bad types, missing fields, invalid decimals, null bytes). |
| `ACCOUNT_NOT_FOUND` | `404` | The requested account identifier does not exist in the ledger. |
| `INSUFFICIENT_FUNDS` | `422` | Account balance is lower than the requested debit or transfer amount. |
| `DUPLICATE_REFERENCE` | `409` | A transaction with this `reference_id` already exists for this account, or a transfer with this `reference_id` already exists globally. |
| `TRANSFER_SAME_ACCOUNT` | `422` | Transfer `source_account_id` and `destination_account_id` are identical. |
| `CURRENCY_MISMATCH` | `422` | Account currency is not INR or transfer source and destination currencies differ. |
| `METHOD_NOT_ALLOWED` | `405` | The HTTP method is not supported on this route (e.g. `PUT`, `DELETE` on transactions). |
| `INTERNAL_SERVER_ERROR`| `500` | Unhandled server error. Stack traces are suppressed from client responses. |

---

## 4. API Endpoints Quick Reference

| Method | Endpoint Path | Summary | Success Status | Key Constraints |
|---|---|---|:---:|---|
| `POST` | `/api/v1/accounts` | Create Account | `201 Created` | Name required (1..255 chars). Initial balance always 0.00. |
| `GET` | `/api/v1/accounts/{id}/balance` | Get Account Balance | `200 OK` | Returns current reconciled balance in INR. |
| `GET` | `/api/v1/accounts/{id}` | Get Account Details | `200 OK` | Returns account metadata, timestamps, and balance. |
| `POST` | `/api/v1/accounts/{id}/transactions` | Post Transaction | `201 Created` | Type: `credit` or `debit`. Amount > 0. Unique `reference_id`. |
| `GET` | `/api/v1/accounts/{id}/transactions` | Transaction History | `200 OK` | Paginated (`page`, `limit`). Type filter. Latest-first sorting. |
| `POST` | `/api/v1/transfers` | Transfer Money | `201 Created` | Atomic debit from source & credit to destination. Unique reference. |
| `GET` | `/health` | Health Check | `200 OK` | Diagnostic readiness probe. |

---

## 5. Detailed Endpoint Specifications

---

### 5.1 Create Account
Initializes a new ledger account. Initial balance is system-managed and always zero. Clients are prohibited from supplying an initial balance.

- **Method:** `POST`
- **Path:** `/api/v1/accounts`
- **Request Headers:**
  - `Content-Type: application/json`

#### Request Body Schema
| Field | Type | Required | Description | Constraints |
|---|---|:---:|---|---|
| `name` | `string` | **Yes** | Full name of the account holder. | 1 to 255 non-whitespace chars. No null bytes (`\x00`). |

*Note: Any additional fields (e.g. `balance`, `balance_minor`, `id`, `created_at`) will cause validation rejection (`extra='forbid'`).*

#### Example Request
```json
{
  "name": "Alice Sharma"
}
```

#### Success Response (`201 Created`)
```json
{
  "id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
  "name": "Alice Sharma",
  "balance": "0.00",
  "currency": "INR",
  "created_at": "2026-10-28T01:10:00.000000Z",
  "updated_at": "2026-10-28T01:10:00.000000Z"
}
```

#### Error Responses
- **`422 Unprocessable Content` (Missing Name):**
  ```json
  {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Request body or query parameters failed validation.",
      "details": [{"field": "name", "message": "Field required"}],
      "request_id": "req_84e1b2c45f",
      "timestamp": "2026-10-28T01:10:01.000000Z"
    }
  }
  ```
- **`422 Unprocessable Content` (Whitespace or Null Byte):**
  ```json
  {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Request body or query parameters failed validation.",
      "details": [{"field": "name", "message": "Value error, Account name cannot be empty or whitespace-only."}],
      "request_id": "req_84e1b2c46a",
      "timestamp": "2026-10-28T01:10:02.000000Z"
    }
  }
  ```

---

### 5.2 Get Account Balance
Retrieves the real-time balance for an account. The returned balance matches the reconciled sum of all accepted credits minus all accepted debits.

- **Method:** `GET`
- **Path:** `/api/v1/accounts/{account_id}/balance`

#### Path Parameters
| Parameter | Type | Required | Description |
|---|---|:---:|---|
| `account_id` | `string` (UUIDv4) | **Yes** | RFC 4122 UUID of the target account. |

#### Success Response (`200 OK`)
```json
{
  "account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
  "balance": "450.30",
  "currency": "INR",
  "updated_at": "2026-10-28T01:15:00.000000Z"
}
```

#### Error Responses
- **`404 Not Found` (Account does not exist):**
  ```json
  {
    "error": {
      "code": "ACCOUNT_NOT_FOUND",
      "message": "The requested account does not exist.",
      "details": [],
      "request_id": "req_01j7bca3e8f8",
      "timestamp": "2026-10-28T01:15:01.000000Z"
    }
  }
  ```
- **`422 Unprocessable Content` (Malformed UUID):**
  ```json
  {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Request body or query parameters failed validation.",
      "details": [{"field": "account_id", "message": "Input should be a valid UUID"}],
      "request_id": "req_01j7bca3e8f9",
      "timestamp": "2026-10-28T01:15:02.000000Z"
    }
  }
  ```

---

### 5.3 Get Account Details
Retrieves complete account record, including balance, timestamps, and account holder name.

- **Method:** `GET`
- **Path:** `/api/v1/accounts/{account_id}`

#### Path Parameters
| Parameter | Type | Required | Description |
|---|---|:---:|---|
| `account_id` | `string` (UUIDv4) | **Yes** | RFC 4122 UUID of the target account. |

#### Success Response (`200 OK`)
```json
{
  "id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
  "name": "Alice Sharma",
  "balance": "450.30",
  "currency": "INR",
  "created_at": "2026-10-28T01:10:00.000000Z",
  "updated_at": "2026-10-28T01:15:00.000000Z"
}
```

---

### 5.4 Add Transaction (Credit / Debit)
Executes an atomic financial credit or debit against an account. Updates the account balance and records an immutable ledger entry within a single database transaction.

- **Method:** `POST`
- **Path:** `/api/v1/accounts/{account_id}/transactions`

#### Path Parameters
| Parameter | Type | Required | Description |
|---|---|:---:|---|
| `account_id` | `string` (UUIDv4) | **Yes** | Target account UUID. |

#### Request Body Schema
| Field | Type | Required | Description | Validation Constraints |
|---|---|:---:|---|---|
| `type` | `string` | **Yes** | Direction of transaction. | Strictly `"credit"` or `"debit"`. |
| `amount` | `string` | **Yes** | Monetary amount in INR rupees. | Decimal string matching `^\d+(\.\d{1,2})?$`. Strictly positive. $\le 2$ decimal places. |
| `description` | `string` | **Yes** | Human-readable transaction memo. | Length: 1 to 1000 characters. No null bytes. |
| `reference_id` | `string` | **Yes** | Unique idempotency reference within this account. | Length: 1 to 128 characters. Must be unique per account. |

#### Example Credit Request
```json
{
  "type": "credit",
  "amount": "450.50",
  "description": "Invoice payment #1024",
  "reference_id": "INV-2026-001"
}
```

#### Example Debit Request
```json
{
  "type": "debit",
  "amount": "150.25",
  "description": "Utility bill reimbursement",
  "reference_id": "PAY-2026-002"
}
```

#### Success Response (`201 Created`)
```json
{
  "id": "3a11b5e2-411a-4f51-b883-9b6d5b0a1a3b",
  "account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
  "type": "credit",
  "amount": "450.50",
  "balance_after": "450.50",
  "currency": "INR",
  "description": "Invoice payment #1024",
  "reference_id": "INV-2026-001",
  "transfer_id": null,
  "created_at": "2026-10-28T01:20:00.000000Z"
}
```

#### Error Responses
- **`409 Conflict` (Duplicate Reference ID on Same Account):**
  ```json
  {
    "error": {
      "code": "DUPLICATE_REFERENCE",
      "message": "A transaction with this reference_id already exists for this account.",
      "details": [],
      "request_id": "req_84e1b2c47b",
      "timestamp": "2026-10-28T01:20:05.000000Z"
    }
  }
  ```
  *(Note: Details are kept empty to prevent leaking financial data across unverified callers).*
- **`422 Unprocessable Content` (Insufficient Funds for Debit):**
  ```json
  {
    "error": {
      "code": "INSUFFICIENT_FUNDS",
      "message": "Account has insufficient balance to complete the requested debit.",
      "details": [],
      "request_id": "req_84e1b2c48c",
      "timestamp": "2026-10-28T01:20:10.000000Z"
    }
  }
  ```
- **`422 Unprocessable Content` (Invalid Decimal Precision e.g. `"1.005"`):**
  ```json
  {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Request body or query parameters failed validation.",
      "details": [{"field": "amount", "message": "Value error, Amount must be a valid positive decimal string with at most 2 decimal places."}],
      "request_id": "req_84e1b2c49d",
      "timestamp": "2026-10-28T01:20:12.000000Z"
    }
  }
  ```
- **`404 Not Found` (Account does not exist):**
  ```json
  {
    "error": {
      "code": "ACCOUNT_NOT_FOUND",
      "message": "Target account does not exist.",
      "details": [],
      "request_id": "req_84e1b2c49e",
      "timestamp": "2026-10-28T01:20:15.000000Z"
    }
  }
  ```

---

### 5.5 Get Transaction History (Pagination & Filter)
Retrieves the chronological transaction ledger for an account. Supports type filtering, boundary-checked pagination, and guaranteed latest-first ordering.

- **Method:** `GET`
- **Path:** `/api/v1/accounts/{account_id}/transactions`

#### Query Parameters
| Parameter | Type | Required | Default | Bounds | Description |
|---|---|:---:|:---:|:---:|---|
| `type` | `string` | No | `null` | `"credit"` or `"debit"` | Filters ledger records. If omitted, both directions are returned. |
| `page` | `integer` | No | `1` | $\ge 1$ | 1-based page index. |
| `limit` | `integer` | No | `20` | $1 \le \text{limit} \le 100$ | Number of records per page (clamped to max 100). |

#### Sorting Policy
Results are sorted strictly by `created_at DESC, id DESC`. The secondary sort key (`id DESC`) guarantees stable tie-breaking for transactions created within the same microsecond.

#### Example Request
`GET /api/v1/accounts/2f309b73-855f-432b-ad27-ae62c3c677c9/transactions?page=1&limit=2&type=credit`

#### Success Response (`200 OK`)
```json
{
  "items": [
    {
      "id": "3a11b5e2-411a-4f51-b883-9b6d5b0a1a3b",
      "account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
      "type": "credit",
      "amount": "450.50",
      "balance_after": "450.50",
      "currency": "INR",
      "description": "Invoice payment #1024",
      "reference_id": "INV-2026-001",
      "transfer_id": null,
      "created_at": "2026-10-28T01:20:00.000000Z"
    }
  ],
  "pagination": {
    "current_page": 1,
    "page_size": 2,
    "total_records": 1,
    "total_pages": 1
  }
}
```

#### Error Responses
- **`422 Unprocessable Content` (Invalid Page or Limit):**
  ```json
  {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Request body or query parameters failed validation.",
      "details": [{"field": "page", "message": "Input should be greater than or equal to 1"}],
      "request_id": "req_84e1b2c4af",
      "timestamp": "2026-10-28T01:25:00.000000Z"
    }
  }
  ```

---

### 5.6 Cross-Account Money Transfer
Bonus Feature. Atomically debits the source account and credits the destination account within a single PostgreSQL transaction. Automatically creates one `transfers` audit record and two balanced ledger entries linked via `transfer_id`.

- **Method:** `POST`
- **Path:** `/api/v1/transfers`

#### Request Body Schema
| Field | Type | Required | Description | Constraints |
|---|---|:---:|---|---|
| `source_account_id` | `string` (UUIDv4) | **Yes** | Account debited. | Must exist and differ from destination. |
| `destination_account_id` | `string` (UUIDv4) | **Yes** | Account credited. | Must exist and differ from source. |
| `amount` | `string` | **Yes** | Transfer amount in INR rupees. | Decimal string. Must be strictly positive ($\le 2$ decimals). |
| `description` | `string` | **Yes** | Transfer narrative. | 1 to 1000 characters. |
| `reference_id` | `string` | **Yes** | Globally unique transfer reference. | 1 to 128 characters. Must be globally unique across all transfers. |

#### Example Request
```json
{
  "source_account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
  "destination_account_id": "2e675ff1-0c3c-4890-9bc6-cdf8b8ceaec5",
  "amount": "150.25",
  "description": "Peer-to-peer reimbursement",
  "reference_id": "XFER-2026-9902"
}
```

#### Success Response (`201 Created`)
```json
{
  "transfer_id": "9d8e7f6a-5b4c-3d2e-1f0a-9b8c7d6e5f4a",
  "source_account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
  "destination_account_id": "2e675ff1-0c3c-4890-9bc6-cdf8b8ceaec5",
  "amount": "150.25",
  "currency": "INR",
  "status": "completed",
  "reference_id": "XFER-2026-9902",
  "description": "Peer-to-peer reimbursement",
  "created_at": "2026-10-28T01:30:00.000000Z"
}
```

#### Error Responses
- **`422 Unprocessable Content` (Transfer to Same Account):**
  ```json
  {
    "error": {
      "code": "TRANSFER_SAME_ACCOUNT",
      "message": "Source and destination accounts must be distinct.",
      "details": [],
      "request_id": "req_84e1b2c4bf",
      "timestamp": "2026-10-28T01:30:02.000000Z"
    }
  }
  ```
- **`409 Conflict` (Duplicate Global Transfer Reference):**
  ```json
  {
    "error": {
      "code": "DUPLICATE_REFERENCE",
      "message": "A transfer with this reference_id already exists.",
      "details": [],
      "request_id": "req_84e1b2c4cf",
      "timestamp": "2026-10-28T01:30:04.000000Z"
    }
  }
  ```
- **`422 Unprocessable Content` (Source Has Insufficient Funds):**
  ```json
  {
    "error": {
      "code": "INSUFFICIENT_FUNDS",
      "message": "Account has insufficient balance to complete the requested debit.",
      "details": [],
      "request_id": "req_84e1b2c4df",
      "timestamp": "2026-10-28T01:30:06.000000Z"
    }
  }
  ```
- **`404 Not Found` (Source or Destination Missing):**
  ```json
  {
    "error": {
      "code": "ACCOUNT_NOT_FOUND",
      "message": "The requested account does not exist.",
      "details": [],
      "request_id": "req_84e1b2c4ef",
      "timestamp": "2026-10-28T01:30:08.000000Z"
    }
  }
  ```

---

### 5.7 Health Check Probe
Application readiness and liveness diagnostic probe.

- **Method:** `GET`
- **Path:** `/health`

#### Success Response (`200 OK`)
```json
{
  "status": "pass",
  "version": "0.1.0",
  "environment": "development"
}
```

---

## 6. Idempotency & Concurrency Model

### 1. Per-Account Transaction Idempotency
- **Constraint:** `uq_transactions_account_reference` on `(account_id, reference_id)`.
- **Behavior:** Within a given account, a `reference_id` may only be executed once. Resubmission returns HTTP `409 Conflict`.
- **Multi-Account Reuse:** The same `reference_id` (e.g. `"GLOBAL-REF-01"`) **can** be used across different accounts without conflict, satisfying the assessment requirement.

### 2. Global Transfer Idempotency
- **Constraint:** `uq_transfers_reference_id` on `transfers(reference_id)`.
- **Behavior:** Across all accounts, a transfer `reference_id` is globally unique. Resubmitting an identical transfer reference ID returns HTTP `409 Conflict`.

### 3. Concurrency Protection & Deadlock Prevention
- **Pessimistic Row Locking:** All balance mutations acquire exclusive row locks via `SELECT ... FOR UPDATE`.
- **Deterministic Lock Ordering:** During transfers between Account A and Account B, the system sorts the UUIDs in ascending lexical order:
  $$\text{locks} = \text{sort}([A, B])$$
  Both accounts are locked in this deterministic order regardless of which is source or destination. This mathematically prevents circular wait conditions and eliminates PostgreSQL transaction deadlocks.

---

## 7. Comprehensive Curl Test Suite

The following sequence of `curl` commands can be copied directly into any terminal to test the complete financial lifecycle:

```bash
# Set Base URL (use port 8000 or 8001 depending on your .env)
export BASE_URL="http://localhost:8001/api/v1"

# 1. Check Health Probe
curl -i -X GET "http://localhost:8001/health"

# 2. Create Alice's Account
ALICE_ID=$(curl -s -X POST "$BASE_URL/accounts" \
  -H "Content-Type: application/json" \
  -d '{"name": "Alice Sharma"}' | grep -o '"id":"[^"]*' | cut -d'"' -f4)
echo "Alice ID: $ALICE_ID"

# 3. Create Bob's Account
BOB_ID=$(curl -s -X POST "$BASE_URL/accounts" \
  -H "Content-Type: application/json" \
  -d '{"name": "Bob Verma"}' | grep -o '"id":"[^"]*' | cut -d'"' -f4)
echo "Bob ID: $BOB_ID"

# 4. Credit Alice with ₹450.50
curl -i -X POST "$BASE_URL/accounts/$ALICE_ID/transactions" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "credit",
    "amount": "450.50",
    "description": "Consulting settlement",
    "reference_id": "CURL-TX-001"
  }'

# 5. Check Alice's Balance (Should be 450.50)
curl -i -X GET "$BASE_URL/accounts/$ALICE_ID/balance"

# 6. Transfer ₹150.25 from Alice to Bob
curl -i -X POST "$BASE_URL/transfers" \
  -H "Content-Type: application/json" \
  -d "{
    \"source_account_id\": \"$ALICE_ID\",
    \"destination_account_id\": \"$BOB_ID\",
    \"amount\": \"150.25\",
    \"description\": \"Project split\",
    \"reference_id\": \"CURL-XFER-001\"
  }"

# 7. Check Alice's Updated Balance (Should be 300.25)
curl -s -X GET "$BASE_URL/accounts/$ALICE_ID/balance"

# 8. Check Bob's Updated Balance (Should be 150.25)
curl -s -X GET "$BASE_URL/accounts/$BOB_ID/balance"

# 9. View Alice's Transaction History (Latest-first)
curl -s -X GET "$BASE_URL/accounts/$ALICE_ID/transactions?page=1&limit=10"

# 10. Verify Validation Rejection: Invalid Precision >2 decimals
curl -i -X POST "$BASE_URL/accounts/$ALICE_ID/transactions" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "credit",
    "amount": "1.005",
    "description": "Invalid precision",
    "reference_id": "CURL-INVALID-01"
  }'

# 11. Verify Duplicate Reference Rejection (HTTP 409)
curl -i -X POST "$BASE_URL/accounts/$ALICE_ID/transactions" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "credit",
    "amount": "10.00",
    "description": "Duplicate attempt",
    "reference_id": "CURL-TX-001"
  }'
```
