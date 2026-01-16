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
from .auth import (
    # Data classes
    Role,
    UserContext,
    WorkerRLSContext,
    # JWT parsing
    parse_jwt_token,
    extract_user_context,
    create_jwks_client,
    # Token exchange
    TokenExchangeClient,
    TokenExchangeService,  # Legacy alias
    TOKEN_EXCHANGE_GRANT,
    clear_token_cache,
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
    # Auth - JWT parsing
    "parse_jwt_token",
    "extract_user_context",
    "create_jwks_client",
    # Auth - Token exchange
    "TokenExchangeClient",
    "TokenExchangeService",
    "TOKEN_EXCHANGE_GRANT",
    "clear_token_cache",
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
]
__version__ = "0.1.0"
