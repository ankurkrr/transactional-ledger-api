-- 01-init.sql: Initializes dedicated ledger databases and roles.
-- Executed automatically on first startup of the PostgreSQL container.

-- 1. Create isolated test database for automated integration testing
CREATE DATABASE ledger_test;

-- 2. Create migration DDL role
CREATE ROLE ledger_migrator WITH LOGIN PASSWORD 'migrator_password';
GRANT ALL PRIVILEGES ON DATABASE ledger TO ledger_migrator;
GRANT ALL PRIVILEGES ON DATABASE ledger_test TO ledger_migrator;

-- 3. Create application runtime DML role
CREATE ROLE ledger_app WITH LOGIN PASSWORD 'postgres';
GRANT CONNECT ON DATABASE ledger TO ledger_app;
GRANT CONNECT ON DATABASE ledger_test TO ledger_app;

-- 4. Schema permissions in development database (ledger)
\c ledger
ALTER SCHEMA public OWNER TO ledger_migrator;
GRANT ALL ON SCHEMA public TO ledger_migrator;
GRANT USAGE ON SCHEMA public TO ledger_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ledger_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO ledger_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ledger_migrator IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ledger_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO ledger_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ledger_app;

-- 5. Schema permissions in test database (ledger_test)
\c ledger_test
ALTER SCHEMA public OWNER TO ledger_migrator;
GRANT ALL ON SCHEMA public TO ledger_migrator;
GRANT ALL ON SCHEMA public TO ledger_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ledger_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ledger_app;
