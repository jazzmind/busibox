"""
Deployment Service Models

Data models for deployment operations.
"""

from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime


class DatabaseConfig(BaseModel):
    """Database configuration from manifest"""
    required: bool
    preferredName: str = Field(..., pattern=r'^[a-z0-9_]+$')
    schemaManagement: Literal['prisma', 'migrations', 'manual'] = 'prisma'
    seedCommand: Optional[str] = None


class BusiboxManifest(BaseModel):
    """App manifest from busibox.json"""
    name: str
    id: str = Field(..., pattern=r'^[a-z0-9-]+$')
    version: str
    description: str
    icon: str
    defaultPath: str = Field(..., pattern=r'^/[a-z0-9-_]+$')
    defaultPort: int = Field(..., ge=1000, le=65535)
    healthEndpoint: str
    buildCommand: str
    startCommand: str
    appMode: Literal['frontend', 'prisma']
    database: Optional[DatabaseConfig] = None
    requiredEnvVars: List[str] = []
    optionalEnvVars: List[str] = []
    busiboxAppVersion: Optional[str] = None


class DeploymentConfig(BaseModel):
    """Deployment configuration"""
    githubRepoOwner: str = ''  # Empty for local dev mode
    githubRepoName: str = ''   # Empty for local dev mode
    githubBranch: str = 'main'
    githubToken: Optional[str] = None
    environment: Literal['production', 'staging'] = 'production'
    secrets: Dict[str, str] = {}  # Legacy: Additional env vars (use envVars instead)
    envVars: Dict[str, str] = {}  # Environment variables for build and runtime
    # Monorepo support: subdirectory within the cloned repo containing this app
    monorepoAppDir: Optional[str] = None  # e.g., "apps/portal" for busibox-frontend
    # Local development mode
    localDevDir: Optional[str] = None  # Directory name in /srv/dev-apps/
    devMode: bool = False


class DeployRequest(BaseModel):
    """Request to deploy an app"""
    manifest: BusiboxManifest
    config: DeploymentConfig


class DatabaseProvisionResult(BaseModel):
    """Result of database provisioning"""
    success: bool
    databaseName: Optional[str] = None
    databaseUser: Optional[str] = None
    databaseUrl: Optional[str] = None
    error: Optional[str] = None


class DeploymentStatus(BaseModel):
    """Deployment status"""
    deploymentId: str
    status: Literal['pending', 'provisioning_db', 'deploying', 'configuring_nginx', 'completed', 'failed']
    progress: int = Field(..., ge=0, le=100)
    currentStep: str
    logs: List[str] = []
    startedAt: datetime
    completedAt: Optional[datetime] = None
    error: Optional[str] = None


class DeploymentResult(BaseModel):
    """Result of deployment"""
    deploymentId: str
    status: str
    appUrl: Optional[str] = None


# =============================================================================
# CONFIG MODELS - Runtime configuration stored in database
# =============================================================================

class ConfigValue(BaseModel):
    """A single configuration value"""
    key: str
    value: str
    encrypted: bool = False
    category: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ConfigSetRequest(BaseModel):
    """Request to set a configuration value"""
    value: str
    encrypted: bool = False
    category: Optional[str] = None
    description: Optional[str] = None


class ConfigBulkSetRequest(BaseModel):
    """Request to set multiple configuration values"""
    configs: Dict[str, ConfigSetRequest]


class ConfigListResponse(BaseModel):
    """Response for listing all config keys"""
    configs: List[ConfigValue]
    total: int


class ConfigCategory(BaseModel):
    """A configuration category with its keys"""
    category: str
    keys: List[str]
    count: int
