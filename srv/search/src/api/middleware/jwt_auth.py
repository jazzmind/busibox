"""
JWT Authentication Middleware for Search API

Validates JWT tokens from AI Portal and extracts:
- User identity (sub, email)
- Document role memberships with CRUD permissions

Provides role information for:
- Milvus partition filtering (only search accessible partitions)
- Authorization header passthrough to downstream services
"""

import os
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import jwt
import structlog
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()

# ============================================================================
# Configuration
# ============================================================================

JWT_SECRET = os.environ.get("JWT_SECRET") or \
             os.environ.get("SERVICE_JWT_SECRET") or \
             os.environ.get("SSO_JWT_SECRET") or \
             "default-service-secret-change-in-production"

JWT_ISSUER = os.environ.get("JWT_ISSUER", "ai-portal")
JWT_AUDIENCE = os.environ.get("JWT_AUDIENCE", "search-api")

# Allow legacy X-User-Id header during migration
ALLOW_LEGACY_HEADER = os.environ.get("ALLOW_LEGACY_AUTH", "true").lower() == "true"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class DocumentRole:
    """Document role with CRUD permissions."""
    id: str
    name: str
    permissions: List[str] = field(default_factory=list)
    
    @property
    def can_create(self) -> bool:
        return "create" in self.permissions
    
    @property
    def can_read(self) -> bool:
        return "read" in self.permissions
    
    @property
    def can_update(self) -> bool:
        return "update" in self.permissions
    
    @property
    def can_delete(self) -> bool:
        return "delete" in self.permissions


@dataclass
class UserContext:
    """User context extracted from JWT."""
    user_id: str
    email: Optional[str] = None
    roles: List[DocumentRole] = field(default_factory=list)
    is_legacy: bool = False  # True if authenticated via X-User-Id header
    authorization_header: Optional[str] = None  # For passthrough to downstream services
    
    def get_role_ids_by_permission(self, permission: str) -> List[str]:
        """Get role IDs where user has specific permission."""
        return [r.id for r in self.roles if permission in r.permissions]
    
    @property
    def read_role_ids(self) -> List[str]:
        return self.get_role_ids_by_permission("read")
    
    @property
    def read_role_names(self) -> List[str]:
        return [r.name for r in self.roles if "read" in r.permissions]


# ============================================================================
# JWT Parsing
# ============================================================================

def parse_jwt_token(token: str) -> Optional[dict]:
    """
    Verify and decode a JWT token.
    
    Returns decoded payload or None if invalid.
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
        return None
    except jwt.InvalidAudienceError:
        logger.warning("JWT invalid audience", expected=JWT_AUDIENCE)
        return None
    except jwt.InvalidIssuerError:
        logger.warning("JWT invalid issuer", expected=JWT_ISSUER)
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("JWT invalid token", error=str(e))
        return None


def extract_user_context(payload: dict, auth_header: str) -> UserContext:
    """Extract UserContext from JWT payload."""
    user_id = payload.get("sub", "")
    email = payload.get("email")
    
    roles = []
    for role_data in payload.get("roles", []):
        role = DocumentRole(
            id=role_data.get("id", ""),
            name=role_data.get("name", ""),
            permissions=role_data.get("permissions", [])
        )
        roles.append(role)
    
    return UserContext(
        user_id=user_id,
        email=email,
        roles=roles,
        is_legacy=False,
        authorization_header=auth_header
    )


# ============================================================================
# Middleware
# ============================================================================

class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate JWT tokens and extract user context.
    
    Supports both:
    1. JWT via Authorization: Bearer <token> (preferred)
    2. Legacy X-User-Id header (for backward compatibility)
    
    Extracts role permissions for:
    - Milvus partition filtering
    - Authorization passthrough to downstream services
    """
    
    async def dispatch(self, request: Request, call_next: Callable):
        """Process request and validate authentication."""
        
        # Skip auth for health endpoints
        if request.url.path == "/health":
            return await call_next(request)
        
        # Try JWT authentication first
        auth_header = request.headers.get("authorization")
        user_context = None
        
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            payload = parse_jwt_token(token)
            
            if payload:
                user_context = extract_user_context(payload, auth_header)
                logger.debug(
                    "JWT authenticated",
                    user_id=user_context.user_id,
                    roles=len(user_context.roles),
                    readable_roles=len(user_context.read_role_ids),
                    path=request.url.path
                )
            else:
                # Invalid JWT
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"error": "Invalid or expired JWT token"}
                )
        
        # Fall back to legacy X-User-Id header if allowed
        elif ALLOW_LEGACY_HEADER:
            user_id_header = request.headers.get("x-user-id")
            
            if user_id_header:
                try:
                    user_id = str(uuid.UUID(user_id_header))
                    user_context = UserContext(
                        user_id=user_id,
                        is_legacy=True,
                        authorization_header=None
                    )
                    logger.debug(
                        "Legacy auth (X-User-Id)",
                        user_id=user_id,
                        path=request.url.path
                    )
                except ValueError:
                    return JSONResponse(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        content={"error": "Invalid X-User-Id format (must be UUID)"}
                    )
        
        # No authentication provided
        if not user_context:
            logger.warning(
                "Missing authentication",
                path=request.url.path,
                method=request.method
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "Missing Authorization header or X-User-Id"}
            )
        
        # Attach user context to request state
        request.state.user_id = user_context.user_id
        request.state.user_email = user_context.email
        request.state.user_roles = user_context.roles
        request.state.is_legacy_auth = user_context.is_legacy
        request.state.authorization = user_context.authorization_header
        
        # Store readable role IDs for Milvus partition filtering
        request.state.readable_role_ids = user_context.read_role_ids
        request.state.readable_role_names = user_context.read_role_names
        
        # Process request
        response = await call_next(request)
        return response


# ============================================================================
# Partition Building Utilities
# ============================================================================

def get_accessible_partitions(request: Request) -> List[str]:
    """
    Build list of Milvus partitions user can search.
    
    Returns partition names:
    - personal_{user_id}: User's personal documents
    - role_{role_id}: Shared documents by role
    """
    user_id = getattr(request.state, "user_id", None)
    readable_role_ids = getattr(request.state, "readable_role_ids", [])
    
    partitions = []
    
    # Personal partition
    if user_id:
        partitions.append(f"personal_{user_id}")
    
    # Role-based partitions
    for role_id in readable_role_ids:
        partitions.append(f"role_{role_id}")
    
    return partitions


def get_partition_names_for_search(request: Request, include_personal: bool = True) -> List[str]:
    """
    Get partition names for a search query.
    
    Args:
        request: FastAPI request with user context
        include_personal: Whether to include personal partition (default True)
    
    Returns:
        List of partition names to search
    """
    user_id = getattr(request.state, "user_id", None)
    readable_role_ids = getattr(request.state, "readable_role_ids", [])
    
    partitions = []
    
    if include_personal and user_id:
        partitions.append(f"personal_{user_id}")
    
    for role_id in readable_role_ids:
        partitions.append(f"role_{role_id}")
    
    # If no partitions available, return empty list
    # This will result in no search results (correct behavior)
    return partitions

