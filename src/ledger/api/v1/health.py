"""Health check endpoints for service liveness and readiness probes.

Ensures zero information leakage (no database credentials, no internal file paths,
and no secret keys exposed).
"""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict

from ledger.core.config import Settings, get_settings

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    """Sanitized health check response payload."""

    model_config = ConfigDict(extra="forbid")

    status: str
    version: str
    environment: str


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Service Health Check",
    description="Basic liveness probe confirming the API process is responsive.",
)
async def get_health(
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """Returns basic service health status without disclosing internal configuration."""
    return HealthResponse(
        status="pass",
        version=settings.VERSION,
        environment=settings.ENVIRONMENT,
    )
