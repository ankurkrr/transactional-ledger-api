#!/bin/sh
set -e

echo "Running database migrations via Alembic..."
alembic upgrade head

# Determine worker count (default to 2 workers if WEB_CONCURRENCY not set)
WORKERS=${WEB_CONCURRENCY:-2}

echo "Starting Uvicorn application server with ${WORKERS} worker(s)..."
exec uvicorn ledger.main:app --host 0.0.0.0 --port 8000 --workers "${WORKERS}"
