"""
Environment utilities for testing.

Provides:
- Environment file loading (.env.local, .env)
- Auth environment setup fixtures
- Test document path resolution

Usage:
    # In conftest.py
    from testing.environment import load_env_files, set_auth_env, TEST_DOC_REPO_PATH
    
    # Load env files before other imports
    load_env_files()
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any

import pytest


def load_env_files(service_dir: Optional[Path] = None) -> bool:
    """
    Load environment files for testing.
    
    Loads .env.local first (for local development), then .env as fallback.
    This must be called BEFORE importing app modules that read from env.
    
    Args:
        service_dir: Service directory containing .env files.
                    If None, uses caller's directory.
    
    Returns:
        True if any env file was loaded
        
    Example:
        # At top of conftest.py, BEFORE any other imports:
        from testing.environment import load_env_files
        load_env_files(Path(__file__).parent.parent)
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    
    if service_dir is None:
        # Try to find service dir from caller's frame
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_file = Path(frame.f_back.f_globals.get("__file__", ""))
            # Assume caller is in tests/ subdirectory
            service_dir = caller_file.parent.parent
        else:
            service_dir = Path.cwd()
    
    env_local = service_dir / ".env.local"
    env_file = service_dir / ".env"
    
    loaded = False
    if env_local.exists():
        load_dotenv(env_local, override=True)
        loaded = True
    elif env_file.exists():
        load_dotenv(env_file, override=True)
        loaded = True
    
    return loaded


def get_test_doc_repo_path() -> Path:
    """
    Get the path to the test documents repository.
    
    Resolution order:
    1. TEST_DOC_REPO_PATH environment variable
    2. Sibling busibox-testdocs directory (relative to busibox repo)
    3. Old samples/ directory structure
    
    Returns:
        Path to test documents directory
    """
    # Check env var first
    env_path = os.getenv("TEST_DOC_REPO_PATH")
    if env_path:
        return Path(env_path)
    
    # Try to find sibling repo
    # Assume we're in srv/*/tests or similar
    current = Path(__file__).resolve()
    
    # Walk up to find busibox root (contains provision/, srv/, etc.)
    for parent in current.parents:
        if (parent / "provision").exists() and (parent / "srv").exists():
            sibling = parent.parent / "busibox-testdocs"
            if sibling.exists():
                return sibling
            # Fallback to old samples/ structure
            samples = parent / "samples"
            if samples.exists():
                return samples
            break
    
    # Last resort - current directory
    return Path.cwd()


# Convenience: pre-computed path
TEST_DOC_REPO_PATH = get_test_doc_repo_path()


@pytest.fixture(autouse=True)
def set_auth_env(monkeypatch):
    """
    Auto-use fixture that sets authentication environment variables.
    
    This ensures all tests have consistent auth configuration.
    Override specific variables in test-specific fixtures if needed.
    
    Sets:
    - AUTHZ_ISSUER: busibox-authz
    - JWT_ALGORITHMS: RS256
    - AUTHZ_JWKS_URL: From environment (if set)
    """
    # Set standard values
    monkeypatch.setenv("AUTHZ_ISSUER", "busibox-authz")
    monkeypatch.setenv("JWT_ALGORITHMS", "RS256")
    
    # Preserve JWKS URL if already set
    jwks_url = os.getenv("AUTHZ_JWKS_URL", "")
    if jwks_url:
        monkeypatch.setenv("AUTHZ_JWKS_URL", jwks_url)
    
    yield


def create_service_auth_fixture(service_name: str):
    """
    Create a service-specific auth environment fixture.
    
    Args:
        service_name: Service name for audience (e.g., "search", "ingest")
    
    Returns:
        Fixture function that sets service-specific auth env
        
    Example:
        # In search conftest.py:
        set_search_auth_env = create_service_auth_fixture("search")
    """
    @pytest.fixture(autouse=True)
    def service_auth_env(monkeypatch):
        monkeypatch.setenv("AUTHZ_ISSUER", "busibox-authz")
        monkeypatch.setenv("AUTHZ_AUDIENCE", f"{service_name}-api")
        monkeypatch.setenv("JWT_ALGORITHMS", "RS256")
        
        jwks_url = os.getenv("AUTHZ_JWKS_URL", "")
        if jwks_url:
            monkeypatch.setenv("AUTHZ_JWKS_URL", jwks_url)
        
        yield
    
    return service_auth_env


# =============================================================================
# Configuration Helpers
# =============================================================================

def get_service_config(service_name: str) -> Dict[str, Any]:
    """
    Get standard configuration values for a service.
    
    Args:
        service_name: Service name ("authz", "ingest", "search", "agent")
    
    Returns:
        Dict with configuration values from environment
    """
    config = {
        "service_name": service_name,
        "postgres_host": os.getenv("POSTGRES_HOST", "localhost"),
        "postgres_port": int(os.getenv("POSTGRES_PORT", "5432")),
        "postgres_user": os.getenv("POSTGRES_USER", ""),
        "postgres_password": os.getenv("POSTGRES_PASSWORD", ""),
        "authz_url": _get_authz_url(),
        "authz_jwks_url": os.getenv("AUTHZ_JWKS_URL", ""),
        "authz_issuer": os.getenv("AUTHZ_ISSUER", "busibox-authz"),
    }
    
    # Service-specific database
    if service_name in ("ingest", "search"):
        config["postgres_db"] = os.getenv("POSTGRES_DB", "files")
    elif service_name == "authz":
        config["postgres_db"] = os.getenv("POSTGRES_DB", "authz")
    elif service_name == "agent":
        config["postgres_db"] = os.getenv("POSTGRES_DB", "agent_server")
    else:
        config["postgres_db"] = os.getenv("POSTGRES_DB", "busibox_test")
    
    config["authz_audience"] = f"{service_name}-api"
    
    return config


def _get_authz_url() -> str:
    """Extract authz URL from JWKS URL or env vars."""
    # Try direct URL first
    direct = os.getenv("AUTHZ_URL") or os.getenv("AUTHZ_BASE_URL")
    if direct:
        return direct
    
    # Extract from JWKS URL
    jwks_url = os.getenv("AUTHZ_JWKS_URL", "")
    if jwks_url:
        return jwks_url.replace("/.well-known/jwks.json", "")
    
    return ""

