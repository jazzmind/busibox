"""
Config API Authentication & Authorization.

Provides FastAPI dependencies for the four access tiers:
- public: no auth needed
- authenticated: any valid JWT
- app_scoped: JWT with access to a specific app (role binding check)
- admin: JWT with Admin role

Uses busibox_common for JWT validation via JWKS.
"""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from busibox_common.auth import (
    create_jwks_client,
    extract_user_context,
    parse_jwt_token,
)

from config import Config

logger = logging.getLogger(__name__)

_config = Config()
security = HTTPBearer(auto_error=False)
_jwks_client = None


def get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{_config.authz_url}/.well-known/jwks.json"
        _jwks_client = create_jwks_client(jwks_url)
    return _jwks_client


def _parse_token(token: str) -> dict:
    """Validate a JWT and return the payload + user context as a dict."""
    payload = parse_jwt_token(
        token=token,
        jwks_client=get_jwks_client(),
        issuer="busibox-authz",
        audience="config-api",
    )
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    uc = extract_user_context(payload, auth_header=f"Bearer {token}", token=token)
    return {
        "user_id": uc.user_id,
        "email": uc.email,
        "roles": [{"id": r.id, "name": r.name} for r in uc.roles],
        "role_names": list(uc.role_names),
        "scopes": list(uc.scopes),
        "token": token,
    }


# ---------------------------------------------------------------------------
# Tier dependencies
# ---------------------------------------------------------------------------

async def optional_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """Return user context if a valid token is present, else None."""
    if credentials is None:
        return None
    try:
        return _parse_token(credentials.credentials)
    except Exception:
        return None


async def require_authenticated(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> dict:
    """Require any valid JWT. Returns user context."""
    return _parse_token(credentials.credentials)


async def require_admin(
    user: dict = Depends(require_authenticated),
) -> dict:
    """Require Admin role."""
    if "Admin" not in user.get("role_names", []):
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def require_admin_or_scope(required_scope: str):
    """
    Factory that returns a dependency allowing access if the caller has
    the Admin role OR the specified OAuth scope in their JWT.

    Non-admin callers with the scope are marked via user["_scope_only"] = True
    so route handlers can restrict what data they return.
    """

    async def _dep(user: dict = Depends(require_authenticated)) -> dict:
        if "Admin" in user.get("role_names", []):
            return user
        if required_scope in user.get("scopes", []):
            user["_scope_only"] = True
            return user
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return _dep


# Categories whose raw values require config.secrets.read scope in addition to
# the Admin role. This prevents admins from directly exfiltrating secrets like
# LLM provider keys by calling /raw; only agent-api (which receives this scope
# via the authz token exchange source-gate) can retrieve decrypted values.
SENSITIVE_CATEGORIES: set[str] = {"llm-keys"}


async def require_secrets_scope(
    user: dict = Depends(require_admin),
) -> dict:
    """
    Require Admin role AND config.secrets.read scope.

    Used to gate /raw and /export endpoints for sensitive config categories so
    that only callers who obtained their config-api token through agent-api can
    retrieve decrypted secret values.
    """
    if "config.secrets.read" not in user.get("scopes", []):
        raise HTTPException(
            status_code=403,
            detail="config.secrets.read scope required to access sensitive config values",
        )
    return user


def require_app_access(app_id_param: str = "app_id"):
    """
    Factory that returns a dependency checking the caller has access to a specific app.

    Admin users have access to all apps. Non-admin users must have a role whose name
    matches the app_id or have the app_id present in their scopes.

    Usage:
        @router.get("/config/app/{app_id}")
        async def get_app_config(app_id: str, user=Depends(require_app_access())):
            ...
    """

    async def _dep(
        request: Request,
        user: dict = Depends(require_authenticated),
    ) -> dict:
        app_id = request.path_params.get(app_id_param)
        if not app_id:
            raise HTTPException(status_code=400, detail="app_id path parameter required")

        # Admins pass through
        if "Admin" in user.get("role_names", []):
            return user

        # Check if user has a role matching the app (convention: role name == app id)
        user_role_names = {r["name"] for r in user.get("roles", [])}
        if app_id in user_role_names:
            return user

        # Check scopes
        if app_id in user.get("scopes", []):
            return user

        logger.warning(
            f"[AUTH] User {user.get('user_id')} denied access to app {app_id}. "
            f"Roles: {user_role_names}"
        )
        raise HTTPException(status_code=403, detail=f"Access denied for app: {app_id}")

    return _dep
