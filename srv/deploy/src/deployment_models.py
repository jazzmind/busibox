"""
Pydantic models for deployment management API.

Request/response schemas for GitHub connections, deployment configs,
deployments, app secrets, and GitHub releases.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum


# ============================================================================
# Enums
# ============================================================================

class DeploymentStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class DeploymentEnvironment(str, Enum):
    PRODUCTION = "PRODUCTION"
    STAGING = "STAGING"


class DeploymentType(str, Enum):
    RELEASE = "RELEASE"
    BRANCH = "BRANCH"


class SecretType(str, Enum):
    DATABASE_URL = "DATABASE_URL"
    API_KEY = "API_KEY"
    JWT_SECRET = "JWT_SECRET"
    OAUTH_SECRET = "OAUTH_SECRET"
    CUSTOM = "CUSTOM"


# ============================================================================
# GitHub Connection
# ============================================================================

class GitHubConnectionCreate(BaseModel):
    """Fields needed when storing a GitHub OAuth connection."""
    access_token: str
    refresh_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    github_user_id: str
    github_username: str
    scopes: List[str] = Field(default_factory=list)


class GitHubConnectionRead(BaseModel):
    """Public representation (no tokens)."""
    id: str
    user_id: str
    github_user_id: str
    github_username: str
    scopes: List[str]
    token_expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


# ============================================================================
# Deployment Config
# ============================================================================

class DeploymentConfigCreate(BaseModel):
    app_id: str
    github_repo_owner: str
    github_repo_name: str
    github_branch: str = "main"
    deploy_path: str
    port: int
    health_endpoint: str = "/api/health"
    build_command: Optional[str] = None
    start_command: Optional[str] = None
    auto_deploy_enabled: bool = False
    staging_enabled: bool = False
    staging_port: Optional[int] = None
    staging_path: Optional[str] = None


class DeploymentConfigUpdate(BaseModel):
    github_branch: Optional[str] = None
    deploy_path: Optional[str] = None
    port: Optional[int] = None
    health_endpoint: Optional[str] = None
    build_command: Optional[str] = None
    start_command: Optional[str] = None
    auto_deploy_enabled: Optional[bool] = None
    staging_enabled: Optional[bool] = None
    staging_port: Optional[int] = None
    staging_path: Optional[str] = None


class DeploymentConfigRead(BaseModel):
    id: str
    app_id: str
    github_connection_id: str
    github_repo_owner: str
    github_repo_name: str
    github_branch: str
    deploy_path: str
    port: int
    health_endpoint: str
    build_command: Optional[str] = None
    start_command: Optional[str] = None
    auto_deploy_enabled: bool
    staging_enabled: bool
    staging_port: Optional[int] = None
    staging_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # Optional includes
    github_username: Optional[str] = None
    latest_deployment: Optional["DeploymentRead"] = None
    secrets: Optional[List["AppSecretRead"]] = None
    deployments: Optional[List["DeploymentRead"]] = None


# ============================================================================
# Deployment (history)
# ============================================================================

class DeploymentCreate(BaseModel):
    deployment_config_id: str
    environment: DeploymentEnvironment = DeploymentEnvironment.PRODUCTION
    deployment_type: DeploymentType = DeploymentType.RELEASE
    release_tag: Optional[str] = None
    release_id: Optional[str] = None
    commit_sha: Optional[str] = None
    deployed_by: str
    previous_deployment_id: Optional[str] = None
    is_rollback: bool = False


class DeploymentUpdate(BaseModel):
    status: Optional[DeploymentStatus] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    logs: Optional[str] = None


class DeploymentRead(BaseModel):
    id: str
    deployment_config_id: str
    environment: str
    status: str
    deployment_type: str
    release_tag: Optional[str] = None
    release_id: Optional[str] = None
    commit_sha: Optional[str] = None
    deployed_by: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    logs: Optional[str] = None
    previous_deployment_id: Optional[str] = None
    is_rollback: bool


# ============================================================================
# App Secret
# ============================================================================

class AppSecretCreate(BaseModel):
    key: str
    value: str  # plaintext; encrypted server-side
    type: SecretType = SecretType.CUSTOM
    description: Optional[str] = None


class AppSecretRead(BaseModel):
    """Public representation (no encrypted value)."""
    id: str
    deployment_config_id: str
    key: str
    type: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# ============================================================================
# GitHub Release
# ============================================================================

class GitHubReleaseUpsert(BaseModel):
    release_id: str
    tag_name: str
    release_name: Optional[str] = None
    body: Optional[str] = None
    commit_sha: Optional[str] = None
    published_at: datetime
    is_prerelease: bool = False
    is_draft: bool = False
    tarball_url: Optional[str] = None


class GitHubReleaseRead(BaseModel):
    id: str
    deployment_config_id: str
    release_id: str
    tag_name: str
    release_name: Optional[str] = None
    body: Optional[str] = None
    commit_sha: Optional[str] = None
    published_at: datetime
    is_prerelease: bool
    is_draft: bool
    tarball_url: Optional[str] = None
    created_at: datetime
    # Added by application logic
    is_currently_deployed: Optional[bool] = None


# ============================================================================
# App Database
# ============================================================================

class AppDatabaseCreate(BaseModel):
    database_name: str
    database_user: str
    password: str  # plaintext; encrypted server-side


class AppDatabaseRead(BaseModel):
    id: str
    deployment_config_id: str
    database_name: str
    database_user: str
    host: str
    port: int
    created_at: datetime
    updated_at: datetime


# ============================================================================
# Helper models
# ============================================================================

class NextPortResponse(BaseModel):
    port: int


class GitHubAuthUrlResponse(BaseModel):
    auth_url: str


class GitHubCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None


class GitHubVerifyRepoRequest(BaseModel):
    github_repo_owner: str
    github_repo_name: str


class GitHubVerifyRepoResponse(BaseModel):
    verified: bool
    repository: Optional[dict] = None
    error: Optional[str] = None


class ReleaseSyncResponse(BaseModel):
    success: bool
    count: int
    releases: List[GitHubReleaseRead]


# Forward ref resolution
DeploymentConfigRead.model_rebuild()
