"""
Test Mode Support for Busibox Services.

Provides a unified approach to running services in test mode with isolated test databases.

When X-Test-Mode: true header is present and test mode is enabled, requests are routed
to the test database. This allows integration tests to run against the live service
without affecting production data.

Usage:
    from busibox_common import TestModeConfig, DatabaseRouter
    
    # Configure test mode (typically from environment)
    config = TestModeConfig.from_env()
    
    # Create database router
    router = DatabaseRouter(
        prod_pool=prod_pool,
        test_pool=test_pool if config.enabled else None,
        config=config,
    )
    
    # In route handlers, get the appropriate connection
    pg = router.get_pool(request)
    async with pg.acquire() as conn:
        ...

Environment Variables:
    AUTHZ_TEST_MODE_ENABLED: "true" to enable test mode (default: "false")
    TEST_DB_NAME: Test database name (default: "test_authz")
    TEST_DB_USER: Test database user (default: "busibox_test_user")
    TEST_DB_PASSWORD: Test database password (default: "testpassword")
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional, Any, TypeVar, Generic, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Header name for test mode
TEST_MODE_HEADER = "X-Test-Mode"


@dataclass
class TestModeConfig:
    """Configuration for test mode support."""
    
    enabled: bool = False
    test_db_name: str = "test_authz"
    test_db_user: str = "busibox_test_user"
    test_db_password: str = "testpassword"
    
    # Service-specific test DB names (override test_db_name)
    service_test_db_names: dict = None
    
    def __post_init__(self):
        if self.service_test_db_names is None:
            self.service_test_db_names = {
                "authz": "test_authz",
                "ingest": "test_files",
                "search": "test_files",
                "agent": "test_agent_server",
            }
    
    @classmethod
    def from_env(cls, service_name: str = "authz") -> "TestModeConfig":
        """
        Create TestModeConfig from environment variables.
        
        Args:
            service_name: The service name (authz, ingest, search, agent)
                         Used to determine the default test database name.
        """
        # Check service-specific or general test mode flag
        enabled = (
            os.getenv(f"{service_name.upper()}_TEST_MODE_ENABLED", "").lower() == "true"
            or os.getenv("TEST_MODE_ENABLED", "").lower() == "true"
        )
        
        # Get default test DB name for this service
        default_test_db = {
            "authz": "test_authz",
            "ingest": "test_files",
            "search": "test_files",
            "agent": "test_agent_server",
        }.get(service_name, f"test_{service_name}")
        
        return cls(
            enabled=enabled,
            test_db_name=os.getenv("TEST_DB_NAME", default_test_db),
            test_db_user=os.getenv("TEST_DB_USER", "busibox_test_user"),
            test_db_password=os.getenv("TEST_DB_PASSWORD", "testpassword"),
        )
    
    def get_test_db_config(self, base_config: dict) -> dict:
        """
        Create a test database config by overriding production config.
        
        Args:
            base_config: The production database configuration dict
            
        Returns:
            A new dict with test database credentials
        """
        test_config = base_config.copy()
        test_config["postgres_db"] = self.test_db_name
        test_config["postgres_user"] = self.test_db_user
        test_config["postgres_password"] = self.test_db_password
        return test_config


@runtime_checkable
class HasHeaders(Protocol):
    """Protocol for objects with headers (like FastAPI Request)."""
    @property
    def headers(self) -> Any: ...


def is_test_mode_request(request: HasHeaders, config: TestModeConfig) -> bool:
    """
    Check if a request should use test mode.
    
    Args:
        request: The incoming request (must have .headers)
        config: The test mode configuration
        
    Returns:
        True if request has X-Test-Mode: true header and test mode is enabled
    """
    if not config.enabled:
        return False
    
    test_header = request.headers.get(TEST_MODE_HEADER, "").lower()
    return test_header == "true"


# Type variable for database pool/service types
T = TypeVar('T')


class DatabaseRouter(Generic[T]):
    """
    Routes database connections based on test mode header.
    
    Usage:
        router = DatabaseRouter(
            prod_pool=pg_service,
            test_pool=pg_test_service,
            config=TestModeConfig.from_env("authz"),
        )
        
        # In route handler:
        pg = router.get_pool(request)
        async with pg.acquire(...) as conn:
            ...
    """
    
    def __init__(
        self,
        prod_pool: T,
        test_pool: Optional[T] = None,
        config: Optional[TestModeConfig] = None,
    ):
        """
        Initialize the database router.
        
        Args:
            prod_pool: The production database pool/service
            test_pool: The test database pool/service (optional)
            config: Test mode configuration (optional, uses default if not provided)
        """
        self.prod_pool = prod_pool
        self.test_pool = test_pool
        self.config = config or TestModeConfig()
    
    def get_pool(self, request: HasHeaders = None) -> T:
        """
        Get the appropriate database pool based on request headers.
        
        Args:
            request: The incoming request (optional)
            
        Returns:
            test_pool if test mode request, otherwise prod_pool
        """
        if request and self.test_pool and is_test_mode_request(request, self.config):
            logger.debug("Using test database for request")
            return self.test_pool
        return self.prod_pool
    
    def is_test_request(self, request: HasHeaders) -> bool:
        """Check if this request is using test mode."""
        return is_test_mode_request(request, self.config)


# Global router instance (set by service startup)
_global_router: Optional[DatabaseRouter] = None


def init_database_router(
    prod_pool: Any,
    test_pool: Any = None,
    config: TestModeConfig = None,
) -> DatabaseRouter:
    """
    Initialize the global database router.
    
    Call this during service startup after creating database pools.
    
    Args:
        prod_pool: The production database pool/service
        test_pool: The test database pool/service (optional)
        config: Test mode configuration (optional)
        
    Returns:
        The configured DatabaseRouter instance
    """
    global _global_router
    _global_router = DatabaseRouter(
        prod_pool=prod_pool,
        test_pool=test_pool,
        config=config or TestModeConfig(),
    )
    
    if config and config.enabled and test_pool:
        logger.info(f"Test mode enabled with database: {config.test_db_name}")
    
    return _global_router


def get_database(request: HasHeaders = None) -> Any:
    """
    Get the appropriate database pool for a request.
    
    This is a convenience function that uses the global router.
    
    Args:
        request: The incoming request (optional)
        
    Returns:
        The appropriate database pool
        
    Raises:
        RuntimeError: If init_database_router hasn't been called
    """
    if _global_router is None:
        raise RuntimeError("Database router not initialized. Call init_database_router first.")
    return _global_router.get_pool(request)


def get_router() -> Optional[DatabaseRouter]:
    """Get the global database router instance."""
    return _global_router
