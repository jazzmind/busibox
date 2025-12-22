"""
JWT Authentication Middleware - Re-exports from shared_auth library.

This module provides backwards compatibility by re-exporting all JWT auth
utilities from the shared_auth library. New code should import directly
from shared_auth.

See srv/shared_auth/jwt_auth.py for implementation details.
"""

import sys
import os

# Add shared_auth to path
# Path resolution for different environments:
# - Deployed: /opt/search/shared_auth (shared_auth is copied alongside src/)
# - Local: srv/shared_auth (shared_auth is a sibling of search/)
_this_file = os.path.abspath(__file__)
_middleware_dir = os.path.dirname(_this_file)  # api/middleware/
_api_dir = os.path.dirname(_middleware_dir)     # api/
_src_dir = os.path.dirname(_api_dir)            # src/
_search_dir = os.path.dirname(_src_dir)         # search/ (or /opt/search on deployed)
_srv_dir = os.path.dirname(_search_dir)         # srv/ (or /opt on deployed)

_shared_auth_paths = [
    "/opt/search/shared_auth",                  # Deployed: absolute path
    os.path.join(_search_dir, "shared_auth"),   # Deployed: relative to service dir
    os.path.join(_srv_dir, "shared_auth"),      # Local: srv/shared_auth
]

for _path in _shared_auth_paths:
    if os.path.exists(_path):
        _parent = os.path.dirname(_path)
        if _parent not in sys.path:
            sys.path.insert(0, _parent)
        break

# Re-export everything from shared_auth
from shared_auth import (
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
