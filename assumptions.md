# Assumptions, Design Decisions & Limitations

**Project:** Transactional Ledger API  
**Document Purpose:** Assessment Submission Record (Submission Item 4)  
**Document Status:** Complete & Verified  
**Target Environment:** Local Evaluation / Docker Compose / PostgreSQL 16+

---

## 1. Purpose

During the development of any financial or ledger system, architectural decisions, operational assumptions, and scope boundaries must be made explicit. Documenting these choices ensures that reviewers and evaluators:
1. Clearly distinguish between **explicit assessment requirements** (from the original specification PDF) and **engineering assumptions** made where requirements were silent.
2. Understand the rationale behind design decisions (such as the monetary contract, database role segregation, and concurrency controls).
3. Recognize the intended scope boundaries and operational limitations of the implementation.

---

## 2. Assessment Assumptions

| Item | Classification | Description & Implementation Evidence |
|---|---|---|
| **Authentication & Access Control** | **Assumption & Design Decision** | **Assessment Requirement:** The assessment specification does not require user authentication, API keys, or session management.<br>**Assumption:** For local evaluation, endpoints must be accessible without bearer tokens or credentials.<br>**Implementation:** The application defaults to `ALLOW_UNAUTHENTICATED_ACCESS=true` in `development` and `test` environments.<br>**Safety Gate:** Enforced in `src/ledger/core/config.py`. If `ENVIRONMENT=production` and `ALLOW_UNAUTHENTICATED_ACCESS=true`, application boot terminates immediately with a fatal configuration error. |
| **Currency (Single-Currency INR)** | **Assessment Requirement & Assumption** | **Assessment Requirement:** The assessment specification examples use numeric values without currency symbols (e.g. 100, 50, 30).<br>**Design Decision:** Standardized exclusively on Indian Rupees (`INR`), implementing Option B.<br>**Implementation:** All accounts default to `currency = 'INR'`. Cross-account transfers verify that both accounts operate in INR; any currency mismatch raises `CurrencyMismatchError` (HTTP 422). Foreign exchange conversion is intentionally out of scope. |
| **Evaluation Environment & Tooling** | **Assumption & Operational Design** | **Assessment Requirement:** The project must be runnable locally using instructions provided in the README.<br>**Assumption:** The evaluator machine has either Docker Desktop (standard workflow) or Python 3.12+ with a running PostgreSQL 16+ instance.<br>**Implementation:** Single-command startup via `docker compose up --build` with automatic Alembic migrations, database initialization (`docker/init-db/01-init.sql`), and dependency management via `uv`. |

---

## 3. Monetary Representation

The application implements **Option B: Public Decimal INR Rupees with Internal Integer-Paise Storage** (ADR-002).

### Verification Against Implementation
- **Public API Inputs (Rupees):** Callers supply monetary amounts as decimal strings (e.g. `"450.30"`, `"450.5"`, `"1.10"`). Validated using regex `^\d+(\.\d{1,2})?$` in `src/ledger/schemas/transaction.py`.
- **Public API Outputs (Rupees):** All responses return fixed two-decimal-place strings (e.g. `"450.30"`, `"0.00"`). Formatting is executed by `paise_to_rupees()` using exact integer division (`paise // 100`) and remainder modulo (`paise % 100:02d`).
- **Conversion Boundary:** Conversion occurs once at the Pydantic schema validation boundary using Python's `Decimal` without binary floating-point math:
  $$\text{paise} = \text{int}(\text{Decimal}(v) \times 100)$$
- **Internal Storage (Paise):** Stored exclusively as 64-bit signed integers (`BIGINT` in PostgreSQL) across all persistence columns: `accounts.balance_minor`, `transactions.amount_minor`, `transactions.balance_after_minor`, and `transfers.amount_minor`. Exactly $1\text{ INR} = 100\text{ paise}$.
- **Strict Input Validation Rules (HTTP 422 `VALIDATION_ERROR`):**
  - Unquoted JSON numbers (e.g. `450.30`) are rejected (`Field(strict=True)`).
  - More than 2 decimal places (e.g. `"1.005"`) are rejected.
  - Negative values (e.g. `"-10.00"`) and zero (`"0.00"`) are rejected.
  - Non-finite numbers (`NaN`, `Infinity`), malformed strings, and whitespace-only strings are rejected.
  - Overflow exceeding PostgreSQL signed `BIGINT` max ($9,223,372,036,854,775,807$ paise) is rejected before database execution.

---

## 4. Transaction Integrity and Concurrency

The implementation enforces financial correctness and ledger integrity through database transactions and database-level constraints.

### Guarantees Established by Code and Automated Tests
1. **Pessimistic Row Locking:**
   - In `LedgerService.add_transaction()` and `TransferService.execute_transfer()`, account records are selected with `SELECT ... FOR UPDATE`.
   - **Guarantee:** Concurrent debits or transfers against the same account execute sequentially. Verified by integration test `test_integration_concurrent_debit_contention_and_race_prevention`.
2. **Deterministic Lock Ordering (Transfers):**
   - When transferring funds between Account A and Account B, the service sorts both UUIDs in ascending lexical order (`sorted([source_id, dest_id])`) before acquiring row locks (ADR-007).
   - **Guarantee:** Eliminates circular wait conditions and prevents PostgreSQL transaction deadlocks during concurrent bi-directional transfers. Verified by integration test `test_integration_concurrent_transfers_no_deadlocks`.
3. **Atomic Unit of Work:**
   - Account balance mutation and ledger record insertion occur within a single SQLAlchemy `async with session.begin()` block.
   - **Guarantee:** If any step fails (e.g. constraint violation, insufficient balance), the entire database transaction is rolled back. No partial records or orphaned ledger rows can exist. Verified by `test_integration_atomicity_rollback_on_partial_failure`.
4. **Database Check Constraints:**
   - `accounts.balance_minor >= 0`: Guarantees an account balance can never become negative at the database layer.
   - `transactions.amount_minor > 0`: Guarantees transaction amounts are strictly positive.
   - `transactions.type IN ('credit', 'debit')`: Guarantees valid direction.
   - Verified by `tests/integration/test_constraints.py` (14 constraint tests).
5. **Ledger Immutability:**
   - **API Level:** No `PUT`, `PATCH`, or `DELETE` endpoints exist for transaction or transfer records.
   - **Database Level:** PostgreSQL privileges explicitly revoke `UPDATE` and `DELETE` on table `transactions` from runtime role `ledger_app` (ADR-006). Verified by `test_integration_role_privilege_least_privilege_enforcement`.

---

## 5. Duplicate References and Retry Behavior

### Reference ID Uniqueness Rules
- **Transactions:** Scoped to the individual account via database unique constraint `uq_transactions_account_reference` on `(account_id, reference_id)`.
  - Reusing a `reference_id` on the **same** account is rejected.
  - Reusing the **same** `reference_id` on **different** accounts is explicitly permitted, satisfying Section 2 of the assessment specification.
- **Transfers:** Scoped globally across all transfers via database unique constraint `uq_transfers_reference_id` on `transfers(reference_id)`.

### Duplicate Reference Protection vs. Request Replay Idempotency
- **Assessment Requirement:**  
  *"If a transaction with the same reference_id is submitted again for the same account: A new transaction must not be created. The account balance must not change. The API should return an appropriate response indicating that the transaction already exists."*
- **Implementation Behavior:**  
  When a duplicate reference is submitted, the API returns **HTTP `409 Conflict`** with error code `DUPLICATE_REFERENCE`. The account balance and ledger entries remain completely unchanged.
- **Crucial Distinction:**  
  This behavior is **duplicate-reference rejection**, not transparent request-replay idempotency. Retrying an identical request does **not** return the original `201 Created` response payload; it explicitly alerts the caller with `409 Conflict` that the reference has already been executed.
- **Security & Privacy Design:**  
  The `409 Conflict` response intentionally returns an empty `details: []` list. It suppresses existing transaction IDs, amounts, or timestamps to prevent financial history disclosure to unverified callers in shared environments (ADR-004).

---

## 6. Design Decisions

| Decision | Choice | Rationale & Architecture Reference |
|---|---|---|
| **API Framework** | **FastAPI + Pydantic v2** | High-performance asynchronous execution, native OpenAPI 3.1 schema generation, and strict type enforcement at boundary layers (ADR-001). |
| **Persistence Engine** | **PostgreSQL 16+ & SQLAlchemy 2.x** | Industry-standard relational database providing true ACID transactions, row-level pessimistic locking (`FOR UPDATE`), rich SQL `CHECK` constraints, and asynchronous I/O via `asyncpg` (ADR-001). |
| **Database Migrations** | **Alembic** | Version-controlled, deterministic schema evolution supporting both online upgrades (`alembic upgrade head`) and offline SQL inspection (`--sql`). |
| **Database Role Separation** | **`ledger_migrator` vs `ledger_app`** | Enforces the Principle of Least Privilege (ADR-006):<br>• `ledger_migrator`: Owns schema `public`, executes DDL.<br>• `ledger_app`: Runtime DML only. Revoked `CREATE` on schema `public` (migration `a17f2231b8d0`); revoked `UPDATE` and `DELETE` on `transactions`. |
| **Entity Identifiers** | **RFC 4122 UUIDv4** | Cryptographically strong 128-bit identifiers (`uuid.uuid4`) generated server-side. Eliminates sequential ID enumeration risks and information disclosure (ADR-005). |
| **History Sorting Policy** | **`created_at DESC, id DESC`** | Guarantees strict latest-first ordering. `id DESC` provides a stable secondary tie-breaker, guaranteeing deterministic pagination when transactions share identical timestamps. |

---

## 7. Scope Limitations

The following functional boundaries are intentionally maintained based on the assessment requirements:

1. **Single-Currency Scope:** Multi-currency wallets, foreign exchange rates, and cross-currency conversions are not supported. All balances and transactions are denominated in INR.
2. **Identity & Authentication:** User registration, OAuth2/JWT tokens, API keys, and multi-tenancy are omitted in evaluation mode to maintain focus on core ledger arithmetic and database integrity.
3. **Ledger Corrections:** Transactions cannot be edited or deleted. If an erroneous transaction is posted, the correction must be made via an offsetting compensatory credit or debit transaction.
4. **Relational Concurrency Model:** Concurrency protection relies on PostgreSQL row-level locks. External distributed locking mechanisms (e.g. Redis Redlock) were not introduced, keeping the deployment architecture lean and self-contained within PostgreSQL.

---

## 8. Reviewer Notes & Checklist

When evaluating the project, the following technical facts should be kept in mind:

- [x] **Monetary Values:** Clients submit string rupees (`"450.50"` or `"450.5"`), never raw minor units or unquoted JSON floats.
- [x] **Port Mapping:** Default is port `8000`. If port 8000 is occupied, set `API_PORT=8001` in `.env`.
- [x] **PostgreSQL Port:** Default container port mapping is `5432`. If local PostgreSQL is running on the host, set `POSTGRES_PORT=5434` in `.env`.
- [x] **Database Isolation:** Integration tests connect to an isolated `ledger_test` database created by `docker/init-db/01-init.sql`. Test runs never truncate or mutate development data.
- [x] **Test Verification:** All 261 automated tests can be verified in ~15 seconds using `uv run pytest -v`.
- [x] **No Unhandled 500 Errors:** All known business rule violations (insufficient funds, duplicate references, invalid decimals, missing accounts) return structured 4xx responses.

---

## 9. Related Documentation

- [Project README](../README.md) — Setup instructions, technology baseline, and quickstart guide.
- [REST API Documentation](../API_DOCUMENTATION.md) — Comprehensive API reference, request/response bodies, and curl suite.
- [Functional Requirements Matrix](requirements.md) — Detailed requirement traceability.
- [Architecture & Concurrency Design](architecture.md) — Deep-dive into database schema and concurrency model.
- [Architectural Decision Records](decisions.md) — ADR-001 through ADR-010.
- [API Contract Specification](api-contract.md) — OpenAPI endpoint schemas.
- [Error Handling Guide](error-handling.md) — Centralized exception mapping and HTTP status conventions.
