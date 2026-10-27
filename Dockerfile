FROM python:3.13-slim

WORKDIR /app

# Copy project specifications, README (required by Hatchling), and source files
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY docker/ ./docker/

# Install application and production dependencies
RUN pip install --default-timeout=100 --no-cache-dir .

# Ensure entrypoint script is executable
RUN chmod +x docker/entrypoint.sh

# Configure Python runtime paths
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

EXPOSE 8000

# Native Python healthcheck requiring zero external packages (curl/wget)
HEALTHCHECK --interval=5s --timeout=5s --retries=5 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/bin/sh", "docker/entrypoint.sh"]
