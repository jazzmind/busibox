# Shared authentication utilities for busibox services
#
# This module provides common JWT authentication, scope checking,
# and RLS (Row-Level Security) utilities used by all services.

from .jwt_auth import (
    # Data classes
    Role,
    UserContext,
    WorkerRLSContext,
    # JWT parsing
    parse_jwt_token,
    extract_user_context,
    create_jwks_client,
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

__all__ = [
    # Data classes
    "Role",
    "UserContext",
    "WorkerRLSContext",
    # JWT parsing
    "parse_jwt_token",
    "extract_user_context",
    "create_jwks_client",
    # Middleware
    "JWTAuthMiddleware",
    # RLS helpers
    "get_rls_session_vars",
    "set_rls_session_vars",
    "set_rls_session_vars_sync",
    # Scope checking
    "require_scope",
    "require_any_scope",
    "has_scope",
    "has_role",
    "has_any_role",
    # FastAPI dependencies
    "ScopeChecker",
    "AnyScopeChecker",
    # Partition utilities
    "get_accessible_partitions",
    "get_partition_names_for_search",
]

