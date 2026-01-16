"""
JWT Authentication Middleware - Re-exports from busibox_common.auth.

This module re-exports all JWT auth utilities from the shared busibox_common library.
New code should import directly from busibox_common.auth.
"""

from busibox_common.auth import (
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
    "Role",
    "UserContext",
    "WorkerRLSContext",
    "parse_jwt_token",
    "extract_user_context",
    "create_jwks_client",
    "JWTAuthMiddleware",
    "get_rls_session_vars",
    "set_rls_session_vars",
    "set_rls_session_vars_sync",
    "require_scope",
    "require_any_scope",
    "has_scope",
    "has_role",
    "has_any_role",
    "ScopeChecker",
    "AnyScopeChecker",
    "get_accessible_partitions",
    "get_partition_names_for_search",
]
