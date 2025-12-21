"""
Document Role Management API

Endpoints for managing roles on documents:
- GET /files/{file_id}/roles - List roles on a document
- PUT /files/{file_id}/roles - Add/remove roles from a document
- POST /files/{file_id}/share - Convert personal document to shared
"""

import json
import uuid
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.middleware.jwt_auth import ScopeChecker
from api.services.postgres import PostgresService
from services.milvus_service import MilvusService
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()

# Scope dependencies
require_ingest_read = ScopeChecker("ingest.read")
require_ingest_write = ScopeChecker("ingest.write")


# ============================================================================
# Request/Response Models
# ============================================================================

class RoleAssignment(BaseModel):
    """Role assignment with metadata."""
    role_id: str
    role_name: str
    added_at: Optional[str] = None
    added_by: Optional[str] = None


class DocumentRolesResponse(BaseModel):
    """Response for listing document roles."""
    file_id: str
    visibility: str
    roles: List[RoleAssignment]


class UpdateRolesRequest(BaseModel):
    """Request to update roles on a document."""
    add_role_ids: List[str] = Field(default_factory=list, description="Role IDs to add")
    add_role_names: List[str] = Field(default_factory=list, description="Role names to add (paired with add_role_ids)")
    remove_role_ids: List[str] = Field(default_factory=list, description="Role IDs to remove")


class ShareDocumentRequest(BaseModel):
    """Request to convert a personal document to shared."""
    role_ids: List[str] = Field(..., min_items=1, description="Role IDs to assign")
    role_names: List[str] = Field(..., min_items=1, description="Role names (paired with role_ids)")


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/{file_id}/roles", response_model=DocumentRolesResponse, dependencies=[Depends(require_ingest_read)])
async def get_document_roles(file_id: str, request: Request):
    """
    Get roles assigned to a document.
    
    Requires:
    - User must have read access to the document (personal owner or role read permission)
    
    Returns:
    - Document visibility (personal/shared)
    - List of role assignments
    """
    user_id = request.state.user_id
    
    config = Config().to_dict()
    from api.main import pg_service as postgres_service  # Use shared PostgresService instance
    # Connection is already established in startup
    
    try:
        # Get document (RLS will filter by access)
        doc = await postgres_service.get_file_metadata(file_id)
        
        if not doc:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "Document not found or access denied"}
            )
        
        # Get roles for this document
        roles = await postgres_service.get_document_roles(file_id)
        
        return DocumentRolesResponse(
            file_id=file_id,
            visibility=doc.get("visibility", "personal"),
            roles=[
                RoleAssignment(
                    role_id=str(r["role_id"]),
                    role_name=r["role_name"],
                    added_at=str(r["added_at"]) if r.get("added_at") else None,
                    added_by=str(r["added_by"]) if r.get("added_by") else None,
                )
                for r in roles
            ]
        )
    
    except Exception as e:
        logger.error(
            "Failed to get document roles",
            file_id=file_id,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to get document roles", "details": str(e)}
        )
    
    finally:
        # Don't disconnect - pg_service is a singleton shared across requests
        pass

@router.put("/{file_id}/roles", dependencies=[Depends(require_ingest_write)])
async def update_document_roles(
    file_id: str,
    update_request: UpdateRolesRequest,
    request: Request,
):
    """
    Add or remove roles from a document.
    
    Requires:
    - Document must be shared (not personal)
    - User must have 'update' permission on:
      - At least one existing document role (to modify)
      - All roles being added (to add)
      - All roles being removed (to remove)
    - Cannot remove ALL roles (minimum 1 required)
    
    Effects:
    - Updates document_roles table in PostgreSQL
    - Copies/removes vectors in Milvus partitions for each role change
    """
    user_id = request.state.user_id
    user_update_roles = getattr(request.state, "role_ids_update", [])
    
    config = Config().to_dict()
    postgres_service = PostgresService(config, request)
    milvus_service = MilvusService(config)
    
    await postgres_service.connect()
    milvus_service.connect()
    
    try:
        # Get document
        doc = await postgres_service.get_file_metadata(file_id)
        
        if not doc:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "Document not found or access denied"}
            )
        
        # Document must be shared
        if doc.get("visibility") != "shared":
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "Cannot modify roles on personal documents",
                    "hint": "Use POST /files/{file_id}/share to convert to shared first",
                }
            )
        
        # Get current roles
        current_roles = await postgres_service.get_document_roles(file_id)
        current_role_ids = {str(r["role_id"]) for r in current_roles}
        
        # Verify user has update permission on at least one current role
        user_has_access = any(
            role_id in user_update_roles for role_id in current_role_ids
        )
        if not user_has_access:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "You don't have 'update' permission on any of this document's roles",
                }
            )
        
        # Validate roles to add
        for role_id in update_request.add_role_ids:
            if role_id not in user_update_roles:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": f"You don't have 'update' permission on role: {role_id}",
                    }
                )
        
        # Validate roles to remove
        for role_id in update_request.remove_role_ids:
            if role_id not in user_update_roles:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": f"You don't have 'update' permission on role: {role_id}",
                    }
                )
        
        # Calculate new roles
        new_role_ids = (
            current_role_ids
            | set(update_request.add_role_ids)
        ) - set(update_request.remove_role_ids)
        
        # Cannot remove ALL roles
        if not new_role_ids:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "Cannot remove all roles from a document",
                    "hint": "Shared documents must have at least one role",
                }
            )
        
        # Build role name lookup for adding
        role_name_lookup = dict(zip(update_request.add_role_ids, update_request.add_role_names))
        
        # Add roles
        roles_added = []
        for role_id in update_request.add_role_ids:
            if role_id not in current_role_ids:
                role_name = role_name_lookup.get(role_id, f"Unknown-{role_id[:8]}")
                
                # Add to PostgreSQL
                await postgres_service.add_document_role(
                    file_id=file_id,
                    role_id=role_id,
                    role_name=role_name,
                    added_by=user_id,
                )
                
                # Copy vectors to new partition in Milvus
                source_partition = None
                for existing_role_id in current_role_ids:
                    source_partition = f"role_{existing_role_id}"
                    break
                
                if source_partition:
                    try:
                        milvus_service.copy_vectors_to_partition(
                            file_id=file_id,
                            source_partition=source_partition,
                            target_partition=f"role_{role_id}",
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to copy vectors to new partition",
                            file_id=file_id,
                            role_id=role_id,
                            error=str(e),
                        )
                
                roles_added.append(role_id)
                logger.info(
                    "Role added to document",
                    file_id=file_id,
                    role_id=role_id,
                    role_name=role_name,
                    added_by=user_id,
                )
        
        # Remove roles
        roles_removed = []
        for role_id in update_request.remove_role_ids:
            if role_id in current_role_ids:
                # Remove from PostgreSQL
                await postgres_service.remove_document_role(
                    file_id=file_id,
                    role_id=role_id,
                )
                
                # Remove vectors from partition in Milvus
                try:
                    milvus_service.delete_vectors_from_partition(
                        file_id=file_id,
                        partition_name=f"role_{role_id}",
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to delete vectors from partition",
                        file_id=file_id,
                        role_id=role_id,
                        error=str(e),
                    )
                
                roles_removed.append(role_id)
                logger.info(
                    "Role removed from document",
                    file_id=file_id,
                    role_id=role_id,
                    removed_by=user_id,
                )
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "file_id": file_id,
                "roles_added": roles_added,
                "roles_removed": roles_removed,
                "current_roles": list(new_role_ids),
            }
        )
    
    except Exception as e:
        logger.error(
            "Failed to update document roles",
            file_id=file_id,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to update document roles", "details": str(e)}
        )
    
    finally:
        # Don't disconnect - pg_service is a singleton shared across requests
        milvus_service.close()


@router.post("/{file_id}/share", dependencies=[Depends(require_ingest_write)])
async def share_document(
    file_id: str,
    share_request: ShareDocumentRequest,
    request: Request,
):
    """
    Convert a personal document to shared by assigning roles.
    
    Requires:
    - User must be the document owner (personal documents only)
    - User must have 'create' permission on all specified roles
    
    Effects:
    - Changes visibility from 'personal' to 'shared'
    - Assigns specified roles
    - Moves vectors from personal partition to role partitions
    - Owner loses special privileges (becomes role-based access)
    
    Warning:
    - This action cannot be undone
    - Owner may lose access if they don't have read permission on assigned roles
    """
    user_id = request.state.user_id
    user_create_roles = getattr(request.state, "role_ids_create", [])
    
    config = Config().to_dict()
    postgres_service = PostgresService(config, request)
    milvus_service = MilvusService(config)
    
    await postgres_service.connect()
    milvus_service.connect()
    
    try:
        # Get document
        doc = await postgres_service.get_file_metadata(file_id)
        
        if not doc:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "Document not found or access denied"}
            )
        
        # Document must be personal
        if doc.get("visibility") != "personal":
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "Document is already shared",
                    "hint": "Use PUT /files/{file_id}/roles to modify roles",
                }
            )
        
        # User must be owner
        if str(doc.get("owner_id")) != user_id:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"error": "Only the document owner can share a personal document"}
            )
        
        # Validate role permissions
        if len(share_request.role_ids) != len(share_request.role_names):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "role_ids and role_names must have the same length"}
            )
        
        for role_id in share_request.role_ids:
            if role_id not in user_create_roles:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": f"You don't have 'create' permission on role: {role_id}",
                    }
                )
        
        # Update visibility
        await postgres_service.update_file_visibility(
            file_id=file_id,
            visibility="shared",
        )
        
        # Add roles
        role_name_lookup = dict(zip(share_request.role_ids, share_request.role_names))
        for role_id in share_request.role_ids:
            role_name = role_name_lookup.get(role_id, f"Unknown-{role_id[:8]}")
            
            await postgres_service.add_document_role(
                file_id=file_id,
                role_id=role_id,
                role_name=role_name,
                added_by=user_id,
            )
        
        # Move vectors from personal partition to role partitions
        personal_partition = f"personal_{user_id}"
        
        for role_id in share_request.role_ids:
            try:
                milvus_service.copy_vectors_to_partition(
                    file_id=file_id,
                    source_partition=personal_partition,
                    target_partition=f"role_{role_id}",
                )
            except Exception as e:
                logger.warning(
                    "Failed to copy vectors to role partition",
                    file_id=file_id,
                    role_id=role_id,
                    error=str(e),
                )
        
        # Remove from personal partition
        try:
            milvus_service.delete_vectors_from_partition(
                file_id=file_id,
                partition_name=personal_partition,
            )
        except Exception as e:
            logger.warning(
                "Failed to delete vectors from personal partition",
                file_id=file_id,
                error=str(e),
            )
        
        logger.info(
            "Document shared",
            file_id=file_id,
            user_id=user_id,
            role_count=len(share_request.role_ids),
        )
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "file_id": file_id,
                "visibility": "shared",
                "roles": list(share_request.role_ids),
                "warning": "Owner privileges have been removed. Access is now role-based.",
            }
        )
    
    except Exception as e:
        logger.error(
            "Failed to share document",
            file_id=file_id,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to share document", "details": str(e)}
        )
    
    finally:
        # Don't disconnect - pg_service is a singleton shared across requests
        milvus_service.close()

