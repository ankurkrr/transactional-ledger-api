# Transaction Ledger API — Architectural Decision Records (ADRs)

**Document Version:** 1.1.0  
**Status:** DRAFT — AWAITING REVIEW (Phase 0 Revised)  
**Author:** Principal Backend Engineer & Application Security Architect  

This document formalizes all architectural, data modeling, concurrency, and security decisions. Unresolved decisions and assumptions requiring explicit approval are flagged prominently.

---

## Index of Decisions

- [ADR-001: Relational Database Engine and Persistence Framework](#adr-001-relational-database-engine-and-persistence-framework)
- [ADR-002: Monetary Representation as 64-bit Integer Minor Units](#adr-002-monetary-representation-as-64-bit-integer-minor-units)
- [ADR-003: Concurrency Control Strategy: Pessimistic Row Locking](#adr-003-concurrency-control-strategy-pessimistic-row-locking)
- [ADR-004: Duplicate Reference Handling Policy: HTTP 409 Conflict](#adr-004-duplicate-reference-handling-policy-http-409-conflict)
- [ADR-005: Entity Identifier Format: RFC 4122 UUIDv4](#adr-005-entity-identifier-format-rfc-4122-uuidv4)
- [ADR-006: Ledger Immutability Enforcement Strategy](#adr-006-ledger-immutability-enforcement-strategy)
- [ADR-007: Transfer Deadlock-Risk Reduction via Deterministic Lock Ordering](#adr-007-transfer-deadlock-risk-reduction-via-deterministic-lock-ordering)
- [ADR-008: Authentication & Environment Configuration Safety Gates](#adr-008-authentication--environment-configuration-safety-gates)
- [ADR-009: Database Transaction Boundary and Rollback Ownership](#adr-009-database-transaction-boundary-and-rollback-ownership)
- [ADR-010: Ambiguous Database Outcomes and Retry Reconciliation Policy](#adr-010-ambiguous-database-outcomes-and-retry-reconciliation-policy)

---

### ADR-001: Relational Database Engine and Persistence Framework
- **Status:** PROPOSED (Approved Baseline)
- **Context:** The system requires absolute ACID transaction semantics, row-level locks, transactional DDL, rich CHECK constraints, and deterministic ordering.
- **Decision:** Use **PostgreSQL 16+** with **SQLAlchemy 2.x** and **Alembic** migrations.
- **Consequences:** Provides robust ACID guarantees and standard SQL constraints. Local test execution utilizes Docker Compose with real PostgreSQL containers.

---

### ADR-002: Monetary Representation — Option B: INR Rupee Public API & Integer-Paise Persistence
- **Status:** APPROVED & IMPLEMENTED
- **Context:** Financial applications must completely avoid floating-point types (`float`, `double`) due to IEEE-754 precision loss. However, requiring public API callers to submit amounts in integer minor units (e.g. `45030` paise) harms developer ergonomics.
- **Decision:** Implement **Option B**:
  1. **Public API Contract:** Callers submit monetary amounts in **INR rupees as decimal strings** (e.g., `"450.30"`). Monetary responses return **fixed two-decimal-place strings** (e.g., `"450.30"`, `"0.00"`). JSON numbers, floats, booleans, negatives, zero, and malformed strings are strictly rejected (HTTP 422).
  2. **Storage and Domain Logic:** Database columns (`accounts.balance_minor`, `transactions.amount_minor`, `transactions.balance_after_minor`, `transfers.amount_minor`) and internal services continue storing and computing exclusively with **64-bit signed integers (`BIGINT`)** representing integer paise.
  3. **Exact Conversion Boundary:** Conversion occurs once at the public API boundary using Python's `Decimal` without binary floating-point arithmetic.
  4. **Single Currency:** The ledger is single-currency `INR`. Non-INR or mismatched currencies are rejected.
- **Consequences:** Combines optimal caller usability with provable financial precision, exact integer arithmetic in PostgreSQL, zero floating-point drift, and complete immutability.

---

### ADR-003: Concurrency Control Strategy: Pessimistic Row Locking
- **Status:** PROPOSED
- **Context:** High-concurrency debits on the same account can result in race conditions and negative balances.
- **Decision:** Utilize **Pessimistic Row Locking via `SELECT ... FOR UPDATE`** on the `accounts` row within an atomic PostgreSQL transaction.
- **Consequences:** Guarantees strict serialization per account row, deterministic error reporting (distinguishing 404 from 422 before writing), and immediate balance snapshot recording.

---

### ADR-004: Duplicate Reference Handling Policy: HTTP 409 Conflict
- **Status:** PROPOSED
- **Context:** The assessment states: *"If a transaction with the same reference_id is submitted again for the same account: A new transaction must not be created. The account balance must not change. The API should return an appropriate response indicating that the transaction already exists."*
- **Decision:** Return **HTTP 409 Conflict** with stable error code `DUPLICATE_REFERENCE`.
- **Security & Privacy Rule:** The response details array is kept empty (`[]`) to avoid leaking transaction details (amounts, timestamps, record IDs) across unverified callers.
- **Consequences:** Clear, unambiguous error reporting that satisfies the assessment's duplicate transaction error requirements without data leakage.

---

### ADR-005: Entity Identifier Format: RFC 4122 UUIDv4
- **Status:** PROPOSED
- **Context:** Identifiers must be globally unique and secure against enumeration attacks.
- **Decision:** Use **RFC 4122 UUIDv4** (e.g., `3fa85f64-5717-4562-b3fc-2c963f66afa6`).
- **Security Rule:** An account UUID is strictly an addressing locator, **never** an authorization token.
- **Consequences:** Non-enumerable, globally unique, native PostgreSQL `uuid` column type.

---

### ADR-006: Ledger Immutability Enforcement Strategy
- **Status:** PROPOSED
- **Context:** Transactions must not be editable or deletable through the API.
- **Decision:** Implement **Defense-in-Depth Immutability**:
  1. API Layer: No `PUT`, `PATCH`, or `DELETE` endpoints exposed.
  2. Service Layer: No update/delete methods implemented in `LedgerService`.
  3. Database Role Layer: In staging/production, runtime role `ledger_app` is granted only `SELECT` and `INSERT` on `transactions`; `UPDATE` and `DELETE` are revoked.
- **Consequences:** Append-only ledger enforced at both application and database engine levels.

---

### ADR-007: Transfer Deadlock-Risk Reduction via Deterministic Lock Ordering
- **Status:** PROPOSED
- **Context:** Concurrent opposing transfers between the same pair of accounts (`A -> B` and `B -> A`) can trigger database deadlocks (`SQLSTATE 40P01`).
- **Decision:** When locking multiple accounts in a transfer, acquire locks in ascending order of their UUID strings (`min(id_a, id_b)` then `max(id_a, id_b)`).
- **Nuance & Limitations:** Deterministic ordering substantially reduces deadlock risk between two-party transfers. However, it does not guarantee deadlocks are impossible in all database environments (e.g., page-level locks, competing concurrent queries). Therefore, application-level deadlock detection handling and client retry mechanisms remain required.

---

### ADR-008: Authentication & Environment Configuration Safety Gates
- **Status:** PROPOSED
- **Context:** The assessment specifies an open, unauthenticated functional API for evaluation. However, deploying an unauthenticated ledger to production would be catastrophic.
- **Decision:**
  1. **Evaluation Mode:** Local development and testing run with `ALLOW_UNAUTHENTICATED_ACCESS=true`.
  2. **Production Safety Gate:** Startup validation in `core/config.py` raises a fatal error if `ENVIRONMENT == "production"` and `ALLOW_UNAUTHENTICATED_ACCESS == True`.
  3. **Production Prerequisite:** An authentication and account-ownership model (`owner_user_id`) must be implemented before production deployment. The system is not claimed to be production-ready without this model.

---

### ADR-009: Database Transaction Boundary and Rollback Ownership
- **Status:** PROPOSED
- **Context:** Financial operations must never leave partial state or leak uncommitted changes upon failure.
- **Decision:** The service layer transaction boundary explicitly owns the database session and is solely responsible for executing `await session.rollback()`. Python exception handling inside the transaction scope must use bare `raise` to preserve original tracebacks. Rollback must never be assumed to happen in an outer HTTP exception handler.

---

### ADR-010: Ambiguous Database Outcomes and Retry Reconciliation Policy
- **Status:** PROPOSED
- **Context:** If a database connection drops while awaiting `COMMIT` confirmation, the outcome is indeterminate (the transaction may have committed or rolled back).
- **Decision:**
  1. Financial write operations must **never be blindly retried** by the client or background workers.
  2. Safe retries are limited to idempotent reads or failures occurring prior to the commit phase.
  3. For commit timeouts or disconnects, clients must reconcile using the same `reference_id` or query account transaction history.
