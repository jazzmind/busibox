"""
Audit Log endpoints.

Provides centralized audit logging for all services:
- Create audit entries
- List/filter audit logs
- Get user audit trail

Also includes legacy /authz/audit endpoint for backwards compatibility.
"""

from __future__ import annotations

import json
from typing import Optional, List

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import Config
from oauth.client_auth import verify_client_secret

router = APIRouter()
config = Config()
JWT_ISSUER = config.issuer

# PostgresService instances - will be set by main.py
# pg is production, pg_test is test database (optional)
pg = None
pg_test = None

# Header name for test mode
TEST_MODE_HEADER = "X-Test-Mode"


def set_pg_service(pg_service, pg_test_service=None):
    """Set the shared PostgresService instances."""
    global pg, pg_test
    pg = pg_service
    pg_test = pg_test_service


def _get_pg(request: Request):
    """Get the appropriate PostgresService based on request headers.
    
    If X-Test-Mode: true header is present and test mode is enabled,
    returns the test database service. Otherwise returns production.
    """
    if pg_test and config.test_mode_enabled:
        test_mode = request.headers.get(TEST_MODE_HEADER, "").lower() == "true"
        if test_mode:
            return pg_test
    return pg


# ============================================================================
# Request/Response Models
# ============================================================================


class AuditLogCreate(BaseModel):
    actor_id: str
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    event_type: Optional[str] = None
    target_user_id: Optional[str] = None
    target_role_id: Optional[str] = None
    target_app_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    success: bool = True
    error_message: Optional[str] = None
    details: Optional[dict] = Field(default_factory=dict)


# ============================================================================
# Authentication Helpers
# ============================================================================


async def _require_client_auth(request: Request) -> None:
    """
    Require OAuth client credentials or admin token.
    """
    # Try admin token first
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        if config.admin_token and token == config.admin_token:
            return

    # Try OAuth client credentials in body
    try:
        body = await request.json()
        client_id = body.get("client_id")
        client_secret = body.get("client_secret")

        if client_id and client_secret:
            db = _get_pg(request)
            await db.connect()
            client = await db.get_oauth_client(client_id)
            if client and client.get("is_active"):
                if verify_client_secret(client_secret, client["client_secret_hash"]):
                    return
    except Exception:
        pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: valid admin token or OAuth client credentials required",
    )


async def _check_client_auth_from_body(request: Request, body: dict) -> bool:
    """
    Check if request has valid client auth from parsed body, but don't raise if missing.
    Returns True if authenticated, False otherwise.
    """
    # Try OAuth client credentials in body
    try:
        client_id = body.get("client_id")
        client_secret = body.get("client_secret")

        if client_id and client_secret:
            db = _get_pg(request)
            await db.connect()
            client = await db.get_oauth_client(client_id)
            if client and client.get("is_active"):
                if verify_client_secret(client_secret, client["client_secret_hash"]):
                    return True
    except Exception:
        pass

    return False


def _is_security_event(action: str) -> bool:
    """
    Check if an action is a security-related event that should be allowed
    without authentication.
    
    This includes:
    - Failed authentication attempts (for security monitoring)
    - Pre-authentication events like sending magic links/TOTP codes
      (which happen before the user is authenticated)
    """
    security_actions = [
        # Failed auth attempts
        "user.login.failed",
        "totp.code_failed",
        "passkey.login_failed",
        "magic_link.expired",
        "oauth.token_rejected",
        # Pre-authentication events (user not yet authenticated)
        "magic_link.sent",
        "totp.code_sent",
    ]
    return action in security_actions


async def _require_read_auth(request: Request) -> None:
    """
    Require authentication for read-only operations.
    Accepts admin token in Authorization header.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        if config.admin_token and token == config.admin_token:
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: valid admin token required",
    )


def _format_datetime(dt) -> str:
    """Format datetime for response."""
    if dt is None:
        return ""
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _format_audit_log(log: dict) -> dict:
    """Format audit log for response."""
    return {
        "id": log.get("id"),
        "actor_id": log.get("actor_id"),
        "action": log.get("action"),
        "resource_type": log.get("resource_type"),
        "resource_id": log.get("resource_id"),
        "event_type": log.get("event_type"),
        "target_user_id": log.get("target_user_id"),
        "target_role_id": log.get("target_role_id"),
        "target_app_id": log.get("target_app_id"),
        "ip_address": log.get("ip_address"),
        "user_agent": log.get("user_agent"),
        "success": log.get("success", True),
        "error_message": log.get("error_message"),
        "details": log.get("details", {}),
        "created_at": _format_datetime(log.get("created_at")),
    }


# ============================================================================
# Audit Log Endpoints
# ============================================================================


@router.post("/audit/log")
async def create_audit_log(request: Request):
    """
    Create an audit log entry.
    
    Body:
    - client_id, client_secret (OAuth client auth) - Optional for security events
    - actor_id: string (required) - The user performing the action
    - action: string (required) - The action performed (e.g., "USER_CREATED", "LOGIN")
    - resource_type: string (required) - The type of resource (e.g., "user", "role", "session")
    - resource_id: string (optional) - The specific resource ID
    - event_type: string (optional) - Category of event (e.g., "auth", "admin", "system")
    - target_user_id: string (optional) - If action affects another user
    - target_role_id: string (optional) - If action affects a role
    - target_app_id: string (optional) - If action affects an app
    - ip_address: string (optional) - Client IP address
    - user_agent: string (optional) - Client user agent
    - success: boolean (default: true) - Whether the action succeeded
    - error_message: string (optional) - Error message if failed
    - details: object (optional) - Additional context
    
    Note: Security events (failed logins, etc.) can be logged without authentication
    to allow logging of failed authentication attempts.
    """
    # Parse body once
    body = await request.json()
    action = body.get("action", "")
    
    # Check if this is a security event that should be allowed without auth
    is_security_event = _is_security_event(action)
    
    if not is_security_event:
        # For non-security events, require authentication
        # Check admin token first
        auth_header = request.headers.get("authorization", "")
        is_authenticated = False
        
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
            if config.admin_token and token == config.admin_token:
                is_authenticated = True
        
        # If not authenticated via admin token, check OAuth client credentials in body
        if not is_authenticated:
            is_authenticated = await _check_client_auth_from_body(request, body)
        
        if not is_authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: valid admin token or OAuth client credentials required",
            )
    
    # Validate the body
    try:
        log_data = AuditLogCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db = _get_pg(request)
    await db.connect()

    result = await db.insert_audit_extended(
        actor_id=log_data.actor_id,
        action=log_data.action,
        resource_type=log_data.resource_type,
        resource_id=log_data.resource_id,
        event_type=log_data.event_type,
        target_user_id=log_data.target_user_id,
        target_role_id=log_data.target_role_id,
        target_app_id=log_data.target_app_id,
        ip_address=log_data.ip_address,
        user_agent=log_data.user_agent,
        success=log_data.success,
        error_message=log_data.error_message,
        details=log_data.details,
    )

    return {
        "status": "ok",
        "audit_log_id": result["audit_log_id"],
        "created_at": _format_datetime(result["created_at"]),
    }


@router.get("/audit/logs")
async def list_audit_logs(request: Request):
    """
    List audit logs with pagination and filtering.
    
    Query params:
    - page: int (default: 1)
    - limit: int (default: 50, max: 100)
    - actor_id: string (optional)
    - event_type: string (optional)
    - resource_type: string (optional)
    - target_user_id: string (optional)
    - from_date: ISO timestamp (optional)
    - to_date: ISO timestamp (optional)
    
    Requires admin authentication.
    """
    await _require_read_auth(request)

    params = request.query_params
    page = int(params.get("page", "1"))
    limit = int(params.get("limit", "50"))
    actor_id = params.get("actor_id")
    event_type = params.get("event_type")
    resource_type = params.get("resource_type")
    target_user_id = params.get("target_user_id")
    from_date = params.get("from_date")
    to_date = params.get("to_date")

    # Validate pagination
    if page < 1:
        page = 1
    if limit < 1 or limit > 100:
        limit = 50

    db = _get_pg(request)
    await db.connect()
    result = await db.list_audit_logs(
        page=page,
        limit=limit,
        actor_id=actor_id,
        event_type=event_type,
        resource_type=resource_type,
        target_user_id=target_user_id,
        from_date=from_date,
        to_date=to_date,
    )

    return {
        "logs": [_format_audit_log(log) for log in result["logs"]],
        "pagination": result["pagination"],
    }


@router.get("/audit/logs/user/{user_id}")
async def get_user_audit_trail(request: Request, user_id: str):
    """
    Get audit trail for a specific user.
    
    Returns all audit logs where the user is either the actor or the target.
    
    Query params:
    - limit: int (default: 100, max: 500)
    
    Requires admin authentication.
    """
    await _require_read_auth(request)

    from uuid import UUID
    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user_id format") from e

    params = request.query_params
    limit = int(params.get("limit", "100"))
    if limit < 1 or limit > 500:
        limit = 100

    db = _get_pg(request)
    await db.connect()
    logs = await db.get_user_audit_trail(user_id, limit)

    return {
        "user_id": user_id,
        "logs": [_format_audit_log(log) for log in logs],
    }


# ============================================================================
# Legacy Endpoint (backwards compatibility)
# ============================================================================


async def _extract_actor_from_headers(request: Request) -> tuple[Optional[str], List[dict]]:
    """
    Best-effort extract actor and roles from Authorization header if present.
    Token is expected to be a signed JWT with `sub` and `roles` fields.
    """
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None, []
    token = auth.split(" ", 1)[1]
    try:
        headers = jwt.get_unverified_header(token)
        kid = headers.get("kid")
        if not kid:
            return None, []
        # Look up public key via active JWKS keys stored in DB.
        db = _get_pg(request)
        await db.connect()
        jwks = await db.list_public_jwks()
        key_data = next((k for k in jwks if k.get("kid") == kid), None)
        if not key_data:
            return None, []
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
        data = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=JWT_ISSUER,
            options={"verify_aud": False},  # audit context does not require audience
        )
        actor = data.get("sub")
        roles = data.get("roles") or []
        return actor, roles
    except Exception:
        return None, []
