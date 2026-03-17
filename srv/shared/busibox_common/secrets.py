"""
Secret validation utilities.

Validates that environment variables containing secrets are not set to
known insecure defaults or placeholder values. Services should call
validate_required_secrets() at startup to fail fast.
"""

import logging
import os

logger = logging.getLogger(__name__)

KNOWN_INSECURE_DEFAULTS = frozenset({
    "devpassword",
    "minioadmin",
    "sk-local-dev-key",
    "local-master-key-change-in-production",
    "dev-encryption-key",
    "dev-sso-secret",
    "dev-master-key-change-me",
    "dev-jwt-secret-change-me",
    "dev-session-secret-change-me",
    "sk-dev-litellm-key",
    "default-service-secret-change-in-production",
    "default-jwt-secret",
    "sk-litellm-master-key-change-me",
    "change-me",
    "ClueCon",
})

PLACEHOLDER_PREFIXES = (
    "CHANGE_ME",
    "your-",
    "TODO",
)


def is_insecure_value(value: str | None) -> bool:
    """Check if a value is a known insecure default or placeholder."""
    if not value:
        return True
    if value in KNOWN_INSECURE_DEFAULTS:
        return True
    for prefix in PLACEHOLDER_PREFIXES:
        if value.startswith(prefix):
            return True
    if "change-in-production" in value.lower():
        return True
    if "change-me" in value.lower():
        return True
    return False


def validate_secret(name: str, value: str | None) -> None:
    """Raise RuntimeError if a secret is missing or uses an insecure default."""
    if is_insecure_value(value):
        raise RuntimeError(
            f"FATAL: {name} is missing or using a known insecure default "
            f"(got: {repr(value[:20] + '...' if value and len(value) > 20 else value)}). "
            f"Ensure vault secrets are properly injected."
        )


def validate_required_secrets(secrets: dict[str, str | None]) -> None:
    """Validate multiple secrets at once. Raises on first failure.

    Args:
        secrets: mapping of {env_var_name: value} to validate
    """
    errors = []
    for name, value in secrets.items():
        if is_insecure_value(value):
            errors.append(
                f"  - {name}: {repr(value[:20] + '...' if value and len(value) > 20 else value)}"
            )

    if errors:
        detail = "\n".join(errors)
        raise RuntimeError(
            f"FATAL: {len(errors)} secret(s) are missing or using insecure defaults:\n"
            f"{detail}\n"
            f"Ensure vault secrets are properly injected before starting services."
        )


def warn_insecure_secrets(secrets: dict[str, str | None], service_name: str = "") -> list[str]:
    """Log warnings for insecure secrets without raising. Returns list of bad var names."""
    bad = []
    for name, value in secrets.items():
        if is_insecure_value(value):
            bad.append(name)
            logger.warning(
                "Insecure default detected for %s%s",
                name,
                f" in {service_name}" if service_name else "",
            )
    return bad
