# Transaction Ledger API — Security Architecture & Threat Model

**Document Version:** 1.1.0  
**Status:** DRAFT — AWAITING REVIEW (Phase 0 Revised)  
**Author:** Principal Backend Engineer & Application Security Architect  
**Standards Reference:** OWASP API Security Top 10 (2023), OWASP ASVS v4.0.3 (Level 2), NIST SP 800-53  

---

## 1. Threat Modeling Methodology & Scope Limitations

This threat model applies STRIDE (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege) and the OWASP API Security Top 10 (2023) to analyze risks in the Transaction Ledger API.

### Scope Limitations & Explicit Warning
- **Not a Production-Ready System Yet:** The assessment document defines an unauthenticated functional API. While this specification designs robust database-level constraints, atomic transactions, and sanitized error responses, **this system cannot be considered production-ready without an implemented authentication and account-ownership model.**
- **Account UUID is Not Authorization:** Simply possessing, discovering, or guessing an account UUID (`account_id`) does not constitute authorization to read balances, view transaction history, or execute debits.
- **Static Scans Do Not Prove Security:** Passing automated static scans (Bandit, Ruff, pip-audit) is a necessary hygiene gate, but does not prove the absence of zero-day vulnerabilities, business logic flaws, or race conditions.

---

## 2. In-Depth Threat Analysis Matrix (14 Core Scenarios)

| # | Threat Scenario | STRIDE / OWASP Ref | Potential Impact | Preventive Control | Detection Mechanism | Verification Test | Residual Risk |
| :-: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | **Malformed & Unexpected Inputs** (Negative amounts, NaN, float truncation, oversized text, JSON injection) | T, D / API3:2023 Broken Object Property Level Auth | Corruption of monetary math; memory exhaustion; database type conversion errors. | Pydantic v2 strict schemas (`amount_minor: int = Field(gt=0)`), string length limits (`max_length=255`), regex on reference IDs, reject extra JSON fields (`extra='forbid'`). | Validation failure logs with field names; Prometheus metric `api_validation_errors_total`. | `test_malformed_inputs_rejection` (negative, zero, float, 10MB payload). | Low. Well-tested parser core in Rust. |
| **2** | **SQL Injection** (Malicious input strings in reference_id, description, or search queries) | T, I / API8:2023 Security Misconfiguration | Unauthorized data exfiltration, database takeover, ledger tampering. | Exclusively use SQLAlchemy 2.0 ORM and parameterized queries. Absolutely zero raw string formatting or SQL string concatenation. | Static analysis via Bandit (`B608`), AST linting, WAF inspection. | `test_sql_injection_payloads` in reference_id and description fields. | Minimal. Standardized parameterized ORM usage. |
| **3** | **Accidental Unauthenticated Production Deployment** (Evaluation unauthenticated mode enabled in production) | S, E / API2:2023 Broken Authentication | Total unauthorized access to all financial accounts in production. | Hardcoded startup safety gate in `core/config.py`: if `ENVIRONMENT == "production"` and `ALLOW_UNAUTHENTICATED_ACCESS == True`, raise fatal configuration error and abort startup. | Startup log alerts; container health check failure. | `test_production_config_rejects_unauthenticated_mode` | Low with startup validation gate. |
| **4** | **Cross-Account Access (BOLA / IDOR)** (Caller specifies another user's `account_id` in URL/body) | E / API1:2023 Broken Object Level Auth | Malicious actor views another account's balance/transactions or initiates unauthorized debits/transfers. | Treat UUIDs strictly as resource locators, never authorization. Documented requirement for account ownership verification (`owner_user_id`) when authentication is activated. | Security audit logs recording caller identity vs. resource owner mismatch. | `test_bola_cross_account_forbidden` | High in unauthenticated evaluation mode; requires auth layer for production. |
| **5** | **Duplicate / Replayed Requests** (Network retry causes client to re-submit same credit or debit) | T, R / API6:2023 Unrestricted Access to Sensitive Business Flows | Double crediting or double debiting an account, draining funds. | Composite unique constraint `UNIQUE (account_id, reference_id)` in PostgreSQL. Idempotent check returning HTTP 409 Conflict with sanitized payload. | 409 Conflict metrics; application audit trail of duplicate attempts. | `test_duplicate_reference_rejection` and replay simulation test. | Low. Guaranteed by PostgreSQL unique index. |
| **6** | **Concurrent Financial Races** (Two simultaneous debits drain balance below 0; double-spend) | T / Financial Invariant | Negative account balance, unbacked financial loss. | Pessimistic row locking (`SELECT ... FOR UPDATE`) in PostgreSQL; database check constraint `balance_minor >= 0`. | Concurrency conflict monitoring; lock wait time metrics. | Multi-threaded concurrency tests (`test_concurrent_competing_debits_prevent_overdraft`). | Negligible. Enforced at PostgreSQL engine level. |
| **7** | **Ambiguous Outcomes from DB Drops** (Connection drops while awaiting commit acknowledgment) | T, D / System Reliability | Client assumes failure and blindly retries, causing double execution or unexpected conflicts. | Treat write timeouts as ambiguous. Prohibit blind client retries. Clients must reconcile using `reference_id` or history lookup. | Database connection pool dropout and socket error alerts. | `test_db_disconnect_handling_ambiguous_outcome` | Medium. Network partitions require client reconciliation protocols. |
| **8** | **Partial Operation Failures** (Balance updated, but server crashes before ledger insertion; or transfer half-executes) | T / Financial Integrity | Broken reconciliation; money created or destroyed without ledger records. | Single atomic PostgreSQL transaction (`BEGIN ... COMMIT`) per operation. Explicit rollback via service transaction manager using bare `raise`. | Automated end-of-day balance-to-ledger reconciliation audit query. | `test_atomic_ledger_write_failure_rollback` | Negligible. Ensured by PostgreSQL ACID transaction engine. |
| **9** | **Credential & Secret Leakage** (Database credentials committed to git or exposed via environment inspection) | I / API8:2023 Security Misconfiguration | Complete compromise of database and application ledger. | Load credentials strictly via environment variables parsed by `pydantic-settings`. Exclude `.env` files via `.gitignore`. No hardcoded defaults in code. | Automated secret scanning (git pre-commit hooks, Trufflehog / GitHub secret scanning). | `test_no_secrets_in_repository` | Low with disciplined CI and secrets management. |
| **10**| **Sensitive Info in Logs** (PII, credentials, or private banking details logged in plain text) | I / API9:2023 Improper Inventory Management | Compliance violation, data leak to log aggregators. | Structured JSON logging with field redaction filters. Never log request authorization headers or unmasked personal data. | Periodic automated log sampling and audit. | `test_log_redaction_sanitizer` | Low. Centralized logging wrapper prevents ad-hoc print/log statements. |
| **11**| **Error Message Information Disclosure** (Full stack traces, PostgreSQL constraint names, or internal paths leaked to client) | I / API8:2023 Security Misconfiguration | Attackers gain architectural insight to craft targeted exploits. | Central FastAPI exception handlers intercept all unhandled exceptions, returning a generic sanitized error envelope with a correlation `request_id`. | Traceback logged to internal log only with correlation ID. | `test_error_response_no_stack_traces` | Negligible. Enforced by global exception filters. |
| **12**| **Resource Exhaustion & Abusive Rates** (Attacker requests `limit=10000000` or floods endpoints with millions of requests) | D / API4:2023 Unrestricted Resource Consumption | Denial of service, database OOM, API unavailability. | Hardcoded pagination bounds (`limit` clamped to 1..100). Request payload size limit (max 1MB). Reverse-proxy rate limiting. | Rate limit warning headers, Prometheus latency and memory gauges. | `test_pagination_limit_clamped` | Low. Clamped queries prevent expensive full-table memory copies. |
| **13**| **Dependency Vulnerabilities** (Known CVEs in third-party Python packages) | T, E / API8:2023 Security Misconfiguration | Remote code execution, arbitrary file read, denial of service. | Pin exact versions in lockfiles. Run `pip-audit` and `bandit` in CI pipeline on every pull request. Automated security updates. | Dependabot / Renovate notifications. | Automated CI gate running `pip-audit`. | Low when scanner gates are maintained. |
| **14**| **Privileged DB Access & Ledger Tampering** (Application user or compromised credential alters ledger rows) | T, R / Systemic Ledger Integrity | Historical transactions modified, falsified balance records. | PostgreSQL role privilege separation: runtime role `ledger_app` has `REVOKE UPDATE, DELETE ON transactions`. Immutable append-only model. | PostgreSQL audit extension (`pgaudit`) or change-data capture logs. | `test_db_user_cannot_update_transactions` | Low. Enforced at PostgreSQL permission system. |

---

## 3. Authentication & Access Control Architecture Analysis

### Evaluation Mode vs. Production Architecture
The assessment functional requirements describe an open API where accounts are managed via `account_id`. To bridge the gap between assessment evaluation and production security:

1. **Local Evaluation Mode (`ALLOW_UNAUTHENTICATED_ACCESS=true`):**
   - Endpoints accept requests directly for streamlined assessment verification.
   - Strictly restricted to `ENVIRONMENT=development` or `ENVIRONMENT=test`.
2. **Production Safety Gate:**
   - If `ENVIRONMENT=production`, the application checks:
     ```python
     if self.environment == "production" and self.allow_unauthenticated_access:
         raise RuntimeError(
             "FATAL CONFIGURATION ERROR: Unauthenticated access is strictly forbidden in production."
         )
     ```
3. **Production Prerequisite (Flagged Decision):**
   - Before deploying to production, an authentication provider (e.g., Bearer Token / JWT) and an account ownership relation (`accounts.owner_user_id`) must be implemented to prevent BOLA (OWASP API1).

---

## 4. Limitations of Static Scanners & Threat Detection

- **Bandit & Ruff:** Excellent for catching obvious security antipatterns (`eval`, insecure temporary files, SQL string formatting), but completely blind to concurrency race conditions, business logic flaws, or unauthorized data access.
- **pip-audit:** Detects only *known* vulnerabilities cataloged in CVE databases. It cannot detect zero-day vulnerabilities or vulnerabilities in custom project code.
- **Verification Requirement:** Static security scans must be supplemented by dynamic real-PostgreSQL concurrency tests, failure-injection tests, and strict code review.
