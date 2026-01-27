"""
Busibox Common - Shared utilities for all Busibox services.

This package provides common functionality used across services:
- Database initialization and migration management
- JWT authentication and token exchange
- RLS (Row-Level Security) helpers
- Test mode support for isolated integration testing
- Shared configuration patterns
- Common utilities
"""

# Import auth utilities (no heavy dependencies)
from .auth import (
    # Data classes
    Role,
    UserContext,
    WorkerRLSContext,
    TokenExchangeResult,
    # JWT parsing
    parse_jwt_token,
    extract_user_context,
    create_jwks_client,
    # Token exchange
    TokenExchangeClient,
    TokenExchangeService,  # Legacy alias
    TOKEN_EXCHANGE_GRANT,
    clear_token_cache,
    clear_zero_trust_cache,
    exchange_token_zero_trust,
    # Middleware
    JWTAuthMiddleware,
    # RLS helpers
    get_rls_session_vars,
    set_rls_session_vars,
    set_rls_session_vars_sync,
    # Scope checking
    require_scope,
    require_any_scope,
    has_scope,
    has_role,
    has_any_role,
    # FastAPI dependencies
    ScopeChecker,
    AnyScopeChecker,
    # Partition utilities
    get_accessible_partitions,
    get_partition_names_for_search,
)
# Optional imports for services that need database/LLM functionality
# These require heavy dependencies (asyncpg, litellm, etc.)
try:
    from .db import DatabaseInitializer, SchemaManager
    from .test_mode import (
        TestModeConfig,
        DatabaseRouter,
        is_test_mode_request,
        init_database_router,
        get_database,
        get_router,
        TEST_MODE_HEADER,
    )
    from .llm import (
        # LiteLLM Client
        LiteLLMClient,
        get_client as get_llm_client,
        reset_client as reset_llm_client,
        ensure_openai_env,
        # Model Registry
        ModelRegistry,
        get_registry as get_model_registry,
        reset_registry as reset_model_registry,
    )
    from .pool import (
        # Pool Configuration
        PoolConfig,
        # Pool Manager
        AsyncPGPoolManager,
        # Module-level convenience functions
        get_pool,
        init_pool,
        reset_pool,
    )
    _HAS_DB_SUPPORT = True
except ImportError:
    # Services without database/LLM support can still use auth utilities
    _HAS_DB_SUPPORT = False
    DatabaseInitializer = None
    SchemaManager = None
    TestModeConfig = None
    DatabaseRouter = None
    is_test_mode_request = None
    init_database_router = None
    get_database = None
    get_router = None
    TEST_MODE_HEADER = None
    LiteLLMClient = None
    get_llm_client = None
    reset_llm_client = None
    ensure_openai_env = None
    ModelRegistry = None
    get_model_registry = None
    reset_model_registry = None
    PoolConfig = None
    AsyncPGPoolManager = None
    get_pool = None
    init_pool = None
    reset_pool = None

__all__ = [
    # Database
    "DatabaseInitializer",
    "SchemaManager",
    # Test mode
    "TestModeConfig",
    "DatabaseRouter",
    "is_test_mode_request",
    "init_database_router",
    "get_database",
    "get_router",
    "TEST_MODE_HEADER",
    # Auth - Data classes
    "Role",
    "UserContext",
    "WorkerRLSContext",
    "TokenExchangeResult",
    # Auth - JWT parsing
    "parse_jwt_token",
    "extract_user_context",
    "create_jwks_client",
    # Auth - Token exchange
    "TokenExchangeClient",
    "TokenExchangeService",
    "TOKEN_EXCHANGE_GRANT",
    "clear_token_cache",
    "clear_zero_trust_cache",
    "exchange_token_zero_trust",
    # Auth - Middleware
    "JWTAuthMiddleware",
    # Auth - RLS helpers
    "get_rls_session_vars",
    "set_rls_session_vars",
    "set_rls_session_vars_sync",
    # Auth - Scope checking
    "require_scope",
    "require_any_scope",
    "has_scope",
    "has_role",
    "has_any_role",
    # Auth - FastAPI dependencies
    "ScopeChecker",
    "AnyScopeChecker",
    # Auth - Partition utilities
    "get_accessible_partitions",
    "get_partition_names_for_search",
    # LLM - Client
    "LiteLLMClient",
    "get_llm_client",
    "reset_llm_client",
    "ensure_openai_env",
    # LLM - Model Registry
    "ModelRegistry",
    "get_model_registry",
    "reset_model_registry",
    # Pool - Configuration
    "PoolConfig",
    # Pool - Manager
    "AsyncPGPoolManager",
    # Pool - Module-level convenience functions
    "get_pool",
    "init_pool",
    "reset_pool",
]
__version__ = "0.1.0"
