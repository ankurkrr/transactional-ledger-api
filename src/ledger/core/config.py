"""Application configuration management using Pydantic Settings.

Enforces strict environment validation, secret management, and a non-bypassable
safety gate preventing unauthenticated execution in production.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EnvironmentType = Literal["development", "test", "staging", "production"]
LogLevelType = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Application settings with environment validation and production safety gates."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Core Application Metadata
    PROJECT_NAME: str = "Transactional Ledger API"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    # Environment & Access Control Safety Gate
    ENVIRONMENT: EnvironmentType = Field(
        default="development",
        description="Operating runtime environment.",
    )
    ALLOW_UNAUTHENTICATED_ACCESS: bool = Field(
        default=False,
        description="Permits unauthenticated requests strictly during local assessment evaluation.",
    )

    # Database Connectivity (PostgreSQL 16+)
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://ledger_app:postgres@localhost:5432/ledger",
        description="Asynchronous database connection URI.",
    )
    MIGRATION_DATABASE_URL: str | None = Field(
        default=None,
        description="Optional dedicated connection URI for database migrations using the DDL role.",
    )
    TEST_DATABASE_URL: str | None = Field(
        default=None,
        description="Optional dedicated connection URI for isolated integration tests.",
    )
    DB_POOL_SIZE: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Database connection pool base size.",
    )
    DB_MAX_OVERFLOW: int = Field(
        default=20,
        ge=0,
        le=50,
        description="Maximum connections allowed beyond pool size during traffic surges.",
    )
    DB_POOL_TIMEOUT: int = Field(
        default=30,
        ge=1,
        le=120,
        description="Seconds to wait before timing out on connection pool checkout.",
    )

    # Application Security Secrets
    SECRET_KEY: str = Field(
        default="dev_insecure_default_secret_key_minimum_32_characters_long",
        description="Cryptographic secret key for signing sessions and state.",
    )

    # Logging
    LOG_LEVEL: LogLevelType = Field(
        default="INFO",
        description="Logging severity threshold.",
    )

    def check_production_safety(self) -> None:
        """Explicit safety verification invoked during application startup."""
        if self.ENVIRONMENT == "production" and self.ALLOW_UNAUTHENTICATED_ACCESS:
            raise ValueError(
                "CRITICAL CONFIGURATION ERROR: ALLOW_UNAUTHENTICATED_ACCESS cannot be True "
                "when ENVIRONMENT is 'production'. Unauthenticated access is strictly prohibited "
                "in production."
            )

        if self.ENVIRONMENT in ("staging", "production") and (
            "dev_insecure_default" in self.SECRET_KEY or len(self.SECRET_KEY) < 32
        ):
            raise ValueError(
                f"CRITICAL CONFIGURATION ERROR: In '{self.ENVIRONMENT}', SECRET_KEY must be "
                "a secure non-default secret at least 32 characters long."
            )

    @model_validator(mode="after")
    def validate_production_safety_gate(self) -> "Settings":
        """Configuration Safety Gate:

        Strictly prevents creating settings with unauthenticated access
        enabled in a production environment.
        """
        self.check_production_safety()
        return self


@lru_cache
def get_settings() -> Settings:
    """Returns a cached singleton instance of application settings."""
    return Settings()
