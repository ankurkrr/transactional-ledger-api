"""Unit tests for the Health Check API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check_root_endpoint(async_client: AsyncClient) -> None:
    """Verifies that the root /health probe returns 200 OK and expected structure."""
    response = await async_client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "pass"
    assert data["version"] == "0.1.0"
    assert data["environment"] == "test"

    # Strict assertion against information disclosure
    assert "database" not in data
    assert "database_url" not in data
    assert "secret_key" not in data
    assert "password" not in data
    assert "path" not in data


@pytest.mark.asyncio
async def test_health_check_v1_endpoint(async_client: AsyncClient) -> None:
    """Verifies that /api/v1/health returns identical sanitized health data."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "pass"
    assert data["version"] == "0.1.0"
    assert data["environment"] == "test"


@pytest.mark.asyncio
async def test_health_check_disallows_unexpected_query_parameters(
    async_client: AsyncClient,
) -> None:
    """Verifies standard endpoint response even if benign queries are provided."""
    response = await async_client.get("/health?verbose=true")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"status", "version", "environment"}
