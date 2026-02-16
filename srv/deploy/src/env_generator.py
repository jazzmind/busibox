"""
Environment Variable Generator

Generates environment variables for deployed apps based on:
- Manifest requirements
- Known service endpoints
- Database credentials
- Container configuration

DNS Hostnames:
--------------
On Proxmox, services are accessed via DNS hostnames defined in /etc/hosts
(configured by the internal_dns Ansible role). This allows consistent
configuration regardless of network topology.
"""

import logging
from typing import Dict, Optional
from .models import BusiboxManifest, DeploymentConfig
from .config import config
from .core_app_executor import is_docker_environment

logger = logging.getLogger(__name__)


def get_service_endpoints(environment: str = 'production') -> Dict[str, str]:
    """
    Get service endpoint URLs based on environment.
    
    In Docker: Use container hostnames (Docker's internal DNS)
    In Proxmox: Use DNS hostnames (resolved via /etc/hosts from internal_dns role)
    
    Note: Both environments now use hostnames - the main difference is the
    DNS resolution mechanism (Docker internal DNS vs /etc/hosts).
    """
    if is_docker_environment():
        return {
            # Database
            'POSTGRES_HOST': 'postgres',
            'POSTGRES_PORT': '5432',
            
            # Auth
            'AUTHZ_BASE_URL': 'http://authz-api:8010',
            
            # LLM
            'LITELLM_BASE_URL': 'http://litellm:4000/v1',
            
            # APIs
            'AGENT_API_URL': 'http://agent-api:8000',
            'DATA_API_URL': 'http://data-api:8002',
            'SEARCH_API_URL': 'http://search-api:8003',
            
            # Storage
            'MINIO_ENDPOINT': 'minio:9000',
            'REDIS_HOST': 'redis',
            'REDIS_PORT': '6379',
        }
    else:
        # Proxmox LXC environment - use DNS hostnames from internal_dns role
        # These hostnames are resolved via /etc/hosts on each container
        # No need for hardcoded IPs - hostnames work across environments
        return {
            # Database
            'POSTGRES_HOST': 'postgres',
            'POSTGRES_PORT': '5432',
            
            # Auth
            'AUTHZ_BASE_URL': 'http://authz-api:8010',
            
            # LLM
            'LITELLM_BASE_URL': 'http://litellm:4000/v1',
            
            # APIs
            'AGENT_API_URL': 'http://agent-api:8000',
            'DATA_API_URL': 'http://data-api:8002',
            'SEARCH_API_URL': 'http://search-api:8003',
            
            # Storage
            'MINIO_ENDPOINT': 'minio:9000',
            'REDIS_HOST': 'redis',
            'REDIS_PORT': '6379',
        }


def generate_env_vars(
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig,
    database_url: Optional[str] = None
) -> Dict[str, str]:
    """
    Generate environment variables for an app deployment.
    
    Args:
        manifest: App manifest from busibox.json
        deploy_config: Deployment configuration
        database_url: Provisioned database URL (if applicable)
    
    Returns:
        Dictionary of environment variables
    """
    env = {}
    
    # Base environment
    env['NODE_ENV'] = 'production' if deploy_config.environment == 'production' else 'development'
    env['PORT'] = str(manifest.defaultPort)
    # Use stable app identity for auth audience checks.
    env['APP_NAME'] = manifest.id
    
    # Next.js basePath for apps deployed at non-root paths
    if manifest.defaultPath and manifest.defaultPath != '/':
        env['NEXT_PUBLIC_BASE_PATH'] = manifest.defaultPath.rstrip('/')

    # Explicit SSO audiences accepted by apps:
    # - manifest.id (canonical audience from busibox.json)
    # - defaultPath segment (legacy/path-based audiences from older app records)
    audience_values = [manifest.id]
    if manifest.defaultPath:
        path_audience = manifest.defaultPath.strip('/').lower()
        if path_audience and path_audience not in audience_values:
            audience_values.append(path_audience)
    env['SSO_AUDIENCE'] = ",".join(audience_values)
    
    # Database URL (if provisioned)
    if database_url:
        env['DATABASE_URL'] = database_url
    
    # Get service endpoints
    endpoints = get_service_endpoints(deploy_config.environment)
    
    # Add required env vars from manifest
    for var in manifest.requiredEnvVars:
        # Check if it's a known service endpoint
        if var in endpoints:
            env[var] = endpoints[var]
        elif var in deploy_config.secrets:
            env[var] = deploy_config.secrets[var]
        else:
            logger.warning(f"Required env var {var} not provided")
    
    # Add optional env vars if available
    for var in manifest.optionalEnvVars:
        if var in endpoints:
            env[var] = endpoints[var]
        elif var in deploy_config.secrets:
            env[var] = deploy_config.secrets[var]
    
    # Always include common Busibox service endpoints for apps that use them
    common_vars = [
        'AUTHZ_BASE_URL',
        'LITELLM_BASE_URL',
        'AGENT_API_URL',
        'DATA_API_URL',
        'SEARCH_API_URL',
    ]
    
    for var in common_vars:
        if var not in env and var in endpoints:
            env[var] = endpoints[var]
    
    # Add any additional secrets from deployment config
    for key, value in deploy_config.secrets.items():
        if key not in env:
            env[key] = value
    
    logger.info(f"Generated {len(env)} environment variables for {manifest.name}")
    return env


def generate_env_file_content(env_vars: Dict[str, str]) -> str:
    """
    Generate .env file content from environment variables.
    
    Args:
        env_vars: Dictionary of environment variables
    
    Returns:
        String content for .env file
    """
    lines = [
        "# Auto-generated by Busibox Deploy Service",
        "# Do not edit manually - changes will be overwritten on next deploy",
        ""
    ]
    
    for key, value in sorted(env_vars.items()):
        # Escape values that contain special characters
        if any(c in value for c in [' ', '"', "'", '$', '\n']):
            # Use double quotes and escape
            value = value.replace('\\', '\\\\').replace('"', '\\"')
            lines.append(f'{key}="{value}"')
        else:
            lines.append(f'{key}={value}')
    
    return '\n'.join(lines) + '\n'
