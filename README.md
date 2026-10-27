# Transactional Ledger API

A high-integrity transactional ledger backend engineered with Python 3.12+, FastAPI, SQLAlchemy 2.x (async), Alembic, and PostgreSQL 16+.

> **Assessment Submission Status:** Complete & Fully Verified  
> **Automated Test Suite:** 261 tests passing (213 unit + 48 PostgreSQL integration)  
> **Quality Gates:** 100% clean across Ruff (lint & format), Pyright, Bandit, and pip-audit.  
> **API Reference:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md) | **Interactive Docs:** [http://localhost:8000/docs](http://localhost:8000/docs) (or port 8001)

---

## 1. Project Overview & Implemented Capabilities

Built for the Full Stack Developer Assessment, this service provides an immutable, double-entry-style financial ledger with strict concurrency controls, atomic transaction processing, and exact monetary arithmetic.

### Implemented Functionality
- **Account Management (`/api/v1/accounts`):** Create accounts with server-generated UUIDv4 identifiers, client-supplied names, server-managed timestamps, and a guaranteed initial balance of zero.
- **Credit & Debit Transactions (`/api/v1/accounts/{id}/transactions`):** Atomic financial operations that validate amounts, acquire pessimistic row locks (`SELECT ... FOR UPDATE`), update account balances, and persist immutable ledger records in a single database transaction.
- **Balance Inquiries (`/api/v1/accounts/{id}/balance`):** Real-time balance retrieval reconciled against the underlying transaction history.
- **Transaction History & Pagination (`/api/v1/accounts/{id}/transactions`):** Chronological transaction inquiry with credit/debit filtering, boundary-validated pagination (`page`, `limit`), latest-first sorting (`created_at DESC, id DESC`), and pagination metadata.
- **Bonus — Atomic Cross-Account Transfers (`/api/v1/transfers`):** Two-legged money movement between distinct accounts executed within a single ACID transaction. Enforces deterministic lock ordering (`sorted([source_id, dest_id])`) to eliminate deadlocks.
- **Data Integrity & Immutability:** No update or deletion endpoints exist for ledger records. Database-level role privileges strictly revoke `UPDATE` and `DELETE` on transactions from the runtime application role.
- **Idempotency & Duplicate Protection:** Enforces per-account reference uniqueness on transactions (`uq_transactions_account_reference`) and global reference uniqueness on transfers (`uq_transfers_reference_id`), returning `409 Conflict` on duplicates.

---

## 2. Technology Stack

- **Language Runtime:** Python 3.12 / 3.13
- **Web Framework:** FastAPI (v0.115+) & Starlette
- **Data Validation & Settings:** Pydantic v2 (strict validation) & `pydantic-settings`
- **Database Engine:** PostgreSQL 16+
- **Async ORM & Persistence:** SQLAlchemy 2.x (asyncio extension) & `asyncpg`
- **Database Migrations:** Alembic (v1.13+)
- **Testing & HTTP Client:** `pytest` (v9.x), `pytest-asyncio`, `httpx`
- **Code Quality & Static Typing:** Ruff (linter/formatter) & Pyright (standard mode)
- **Security & Dependency Auditing:** Bandit (AST security scanner) & `pip-audit` (OSV CVE scanner)
- **Containerization:** Docker & Docker Compose
- **Package Management:** `uv` & Hatchling build backend

---

## 3. Monetary Representation & Currency Contract (Option B)

The application implements **Option B: Public Decimal INR Rupees with Internal Integer-Paise Storage**:

| Layer | Representation | Examples | Rationale |
|---|---|---|---|
| **Public API Request** | Decimal string in INR rupees | `"450.30"`, `"450.5"`, `"1.10"` | Developer ergonomics; avoids client-side minor unit calculations. |
| **Public API Response** | Fixed 2-decimal string in INR rupees | `"450.30"`, `"0.00"`, `"1.10"` | Guarantees deterministic display without floating-point artifacts. |
| **API Boundary Conversion** | Python `Decimal` (`Decimal(v) * 100`) | Exact integer multiplication | Zero binary floating-point representation drift (no `float`). |
| **Database & Domain Logic** | 64-bit signed integer paise (`BIGINT`) | `45030`, `110`, `0` | Standard relational integer arithmetic; overflow-protected. |

### Validation Rules for Monetary Inputs
- **Accepted Formats:** Strictly positive decimal strings matching `^\d+(\.\d{1,2})?$` (e.g. `"1"`, `"1.1"`, `"1.10"`, `"450.5"`, `"450.50"`).
- **Rejected Inputs (HTTP 422):** JSON numbers (e.g. `450.50` without quotes), booleans, `null`, empty/whitespace strings, negative amounts (`"-10.00"`), zero (`"0.00"`), strings with $>2$ decimal places (`"1.001"`), non-finite values (`NaN`, `Infinity`), and amounts exceeding PostgreSQL signed `BIGINT` max (`92233720368547758.07` INR).
- **Single Currency:** The ledger operates strictly in ISO 4217 `INR`. Non-INR accounts or mismatched transfer currencies are rejected with HTTP 422 (`CURRENCY_MISMATCH`).

---

## 4. Project Layout

```
.
├── docs/                        # Architectural documentation & specifications
│   ├── Full Stack Developer Assessment.pdf  # Original assessment specification
│   ├── requirements.md          # Functional & non-functional requirements matrix
│   ├── architecture.md          # Modular monolith architecture & concurrency design
│   ├── api-contract.md          # Comprehensive REST API contract & JSON schemas
│   ├── error-handling.md        # Unified error responses and HTTP status mappings
│   ├── test-strategy.md         # Test strategy, fixtures, and concurrency testing
│   ├── decisions.md             # Architecture Decision Records (ADR-001 - ADR-010)
│   ├── assumptions.md           # Assumptions, design decisions & limitations (Submission Item 4)
│   └── session-handoff.md       # Implementation and verification history
├── docker/
│   ├── entrypoint.sh            # Container startup script (migrations + uvicorn)
│   └── init-db/
│       └── 01-init.sql          # PostgreSQL database & least-privilege role setup
├── src/
│   └── ledger/
│       ├── __init__.py
│       ├── main.py              # Application factory & lifespan safety gates
│       ├── core/                # Config, database engine, exceptions
│       ├── models/              # SQLAlchemy models (Account, Transaction, Transfer)
│       ├── schemas/             # Pydantic v2 request/response contracts
│       ├── services/            # Domain service layer & transaction management
│       └── api/                 # API routers, dependencies, and health check
├── tests/
│   ├── conftest.py              # Pytest fixtures & isolated database setup
│   ├── unit/                    # Fast in-memory unit tests (schemas, services, config)
│   └── integration/             # Integration tests against PostgreSQL 16
├── alembic/                     # Database migrations
│   ├── versions/
│   │   ├── 6f904906c046_create_initial_schema.py
│   │   └── a17f2231b8d0_revoke_public_create_from_ledger_app.py
│   ├── env.py
│   └── script.py.mako
├── .env.example                 # Safe environment configuration template
├── .gitignore                   # Version control ignore rules
├── docker-compose.yml           # PostgreSQL 16 & API container definitions
├── Dockerfile                   # Multi-stage production container image
├── pyproject.toml               # Pinned dependencies & tool configurations
├── uv.lock                      # Deterministic dependency lockfile
├── API_DOCUMENTATION.md         # Exhaustive standalone REST API specification
└── README.md
```

---

## 5. Environment Configuration

All configuration is managed through environment variables or a local `.env` file created from `.env.example`:

```bash
cp .env.example .env
```

### Configuration Parameters

| Variable | Default in Template | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | Operating environment: `development`, `test`, `staging`, `production`. |
| `ALLOW_UNAUTHENTICATED_ACCESS` | `true` | Allows unauthenticated requests for assessment evaluation. **Blocked at startup in `production`.** |
| `POSTGRES_PORT` | `5432` | Host port mapped to PostgreSQL container. (Use `5434` if 5432 is already occupied locally). |
| `POSTGRES_DB` | `ledger` | Primary database name. |
| `POSTGRES_USER` | `postgres` | Superuser for initial container initialization. |
| `POSTGRES_PASSWORD` | `postgres` | Superuser password for container initialization. |
| `POSTGRES_APP_USER` | `ledger_app` | Restricted runtime application role (DML only, no DDL). |
| `POSTGRES_APP_PASSWORD` | `postgres` | Password for application runtime role. |
| `POSTGRES_MIGRATOR_USER` | `ledger_migrator` | Dedicated migration role (owns schema `public` for DDL). |
| `POSTGRES_MIGRATOR_PASSWORD` | `migrator_password` | Password for migration role. |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Application runtime connection string (uses `ledger_app`). |
| `MIGRATION_DATABASE_URL` | `postgresql+asyncpg://...` | Migration connection string (uses `ledger_migrator`). |
| `TEST_DATABASE_URL` | `postgresql+asyncpg://...` | Automated integration test connection string (`ledger_test`). |
| `SECRET_KEY` | *(dev default)* | Secret key. Must be $\ge 32$ characters in `staging`/`production`. |
| `LOG_LEVEL` | `INFO` | Logging threshold (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `API_PORT` | `8000` | Host port mapped to the FastAPI service. (e.g. `8001` if port 8000 is occupied). |

### Production Safety Gate
Implemented in `src/ledger/core/config.py`:
- If `ENVIRONMENT=production` and `ALLOW_UNAUTHENTICATED_ACCESS=true`, application startup terminates immediately with a fatal configuration error.
- In `staging` or `production`, an insecure default or short `SECRET_KEY` halts application boot.

---

## 6. Setup & Execution Workflows

### Option A: Complete Docker Compose Setup (Standard Evaluation)
Starts PostgreSQL, waits for health readiness, runs Alembic migrations automatically, and starts the API:

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Build and start containers in background
docker compose up -d --build

# 3. Verify container health status
docker compose ps

# 4. View application startup logs
docker compose logs -f api
```

The API is accessible at `http://localhost:${API_PORT:-8000}` (e.g. [http://localhost:8000/docs](http://localhost:8000/docs) or [http://localhost:8001/docs](http://localhost:8001/docs)).

To stop containers without deleting data:
```bash
docker compose down
```

### Option B: Hybrid Setup (Docker PostgreSQL + Local Python Service)
Runs PostgreSQL in Docker and the FastAPI application directly on the host using `uv`:

```bash
# 1. Install dependencies into virtual environment
uv sync --extra dev

# 2. Copy environment template
cp .env.example .env

# 3. Start PostgreSQL container
docker compose up -d postgres

# 4. Apply database migrations
uv run alembic upgrade head

# 5. Launch development API server
uv run uvicorn ledger.main:app --reload --port 8000
```

### Option C: Native PostgreSQL Fallback (No Docker)
If Docker is unavailable, configure `.env` with credentials for an existing PostgreSQL 16+ instance:
```bash
# Apply migrations and launch server
uv run alembic upgrade head
uv run uvicorn ledger.main:app --reload --port 8000
```

---

## 7. Interactive Browser Walkthrough in Swagger UI (Step-by-Step)

Once the backend is started, open your browser and navigate to:  
👉 **`http://localhost:8000/docs`** (or **`http://localhost:8001/docs`** if running on port 8001).

You will see the Swagger UI dashboard with grouped sections: **Accounts**, **Transactions**, **Transfers**, and **Health**. Follow these concrete steps to test every core functionality:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       SWAGGER UI EVALUATION LIFECYCLE                       │
│                                                                             │
│  [Step 1: Create Alice]   [Step 2: Create Bob]   [Step 3: Credit Alice]     │
│   POST /accounts          POST /accounts          POST /accounts/{id}/tx    │
│   Balance: 0.00           Balance: 0.00           Amount: 450.50            │
│          │                       │                       │                  │
│          ▼                       ▼                       ▼                  │
│  [Step 4: Check Balance] ───────────────►  [Step 5: Transfer to Bob]        │
│   GET /accounts/{id}/bal                    POST /transfers                 │
│   Balance: 450.50                           Alice: 300.25 | Bob: 150.25     │
│                                                          │                  │
│                                                          ▼                  │
│                                             [Step 6: History & Filter]      │
│                                              GET /accounts/{id}/tx          │
│                                              Latest-first chronological     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Step 1: Create Account 1 (Alice)
1. In the **Accounts** section, click `POST /api/v1/accounts`.
2. Click the white **"Try it out"** button in the upper-right corner.
3. In the **Request body** text area, enter:
   ```json
   {
     "name": "Alice Sharma"
   }
   ```
4. Click the blue **"Execute"** button.
5. In the **Server response** section below:
   - Verify **Code:** `201 Created`.
   - Copy the generated `id` value from the response body (e.g. `"e83b070b-40dd-47f0-997e-8d14fc5fb599"`).
   - Notice `"balance": "0.00"` and `"currency": "INR"`.

### Step 2: Create Account 2 (Bob)
1. With `POST /api/v1/accounts` still open, update the request body:
   ```json
   {
     "name": "Bob Verma"
   }
   ```
2. Click **"Execute"**.
3. Verify **Code:** `201 Created` and copy Bob's `id` value.

### Step 3: Add a Credit Transaction to Alice
1. Scroll down to the **Transactions** section and click `POST /api/v1/accounts/{account_id}/transactions`.
2. Click **"Try it out"**.
3. In the **`account_id`** field, paste Alice's account ID.
4. In the **Request body** text area, paste:
   ```json
   {
     "type": "credit",
     "amount": "450.50",
     "description": "Invoice settlement #1024",
     "reference_id": "SWAGGER-TX-001"
   }
   ```
5. Click **"Execute"**.
6. Verify the response:
   - **Code:** `201 Created`.
   - **Response Body:** `"amount": "450.50"`, `"balance_after": "450.50"`, `"currency": "INR"`.

### Step 4: Verify Alice's Account Balance
1. Scroll to the **Accounts** section and click `GET /api/v1/accounts/{account_id}/balance`.
2. Click **"Try it out"**.
3. Paste Alice's account ID into the **`account_id`** field.
4. Click **"Execute"**.
5. Verify:
   - **Code:** `200 OK`.
   - **Response Body:** `{"account_id": "...", "balance": "450.50", "currency": "INR", ...}`.

### Step 5: Transfer Money from Alice to Bob (Bonus Feature)
1. Scroll to the **Transfers** section and click `POST /api/v1/transfers`.
2. Click **"Try it out"**.
3. In the **Request body**, paste:
   ```json
   {
     "source_account_id": "<Alice_Account_ID>",
     "destination_account_id": "<Bob_Account_ID>",
     "amount": "150.25",
     "description": "Project reimbursement",
     "reference_id": "SWAGGER-XFER-001"
   }
   ```
   *(Paste Alice's UUID into `source_account_id` and Bob's UUID into `destination_account_id`).*
4. Click **"Execute"**.
5. Verify:
   - **Code:** `201 Created`.
   - **Response Body:** `"amount": "150.25"`, `"status": "completed"`, `"currency": "INR"`.
6. Confirm both account balances:
   - Alice's balance via `GET /api/v1/accounts/{id}/balance`: now **`"300.25"`** (₹450.50 - ₹150.25).
   - Bob's balance via `GET /api/v1/accounts/{id}/balance`: now **`"150.25"`** (₹0.00 + ₹150.25).

### Step 6: View Transaction History with Filtering & Pagination
1. In the **Transactions** section, click `GET /api/v1/accounts/{account_id}/transactions`.
2. Click **"Try it out"**.
3. Enter Alice's account ID.
4. Leave `type` empty (or enter `debit`) and click **"Execute"**.
5. Verify:
   - **Code:** `200 OK`.
   - **Response Body:** Contains Alice's credit (`+450.50`) and transfer debit (`-150.25`) ordered latest-first with pagination envelope:
     ```json
     "pagination": {
       "current_page": 1,
       "page_size": 20,
       "total_records": 2,
       "total_pages": 1
     }
     ```

### Step 7: Test Validation & Security Defenses
Try submitting invalid payloads to observe the standardized error format:
- **Decimal Precision Violation:** In `POST /api/v1/accounts/{account_id}/transactions`, submit `"amount": "1.005"`.  
  👉 Returns **`422 Unprocessable Content`** with `"Amount must be a valid positive decimal string with at most 2 decimal places."`
- **Numeric Float Rejection:** Submit `"amount": 450.50` (unquoted number).  
  👉 Returns **`422 Unprocessable Content`** with `"Amount must be a decimal string."`
- **Overdraft Protection:** Try to debit ₹10,000 from Bob (`"amount": "10000.00"`).  
  👉 Returns **`422 Unprocessable Content`** with code `"INSUFFICIENT_FUNDS"`.
- **Duplicate Reference Rejection:** Submit the same reference ID (`"SWAGGER-TX-001"`) for Alice again.  
  👉 Returns **`409 Conflict`** with code `"DUPLICATE_REFERENCE"`.

---

## 8. Database Migrations & Least-Privilege Architecture

### Database Role Separation (Least Privilege)
The database enforces role separation configured in `docker/init-db/01-init.sql` and Alembic migrations:
- **Migration Role (`ledger_migrator`):** Dedicated DDL role that owns schema `public`. It possesses full permissions to create and alter tables, constraints, and indexes.
- **Application Runtime Role (`ledger_app`):** Strictly restricted runtime DML role. It holds `USAGE` on schema `public` (`CREATE` revoked in migration `a17f2231b8d0`). On tables, it holds only necessary DML permissions (`SELECT, INSERT, UPDATE` on `accounts`; `SELECT, INSERT` on `transactions` and `transfers`). `UPDATE` and `DELETE` on `transactions` are explicitly revoked in PostgreSQL to guarantee immutable ledger history (ADR-006).

### Migration Commands
Alembic manages migrations asynchronously for both fresh setups and upgrades:

```bash
# Inspect current database revision
uv run alembic current

# Apply all pending migrations (schema creation & privilege hardening)
uv run alembic upgrade head

# Verify that database schema is in sync with models (no drift)
uv run alembic check

# Review raw SQL without connecting to a database (offline mode)
uv run alembic upgrade head --sql

# Rollback last migration
uv run alembic downgrade -1
```

---

## 9. API Specification & Endpoints Reference

Base Path: `/api/v1`

### 9.1 Create Account
- **Endpoint:** `POST /api/v1/accounts`
- **Status:** `201 Created`
- **Request:**
  ```json
  {
    "name": "Alice Sharma"
  }
  ```
- **Response (`201 Created`):**
  ```json
  {
    "id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
    "name": "Alice Sharma",
    "balance": "0.00",
    "currency": "INR",
    "created_at": "2026-10-27T10:00:00Z",
    "updated_at": "2026-10-27T10:00:00Z"
  }
  ```
- **Validation:** `name` must be between 1 and 255 non-whitespace characters with no null bytes. Clients cannot set initial balance (`extra='forbid'`).

### 9.2 Get Account Balance
- **Endpoint:** `GET /api/v1/accounts/{account_id}/balance`
- **Status:** `200 OK`
- **Response:**
  ```json
  {
    "account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
    "balance": "450.30",
    "currency": "INR",
    "updated_at": "2026-10-27T10:05:00Z"
  }
  ```
- **Errors:** Returns `404 Not Found` with `ACCOUNT_NOT_FOUND` if the account does not exist.

### 9.3 Get Account Details
- **Endpoint:** `GET /api/v1/accounts/{account_id}`
- **Status:** `200 OK`
- **Response:** Returns account model with current balance, name, and timestamps.

### 9.4 Add Transaction (Credit or Debit)
- **Endpoint:** `POST /api/v1/accounts/{account_id}/transactions`
- **Status:** `201 Created`
- **Credit Request:**
  ```json
  {
    "type": "credit",
    "amount": "450.30",
    "description": "Invoice settlement #1024",
    "reference_id": "REF-INV-2026-001"
  }
  ```
- **Debit Request:**
  ```json
  {
    "type": "debit",
    "amount": "1.10",
    "description": "Subscription fee",
    "reference_id": "REF-SUB-2026-002"
  }
  ```
- **Response (`201 Created`):**
  ```json
  {
    "id": "3a11b5e2-411a-4f51-b883-9b6d5b0a1a3b",
    "account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
    "type": "credit",
    "amount": "450.30",
    "balance_after": "450.30",
    "currency": "INR",
    "description": "Invoice settlement #1024",
    "reference_id": "REF-INV-2026-001",
    "transfer_id": null,
    "created_at": "2026-10-27T10:05:00Z"
  }
  ```
- **Validation & Business Rules:**
  - `type`: Must be strictly `'credit'` or `'debit'`.
  - `amount`: Strictly positive decimal string with $\le 2$ decimal places.
  - `reference_id`: Unique within this account (1..128 chars).
  - Debit requests with insufficient balance return `422 Unprocessable Content` with `INSUFFICIENT_FUNDS`.
  - Duplicate reference IDs within the same account return `409 Conflict` with `DUPLICATE_REFERENCE`.

### 9.5 Get Transaction History
- **Endpoint:** `GET /api/v1/accounts/{account_id}/transactions`
- **Query Parameters:**
  - `type` (optional): Filter by `'credit'` or `'debit'`. Returns both if omitted.
  - `page` (optional, default `1`): 1-based page number ($\ge 1$).
  - `limit` (optional, default `20`): Page size ($1 \le \text{limit} \le 100$).
- **Response (`200 OK`):**
  ```json
  {
    "items": [
      {
        "id": "3a11b5e2-411a-4f51-b883-9b6d5b0a1a3b",
        "account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
        "type": "credit",
        "amount": "450.30",
        "balance_after": "450.30",
        "currency": "INR",
        "description": "Invoice settlement #1024",
        "reference_id": "REF-INV-2026-001",
        "transfer_id": null,
        "created_at": "2026-10-27T10:05:00Z"
      }
    ],
    "pagination": {
      "current_page": 1,
      "page_size": 20,
      "total_records": 1,
      "total_pages": 1
    }
  }
  ```
- **Sorting:** Strict newest-first ordering: `ORDER BY created_at DESC, id DESC`.

### 9.6 Money Transfer (Bonus)
- **Endpoint:** `POST /api/v1/transfers`
- **Status:** `201 Created`
- **Request:**
  ```json
  {
    "source_account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
    "destination_account_id": "2e675ff1-0c3c-4890-9bc6-cdf8b8ceaec5",
    "amount": "450.30",
    "description": "Peer-to-peer reimbursement",
    "reference_id": "XFER-2026-9902"
  }
  ```
- **Response (`201 Created`):**
  ```json
  {
    "transfer_id": "9d8e7f6a-5b4c-3d2e-1f0a-9b8c7d6e5f4a",
    "source_account_id": "2f309b73-855f-432b-ad27-ae62c3c677c9",
    "destination_account_id": "2e675ff1-0c3c-4890-9bc6-cdf8b8ceaec5",
    "amount": "450.30",
    "currency": "INR",
    "status": "completed",
    "reference_id": "XFER-2026-9902",
    "description": "Peer-to-peer reimbursement",
    "created_at": "2026-10-27T10:10:00Z"
  }
  ```
- **Validation & Atomicity:**
  - Source and destination accounts must be distinct (`source_account_id != destination_account_id`), otherwise returns `422` with `TRANSFER_SAME_ACCOUNT`.
  - Source must have sufficient balance (`INSUFFICIENT_FUNDS`).
  - Both accounts must exist (`ACCOUNT_NOT_FOUND`).
  - `reference_id` must be globally unique across transfers (`409 Conflict`).
  - Atomically creates one `transfers` record and two linked `transactions` records (debit and credit). Failure at any point rolls back all modifications.

### 9.7 Standard Error Response Format
All 4xx and 5xx responses conform to a unified error structure:
```json
{
  "error": {
    "code": "INSUFFICIENT_FUNDS",
    "message": "Account has insufficient balance to complete the requested debit.",
    "details": [],
    "request_id": "req_ad6b5cc7852b456aaec261997197568a",
    "timestamp": "2026-10-28T00:15:00.000000Z"
  }
}
```

---

## 10. Concurrency, Atomicity & Data Integrity Guarantees

1. **Pessimistic Row Locking:** During credits, debits, and transfers, the affected account rows are queried with `SELECT ... FOR UPDATE`. This forces concurrent transactions on the same account to execute sequentially, completely preventing negative balance race conditions.
2. **Deterministic Lock Ordering:** Cross-account transfers lock the two accounts in ascending alphabetical order of their UUIDs (`sorted([source_id, dest_id])`). This eliminates circular wait conditions and prevents PostgreSQL transaction deadlocks (ADR-007).
3. **Atomic Unit of Work:** Balance mutations and transaction ledger insertions occur inside a single SQLAlchemy `async with session.begin()` block. If any step fails, the entire transaction rolls back cleanly.
4. **Database-Level Check Constraints:**
   - `accounts.balance_minor >= 0`: Guarantees an account balance can never become negative at the database layer.
   - `transactions.amount_minor > 0`: Guarantees every ledger transaction has a strictly positive value.
   - `transactions.type IN ('credit', 'debit')`: Constrains transaction direction.
5. **Ledger Immutability:** Transactions cannot be updated or deleted via the API (no routes defined). Furthermore, the PostgreSQL database revokes `UPDATE` and `DELETE` on `transactions` from `ledger_app`.

---

## 11. Interactive Documentation & Health Probes

When the application is running, the interactive OpenAPI documentation and diagnostic probes are available at:

- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs) (or port `8001` if configured)
- **ReDoc Documentation:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **OpenAPI 3.1 JSON Specification:** [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)
- **Application Health Probe:** [http://localhost:8000/health](http://localhost:8000/health)

---

## 12. Running Automated Tests & Quality Scanners

The repository includes a comprehensive automated test suite (213 unit tests + 48 PostgreSQL integration tests = 261 total tests):

### Running Tests
```bash
# Run the complete test suite (unit + PostgreSQL integration)
uv run pytest -v

# Run fast in-memory unit tests only
uv run pytest -v tests/unit

# Run PostgreSQL integration tests only
uv run pytest -v tests/integration
```

### Running Static Analysis, Style & Security Scanners
```bash
# 1. Code style and import order checks
uv run ruff check .
uv run ruff format --check .

# 2. Static type checking
uv run pyright

# 3. Security AST scan
uv run bandit -c pyproject.toml -r src/

# 4. Dependency vulnerability audit
uv run pip-audit -s osv
```

All 5 scanner commands pass with zero errors, warnings, or findings.

---

## 13. Assumptions & Limitations

- **Single Currency (INR):** The system is purpose-built as an Indian Rupee ledger. Multi-currency conversions and foreign exchange are intentionally out of scope.
- **Evaluation Mode:** For assessment evaluation, `ALLOW_UNAUTHENTICATED_ACCESS=true` permits direct endpoint invocation. In production, this flag is blocked at boot by a startup safety gate requiring integration with an identity provider.
- **Database-Level Concurrency:** Concurrency control is achieved via PostgreSQL row-level pessimistic locking (`FOR UPDATE`). This design provides optimal consistency without requiring external distributed lock managers (e.g. Redis Redlock).

For an exhaustive record of all assessment assumptions, monetary precision models, concurrency guarantees, and scope boundaries, refer to [docs/assumptions.md](docs/assumptions.md).
