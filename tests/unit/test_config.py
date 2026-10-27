"""Unit tests for Pydantic Settings and the Production Configuration Safety Gate."""

import pytest
from pydantic import ValidationError

from ledger.core.config import Settings


def test_development_settings_defaults() -> None:
    """Verifies that development settings instantiate with valid defaults."""
    settings = Settings(ENVIRONMENT="development", ALLOW_UNAUTHENTICATED_ACCESS=True)
    assert settings.ENVIRONMENT == "development"
    assert settings.ALLOW_UNAUTHENTICATED_ACCESS is True
    assert settings.PROJECT_NAME == "Transactional Ledger API"


def test_test_environment_settings() -> None:
    """Verifies test environment configuration."""
    settings = Settings(ENVIRONMENT="test", ALLOW_UNAUTHENTICATED_ACCESS=True)
    assert settings.ENVIRONMENT == "test"


def test_production_safety_gate_rejects_unauthenticated_access() -> None:
    """CRITICAL SAFETY GATE:

    Verifies that ENVIRONMENT='production' combined with
    ALLOW_UNAUTHENTICATED_ACCESS=True raises a fatal validation error.
    """
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            ALLOW_UNAUTHENTICATED_ACCESS=True,
            SECRET_KEY="super_secure_production_secret_key_at_least_32_chars_long",
        )

    error_message = str(exc_info.value)
    assert "CRITICAL CONFIGURATION ERROR" in error_message
    assert "ALLOW_UNAUTHENTICATED_ACCESS cannot be True" in error_message


def test_production_valid_configuration_succeeds() -> None:
    """Verifies that a properly secured production configuration succeeds."""
    settings = Settings(
        ENVIRONMENT="production",
        ALLOW_UNAUTHENTICATED_ACCESS=False,
        SECRET_KEY="super_secure_production_secret_key_at_least_32_chars_long",
    )
    assert settings.ENVIRONMENT == "production"
    assert settings.ALLOW_UNAUTHENTICATED_ACCESS is False


def test_production_rejects_insecure_default_secret_key() -> None:
    """Verifies that production rejects the dev insecure secret key."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            ALLOW_UNAUTHENTICATED_ACCESS=False,
            SECRET_KEY="dev_insecure_default_secret_key_minimum_32_characters_long",
        )

    error_message = str(exc_info.value)
    assert "SECRET_KEY must be a secure non-default secret" in error_message


def test_production_rejects_short_secret_key() -> None:
    """Verifies that production rejects secret keys under 32 characters."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            ALLOW_UNAUTHENTICATED_ACCESS=False,
            SECRET_KEY="short_key_under_32_chars",
        )

    error_message = str(exc_info.value)
    assert "at least 32 characters long" in error_message


def test_invalid_environment_rejected_by_pydantic() -> None:
    """Verifies that an unknown environment cannot silently be accepted."""
    with pytest.raises(ValidationError):
        Settings(ENVIRONMENT="invalid_custom_env")  # type: ignore[reportArgumentType]


def test_invalid_log_level_rejected_by_pydantic() -> None:
    """Verifies that an unknown log level cannot silently be accepted."""
    with pytest.raises(ValidationError):
        Settings(LOG_LEVEL="UNSUPPORTED_LEVEL")  # type: ignore[reportArgumentType]


def test_lifespan_aborts_on_production_unauthenticated() -> None:
    """Verifies that check_production_safety directly prevents startup in production unauthenticated mode."""
    # Construct an unsafe settings object manually bypassing model_validator to test check_production_safety directly
    settings = Settings.model_construct(
        ENVIRONMENT="production",
        ALLOW_UNAUTHENTICATED_ACCESS=True,
        SECRET_KEY="super_secure_production_secret_key_at_least_32_chars_long",
    )
    with pytest.raises(ValueError) as exc_info:
        settings.check_production_safety()

    assert "CRITICAL CONFIGURATION ERROR" in str(exc_info.value)
