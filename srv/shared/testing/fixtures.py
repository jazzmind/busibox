"""
Common test fixtures and utilities for all busibox services.

Provides environment variable handling and URL extraction.
"""

import os
import pytest


def require_env(name: str) -> str:
    """
    Require an environment variable to be set.
    
    Args:
        name: Environment variable name
        
    Returns:
        The value of the environment variable
        
    Raises:
        pytest.fail if the variable is not set
    """
    value = os.getenv(name, "")
    if not value:
        pytest.fail(f"Required environment variable {name} is not set. Check .env file.")
    return value


def get_env(name: str, default: str = "") -> str:
    """
    Get an environment variable with optional default.
    
    Args:
        name: Environment variable name
        default: Default value if not set
        
    Returns:
        The value or default
    """
    return os.getenv(name, default)


def get_authz_base_url() -> str:
    """
    Extract AuthZ base URL from JWKS URL environment variable.
    
    Returns:
        Base URL for authz service (e.g., http://10.96.200.210:8010)
    """
    jwks_url = os.getenv("AUTHZ_JWKS_URL", "")
    if not jwks_url:
        return ""
    return jwks_url.replace("/.well-known/jwks.json", "")

