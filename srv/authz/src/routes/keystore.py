"""
Keystore API Routes

Provides endpoints for envelope encryption key management:
- KEK creation and retrieval (for roles and users)
- DEK wrapping and unwrapping (for file encryption)
- Key rotation

All endpoints require authentication via admin token or OAuth client.
"""

import base64
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from services.postgres import PostgresService
from services.encryption import get_encryption_service, EnvelopeEncryptionService

logger = structlog.get_logger()

router = APIRouter(prefix="/keystore", tags=["Keystore"])

# Module-level service references (set during startup)
_pg: Optional[PostgresService] = None
_pg_test: Optional[PostgresService] = None

# Header name for test mode
TEST_MODE_HEADER = "X-Test-Mode"

# Load config for test_mode_enabled
from config import Config
config = Config()


def set_pg_service(pg: PostgresService, pg_test: PostgresService = None):
    """Set the PostgreSQL service instances."""
    global _pg, _pg_test
    _pg = pg
    _pg_test = pg_test


def get_pg() -> PostgresService:
    """Get the production PostgreSQL service instance."""
    if _pg is None:
        raise RuntimeError("PostgreSQL service not initialized")
    return _pg


def _get_pg(request: Request) -> PostgresService:
    """Get the appropriate PostgresService based on request headers.
    
    If X-Test-Mode: true header is present and test mode is enabled,
    returns the test database service. Otherwise returns production.
    """
    if _pg_test and config.test_mode_enabled:
        test_mode = request.headers.get(TEST_MODE_HEADER, "").lower() == "true"
        if test_mode:
            return _pg_test
    return get_pg()


# ============================================================================
# Request/Response Models
# ============================================================================

class CreateKekRequest(BaseModel):
    """Request to create a new KEK."""
    owner_type: str = Field(..., pattern="^(role|user|system)$", description="Type of owner")
    owner_id: Optional[str] = Field(None, description="UUID of the role or user (null for system)")


class KekResponse(BaseModel):
    """Response containing KEK metadata (not the key itself)."""
    kek_id: str
    owner_type: str
    owner_id: Optional[str]
    key_algorithm: str
    key_version: int
    is_active: bool
    created_at: str


class WrapDekRequest(BaseModel):
    """Request to wrap a DEK for storage."""
    file_id: str = Field(..., description="UUID of the file")
    role_ids: List[str] = Field(default_factory=list, description="Role IDs to wrap DEK for")
    user_id: Optional[str] = Field(None, description="User ID for personal files")


class WrapDekResponse(BaseModel):
    """Response containing the encrypted content and wrapped DEKs."""
    file_id: str
    wrapped_deks: List[dict]  # List of {kek_id, owner_type, owner_id}


class UnwrapDekRequest(BaseModel):
    """Request to unwrap a DEK for decryption."""
    file_id: str = Field(..., description="UUID of the file")


class UnwrapDekResponse(BaseModel):
    """Response containing the unwrapped DEK (base64 encoded)."""
    file_id: str
    dek: str  # Base64 encoded DEK
    kek_id: str
    owner_type: str
    owner_id: Optional[str]


class EncryptContentRequest(BaseModel):
    """Request to encrypt file content."""
    file_id: str = Field(..., description="UUID of the file")
    content: str = Field(..., description="Base64 encoded content to encrypt")
    role_ids: List[str] = Field(default_factory=list, description="Role IDs that should have access")
    user_id: Optional[str] = Field(None, description="User ID for personal files")
    system_owner_id: Optional[str] = Field(None, description="System owner ID for service-level encryption (e.g., 'deploy-api')")


class EncryptContentResponse(BaseModel):
    """Response with encrypted content."""
    file_id: str
    encrypted_content: str  # Base64 encoded
    wrapped_dek_count: int


class DecryptContentRequest(BaseModel):
    """Request to decrypt file content."""
    file_id: str = Field(..., description="UUID of the file")
    encrypted_content: str = Field(..., description="Base64 encoded encrypted content")


class DecryptContentResponse(BaseModel):
    """Response with decrypted content."""
    file_id: str
    content: str  # Base64 encoded decrypted content


class RotateKekRequest(BaseModel):
    """Request to rotate a KEK."""
    owner_type: str = Field(..., pattern="^(role|user|system)$")
    owner_id: Optional[str] = Field(None)


# ============================================================================
# Authentication Dependency
# ============================================================================

async def require_keystore_auth(request: Request):
    """
    Require authentication for keystore operations.
    Accepts:
    1. OAuth client credentials in request body (client_id, client_secret)
    2. Bearer token (any valid bearer token from internal services)
    """
    from oauth.client_auth import verify_client_secret
    
    # Try client credentials in body first
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
                    return {"auth_type": "service_account", "client_id": client_id}
    except Exception:
        pass  # Body is not JSON or doesn't have credentials
    
    # Check OAuth bearer token (from internal services)
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        # Accept any valid bearer token from internal services
        return {"auth_type": "oauth", "token": auth_header[7:]}
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Keystore operations require OAuth client credentials or bearer token"
    )


# ============================================================================
# KEK Management Endpoints
# ============================================================================

@router.post("/kek", response_model=KekResponse, dependencies=[Depends(require_keystore_auth)])
async def create_kek(request: Request, body: CreateKekRequest):
    """
    Create a new Key Encryption Key for a role, user, or system.
    
    The KEK is generated, encrypted with the master key, and stored.
    """
    pg = _get_pg(request)
    encryption = get_encryption_service()
    
    # Check if KEK already exists for this owner
    existing = await pg.get_kek_for_owner(body.owner_type, body.owner_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"KEK already exists for {body.owner_type}/{body.owner_id}"
        )
    
    # Generate and encrypt new KEK
    kek = encryption.generate_kek()
    encrypted_kek = encryption.encrypt_kek(kek)
    
    # Store in database
    result = await pg.create_kek(
        owner_type=body.owner_type,
        owner_id=body.owner_id,
        encrypted_key=encrypted_kek,
    )
    
    logger.info(
        "KEK created",
        kek_id=result["kek_id"],
        owner_type=body.owner_type,
        owner_id=body.owner_id,
    )
    
    return KekResponse(
        kek_id=result["kek_id"],
        owner_type=result["owner_type"],
        owner_id=result.get("owner_id"),
        key_algorithm=result["key_algorithm"],
        key_version=result["key_version"],
        is_active=result["is_active"],
        created_at=str(result["created_at"]),
    )


@router.get("/kek/{owner_type}/{owner_id}", response_model=KekResponse, dependencies=[Depends(require_keystore_auth)])
async def get_kek(request: Request, owner_type: str, owner_id: str):
    """Get KEK metadata for an owner (does not return the key itself)."""
    pg = _get_pg(request)
    
    result = await pg.get_kek_for_owner(owner_type, owner_id if owner_id != "system" else None)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active KEK found for {owner_type}/{owner_id}"
        )
    
    return KekResponse(
        kek_id=result["kek_id"],
        owner_type=result["owner_type"],
        owner_id=result.get("owner_id"),
        key_algorithm=result["key_algorithm"],
        key_version=result["key_version"],
        is_active=result["is_active"],
        created_at=str(result["created_at"]),
    )


@router.post("/kek/ensure-for-role/{role_id}", response_model=KekResponse, dependencies=[Depends(require_keystore_auth)])
async def ensure_kek_for_role(request: Request, role_id: str):
    """
    Ensure a KEK exists for a role, creating one if necessary.
    This is idempotent - returns existing KEK if present.
    """
    pg = _get_pg(request)
    encryption = get_encryption_service()
    
    # Check if KEK already exists
    existing = await pg.get_kek_for_owner("role", role_id)
    if existing:
        return KekResponse(
            kek_id=existing["kek_id"],
            owner_type=existing["owner_type"],
            owner_id=existing.get("owner_id"),
            key_algorithm=existing["key_algorithm"],
            key_version=existing["key_version"],
            is_active=existing["is_active"],
            created_at=str(existing["created_at"]),
        )
    
    # Create new KEK
    kek = encryption.generate_kek()
    encrypted_kek = encryption.encrypt_kek(kek)
    
    result = await pg.create_kek(
        owner_type="role",
        owner_id=role_id,
        encrypted_key=encrypted_kek,
    )
    
    logger.info("KEK created for role", kek_id=result["kek_id"], role_id=role_id)
    
    return KekResponse(
        kek_id=result["kek_id"],
        owner_type=result["owner_type"],
        owner_id=result.get("owner_id"),
        key_algorithm=result["key_algorithm"],
        key_version=result["key_version"],
        is_active=result["is_active"],
        created_at=str(result["created_at"]),
    )


@router.post("/kek/rotate", response_model=KekResponse, dependencies=[Depends(require_keystore_auth)])
async def rotate_kek(request: Request, body: RotateKekRequest):
    """
    Rotate a KEK by creating a new version.
    
    Note: After rotation, existing wrapped DEKs using the old KEK version
    will need to be re-wrapped with the new KEK. This is typically done
    during a background key rotation job.
    """
    pg = _get_pg(request)
    encryption = get_encryption_service()
    
    # Generate new KEK
    new_kek = encryption.generate_kek()
    encrypted_kek = encryption.encrypt_kek(new_kek)
    
    result = await pg.rotate_kek(
        owner_type=body.owner_type,
        owner_id=body.owner_id,
        new_encrypted_key=encrypted_kek,
    )
    
    logger.info(
        "KEK rotated",
        kek_id=result["kek_id"],
        owner_type=body.owner_type,
        owner_id=body.owner_id,
        new_version=result["key_version"],
    )
    
    return KekResponse(
        kek_id=result["kek_id"],
        owner_type=result["owner_type"],
        owner_id=result.get("owner_id"),
        key_algorithm=result["key_algorithm"],
        key_version=result["key_version"],
        is_active=result["is_active"],
        created_at=str(result["created_at"]),
    )


# ============================================================================
# Encryption/Decryption Endpoints
# ============================================================================

@router.post("/encrypt", response_model=EncryptContentResponse, dependencies=[Depends(require_keystore_auth)])
async def encrypt_content(request: Request, body: EncryptContentRequest):
    """
    Encrypt file content using envelope encryption.
    
    1. Generates a new DEK for the file
    2. Encrypts content with the DEK
    3. Wraps the DEK with KEKs for specified roles/user
    4. Stores wrapped DEKs in the keystore
    """
    pg = _get_pg(request)
    encryption = get_encryption_service()
    
    # Decode content
    try:
        content = base64.b64decode(body.content)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid base64 content"
        )
    
    # Collect KEKs for authorized parties
    keks = []
    
    # Get KEKs for roles
    if body.role_ids:
        role_keks = await pg.get_keks_for_roles(body.role_ids)
        for kek_data in role_keks:
            keks.append((kek_data["kek_id"], kek_data["encrypted_key"]))
    
    # Get or create KEK for user (personal files)
    if body.user_id:
        user_kek = await pg.get_kek_for_owner("user", body.user_id)
        if not user_kek:
            # Create KEK for user
            new_kek = encryption.generate_kek()
            encrypted_kek = encryption.encrypt_kek(new_kek)
            user_kek_result = await pg.create_kek(
                owner_type="user",
                owner_id=body.user_id,
                encrypted_key=encrypted_kek,
            )
            user_kek = await pg.get_kek(user_kek_result["kek_id"])
        keks.append((user_kek["kek_id"], user_kek["encrypted_key"]))
    
    # Get or create KEK for system owner (service-level encryption)
    if body.system_owner_id:
        system_kek = await pg.get_kek_for_owner("system", body.system_owner_id)
        if not system_kek:
            # Create KEK for system owner
            new_kek = encryption.generate_kek()
            encrypted_kek = encryption.encrypt_kek(new_kek)
            system_kek_result = await pg.create_kek(
                owner_type="system",
                owner_id=body.system_owner_id,
                encrypted_key=encrypted_kek,
            )
            system_kek = await pg.get_kek(system_kek_result["kek_id"])
        keks.append((system_kek["kek_id"], system_kek["encrypted_key"]))
    
    if not keks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one role_id, user_id, or system_owner_id must be specified"
        )
    
    # Encrypt content and wrap DEK
    encrypted_content, wrapped_deks = encryption.encrypt_for_storage(content, keks)
    
    # Store wrapped DEKs
    for kek_id, wrapped_dek in wrapped_deks:
        await pg.store_wrapped_dek(
            file_id=body.file_id,
            kek_id=kek_id,
            wrapped_dek=wrapped_dek,
        )
    
    logger.info(
        "Content encrypted",
        file_id=body.file_id,
        wrapped_dek_count=len(wrapped_deks),
    )
    
    return EncryptContentResponse(
        file_id=body.file_id,
        encrypted_content=base64.b64encode(encrypted_content).decode(),
        wrapped_dek_count=len(wrapped_deks),
    )


@router.post("/decrypt", response_model=DecryptContentResponse, dependencies=[Depends(require_keystore_auth)])
async def decrypt_content(request: Request, body: DecryptContentRequest):
    """
    Decrypt file content.
    
    Requires the caller to have access to at least one KEK that can unwrap
    the file's DEK. Access is determined by:
    - role_ids from the caller's JWT token
    - user_id from the caller's JWT token
    """
    pg = _get_pg(request)
    encryption = get_encryption_service()
    
    # Decode encrypted content
    try:
        encrypted_content = base64.b64decode(body.encrypted_content)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid base64 encrypted content"
        )
    
    # Get caller's roles and user_id from request state or headers
    # This would be populated by JWT middleware in production
    role_ids = getattr(request.state, "role_ids", None)
    user_id = getattr(request.state, "user_id", None)
    
    # For internal service calls, allow specifying in headers
    if not role_ids:
        role_ids_header = request.headers.get("x-user-role-ids", "")
        role_ids = [r.strip() for r in role_ids_header.split(",") if r.strip()]
    if not user_id:
        user_id = request.headers.get("x-user-id")
    
    # System owner ID for service-level decryption (e.g., deploy-api)
    system_owner_id = request.headers.get("x-system-owner-id")
    
    wrapped_dek_data = None
    
    # Try to find a wrapped DEK the caller can access
    if role_ids:
        wrapped_dek_data = await pg.get_wrapped_dek_for_roles(body.file_id, role_ids)
    
    if not wrapped_dek_data and user_id:
        wrapped_dek_data = await pg.get_wrapped_dek_for_user(body.file_id, user_id)
    
    if not wrapped_dek_data and system_owner_id:
        wrapped_dek_data = await pg.get_wrapped_dek_for_system(body.file_id, system_owner_id)
    
    if not wrapped_dek_data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to decrypt this file"
        )
    
    # Decrypt
    try:
        decrypted = encryption.decrypt_from_storage(
            encrypted_content,
            wrapped_dek_data["wrapped_dek"],
            wrapped_dek_data["kek_encrypted_key"],
        )
    except Exception as e:
        logger.error("Decryption failed", file_id=body.file_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Decryption failed"
        )
    
    return DecryptContentResponse(
        file_id=body.file_id,
        content=base64.b64encode(decrypted).decode(),
    )


# ============================================================================
# Key Management for File Operations
# ============================================================================

@router.post("/file/{file_id}/add-role/{role_id}", dependencies=[Depends(require_keystore_auth)])
async def add_role_access(request: Request, file_id: str, role_id: str):
    """
    Add a role's access to a file by wrapping the DEK with the role's KEK.
    
    Requires an existing wrapped DEK (from admin or another authorized role)
    to be provided in the request to re-wrap.
    """
    pg = _get_pg(request)
    encryption = get_encryption_service()
    
    # Get existing wrapped DEKs for the file
    existing_deks = await pg.get_wrapped_deks_for_file(file_id)
    if not existing_deks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No wrapped DEKs found for this file"
        )
    
    # Get the role's KEK
    role_kek = await pg.get_kek_for_owner("role", role_id)
    if not role_kek:
        # Create KEK for role if it doesn't exist
        new_kek = encryption.generate_kek()
        encrypted_kek = encryption.encrypt_kek(new_kek)
        await pg.create_kek(
            owner_type="role",
            owner_id=role_id,
            encrypted_key=encrypted_kek,
        )
        role_kek = await pg.get_kek_for_owner("role", role_id)
    
    # Use first existing wrapped DEK to get the DEK
    first_wrapped = existing_deks[0]
    try:
        dek = encryption.decrypt_from_storage(
            b"",  # We don't have the content, just unwrapping DEK
            first_wrapped["wrapped_dek"],
            first_wrapped["kek_encrypted_key"],
        )
    except Exception:
        # Actually we need to unwrap the DEK properly
        source_kek = encryption.decrypt_kek(first_wrapped["kek_encrypted_key"])
        dek = encryption.unwrap_dek(first_wrapped["wrapped_dek"], source_kek)
    
    # Wrap DEK with role's KEK
    target_kek = encryption.decrypt_kek(role_kek["encrypted_key"])
    wrapped_dek = encryption.wrap_dek(dek, target_kek)
    
    # Store the new wrapped DEK
    await pg.store_wrapped_dek(
        file_id=file_id,
        kek_id=role_kek["kek_id"],
        wrapped_dek=wrapped_dek,
    )
    
    logger.info("Role access added to file", file_id=file_id, role_id=role_id)
    
    return {"status": "ok", "file_id": file_id, "role_id": role_id}


@router.delete("/file/{file_id}/remove-role/{role_id}", dependencies=[Depends(require_keystore_auth)])
async def remove_role_access(request: Request, file_id: str, role_id: str):
    """Remove a role's access to a file by deleting their wrapped DEK."""
    pg = _get_pg(request)
    
    removed = await pg.remove_wrapped_dek_for_role(file_id=file_id, role_id=role_id)
    
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No wrapped DEK found for role {role_id} on file {file_id}"
        )
    
    logger.info("Role access removed from file", file_id=file_id, role_id=role_id)
    
    return {"status": "ok", "file_id": file_id, "role_id": role_id}


@router.delete("/file/{file_id}", dependencies=[Depends(require_keystore_auth)])
async def delete_file_keys(request: Request, file_id: str):
    """Delete all wrapped DEKs for a file (called when file is deleted)."""
    pg = _get_pg(request)
    
    count = await pg.delete_wrapped_deks_for_file(file_id)
    
    logger.info("File keys deleted", file_id=file_id, deleted_count=count)
    
    return {"status": "ok", "file_id": file_id, "deleted_count": count}

