"""
Ansible Execution

Executes Ansible playbooks for app and infrastructure deployment.
In Docker/local environments, skips actual deployment (uses docker compose instead).
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional, AsyncGenerator
from .models import BusiboxManifest, DeploymentConfig
from .config import config
from .core_app_executor import is_docker_environment

logger = logging.getLogger(__name__)


# =============================================================================
# Infrastructure Service to Ansible Mapping
# =============================================================================
# Maps service names to their Ansible deployment configuration
# Format: service_name -> (host_limit, tags, description)
#
# host_limit: Ansible -l parameter (inventory host/group to target)
# tags: Ansible --tags parameter (role/task tags to execute)
# description: Human-readable description for logging

INFRASTRUCTURE_ANSIBLE_MAP = {
    # Core Infrastructure (data layer)
    'redis': ('data', ['data_install'], 'Redis (message queue)'),
    'postgres': ('pg', ['core_database'], 'PostgreSQL (database)'),
    'minio': ('files', ['core_storage'], 'MinIO (object storage)'),
    'milvus': ('milvus', ['core_vectorstore'], 'Milvus (vector database)'),
    
    # LLM Services
    'litellm': ('litellm', ['llm_litellm'], 'LiteLLM (LLM gateway)'),
    'vllm': ('vllm', ['llm_vllm'], 'vLLM (GPU inference)'),
    'embedding-api': ('data', ['embedding_api'], 'Embedding API'),
    'embedding': ('data', ['embedding_api'], 'Embedding API'),  # alias
    
    # API Services
    'data-api': ('data', ['apis_data'], 'Data API'),
    'data': ('data', ['apis_data'], 'Data API'),  # alias
    'search-api': ('milvus', ['apis_search'], 'Search API'),
    'search': ('milvus', ['apis_search'], 'Search API'),  # alias
    'agent-api': ('agent', ['apis_agent'], 'Agent API'),
    'agent': ('agent', ['apis_agent'], 'Agent API'),  # alias
    'authz-api': ('authz', ['authz'], 'AuthZ API'),
    'authz': ('authz', ['authz'], 'AuthZ API'),  # alias
    'deploy-api': ('authz', ['deploy_api'], 'Deploy API'),
    'deploy': ('authz', ['deploy_api'], 'Deploy API'),  # alias
    'docs-api': ('agent', ['docs_api'], 'Docs API'),
    'docs': ('agent', ['docs_api'], 'Docs API'),  # alias
    
    # Nginx
    'nginx': ('proxy', ['core_nginx'], 'Nginx (reverse proxy)'),
    
    # Apps (deploy via Deploy API, but can also use Ansible)
    'apps': ('apps', ['apps'], 'Frontend applications'),
}


# =============================================================================
# Service Installation Order
# =============================================================================
# Services should be installed in this order for proper dependency resolution.
# Each group can be installed in parallel, but groups must be sequential.

INSTALLATION_ORDER = [
    # Group 1: Core infrastructure (no dependencies)
    ['postgres', 'nginx'],
    
    # Group 2: Data layer (needs postgres)
    ['redis', 'minio'],
    
    # Group 3: Vector database (needs minio for storage)
    ['milvus'],
    
    # Group 4: LLM services (optional GPU support)
    ['vllm', 'litellm'],
    
    # Group 5: APIs (need infrastructure)
    ['embedding-api'],  # Embedding first (data-api depends on it)
    ['data-api'],       # Data API (depends on redis, minio, postgres, embedding)
    ['search-api'],     # Search API (depends on milvus)
    ['authz-api', 'deploy-api'],  # Auth services
    ['agent-api', 'docs-api'],    # Agent services
    
    # Group 6: Apps (need all APIs)
    ['apps'],
]


def get_installation_order() -> List[List[str]]:
    """
    Get the recommended service installation order.
    
    Returns a list of groups. Services within a group can be installed in parallel,
    but groups should be installed sequentially.
    """
    return INSTALLATION_ORDER


def get_service_dependencies(service: str) -> List[str]:
    """
    Get the services that should be installed before the given service.
    
    Returns a flat list of service names in installation order.
    """
    dependencies = []
    for group in INSTALLATION_ORDER:
        if service in group:
            return dependencies
        dependencies.extend(group)
    return dependencies


def get_vault_password_file(environment: str) -> Optional[str]:
    """
    Get the vault password file path for the given environment.
    
    Returns None if no vault password file exists (will run without vault decryption).
    """
    # Check environment-specific password files first
    env_vault_files = {
        'production': '~/.busibox-vault-pass-prod',
        'staging': '~/.busibox-vault-pass-staging',
        'demo': '~/.busibox-vault-pass-demo',
        'development': '~/.busibox-vault-pass-dev',
    }
    
    vault_file = os.path.expanduser(env_vault_files.get(environment, '~/.busibox-vault-pass-dev'))
    if os.path.exists(vault_file):
        return vault_file
    
    # Fallback to legacy vault pass
    legacy_vault = os.path.expanduser('~/.vault_pass')
    if os.path.exists(legacy_vault):
        return legacy_vault
    
    logger.warning(f"No vault password file found for environment '{environment}'")
    return None


class AnsibleExecutor:
    def __init__(self):
        self.ansible_dir = config.ansible_dir
        self.inventory_production = f"{self.ansible_dir}/inventory/production"
        self.inventory_staging = f"{self.ansible_dir}/inventory/staging"
    
    async def execute_playbook(
        self,
        playbook: str,
        inventory: str,
        extra_vars: Dict[str, Any],
        tags: List[str] = None
    ) -> Tuple[str, str, int]:
        """Execute Ansible playbook"""
        
        cmd = [
            'ansible-playbook',
            '-i', inventory,
            f'{self.ansible_dir}/{playbook}'
        ]
        
        if tags:
            cmd.extend(['--tags', ','.join(tags)])
        
        if extra_vars:
            vars_str = ' '.join([f'{k}={v}' for k, v in extra_vars.items()])
            cmd.extend(['--extra-vars', vars_str])
        
        logger.info(f"Executing: {' '.join(cmd)}")
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.ansible_dir
        )
        
        stdout, stderr = await proc.communicate()
        return stdout.decode(), stderr.decode(), proc.returncode
    
    async def install_infrastructure_service_stream(
        self,
        service: str,
        environment: str = 'staging'
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Install an infrastructure service via Ansible with streaming output.
        
        Yields SSE-compatible event dictionaries with keys:
        - type: 'info', 'log', 'error', 'success', 'warning'
        - message: Human-readable message
        - done: True if this is the final message
        
        Args:
            service: Service name (e.g., 'redis', 'postgres', 'minio')
            environment: 'staging' or 'production'
        """
        # Check if service is supported
        if service not in INFRASTRUCTURE_ANSIBLE_MAP:
            yield {
                'type': 'error',
                'message': f'Service {service} is not supported for Ansible installation. '
                           f'Supported services: {", ".join(sorted(INFRASTRUCTURE_ANSIBLE_MAP.keys()))}',
                'done': True
            }
            return
        
        host_limit, tags, description = INFRASTRUCTURE_ANSIBLE_MAP[service]
        
        yield {
            'type': 'info',
            'message': f'Installing {description} via Ansible...'
        }
        
        # Determine inventory
        if environment == 'staging':
            inventory = self.inventory_staging
        else:
            inventory = self.inventory_production
        
        yield {
            'type': 'info',
            'message': f'Using {environment} inventory: {inventory}'
        }
        
        # Get vault password file
        vault_pass_file = get_vault_password_file(environment)
        if not vault_pass_file:
            yield {
                'type': 'error',
                'message': f'No vault password file found for environment "{environment}". '
                           f'Expected: ~/.busibox-vault-pass-{environment[:4]}',
                'done': True
            }
            return
        
        yield {
            'type': 'info',
            'message': f'Using vault password from: {vault_pass_file}'
        }
        
        # Build command
        cmd = [
            'ansible-playbook',
            '-i', inventory,
            '-l', host_limit,
            f'{self.ansible_dir}/site.yml',
            '--tags', ','.join(tags),
            '--vault-password-file', vault_pass_file,
        ]
        
        logger.info(f"[ANSIBLE] Executing: {' '.join(cmd)}")
        yield {
            'type': 'info',
            'message': f'Running: ansible-playbook -l {host_limit} --tags {",".join(tags)}'
        }
        
        # Execute with streaming output
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
                cwd=self.ansible_dir
            )
            
            # Stream output line by line
            line_count = 0
            async for line_bytes in proc.stdout:
                line = line_bytes.decode('utf-8', errors='replace').rstrip()
                if line:
                    line_count += 1
                    # Determine message type based on content
                    msg_type = 'log'
                    if 'FAILED' in line or 'fatal:' in line or 'ERROR' in line:
                        msg_type = 'error'
                    elif 'changed:' in line or 'ok:' in line:
                        msg_type = 'log'
                    elif 'TASK' in line or 'PLAY' in line:
                        msg_type = 'info'
                    elif 'RECAP' in line:
                        msg_type = 'info'
                    elif 'skipping:' in line:
                        msg_type = 'log'  # Keep as log, don't show warnings for skips
                    
                    yield {
                        'type': msg_type,
                        'message': line
                    }
            
            # Wait for process to complete
            await proc.wait()
            
            if proc.returncode == 0:
                yield {
                    'type': 'success',
                    'message': f'{description} installed successfully!',
                    'done': True
                }
            else:
                yield {
                    'type': 'error',
                    'message': f'Ansible playbook failed with exit code {proc.returncode}',
                    'done': True
                }
                
        except Exception as e:
            logger.error(f"[ANSIBLE] Error executing playbook: {e}")
            yield {
                'type': 'error',
                'message': f'Error executing Ansible playbook: {str(e)}',
                'done': True
            }
    
    def get_supported_services(self) -> Dict[str, str]:
        """
        Get list of services that can be installed via Ansible.
        
        Returns dict of service_name -> description
        """
        return {
            service: info[2] 
            for service, info in INFRASTRUCTURE_ANSIBLE_MAP.items()
        }
    
    async def deploy_app(
        self,
        manifest: BusiboxManifest,
        deploy_config: DeploymentConfig,
        database_url: str = None
    ) -> Tuple[bool, List[str]]:
        """Deploy app via Ansible (production) or simulate (Docker local)"""
        
        logs = []
        
        logs.append(f"Deploying {manifest.name} to {deploy_config.environment}")
        logs.append(f"Repository: {deploy_config.githubRepoOwner}/{deploy_config.githubRepoName}")
        logs.append(f"Branch: {deploy_config.githubBranch}")
        
        # In Docker/local environment, skip actual Ansible deployment
        # Apps in Docker share the apps container with AI Portal
        if is_docker_environment():
            logs.append("📦 Docker/local environment detected")
            logs.append("⏭️  Skipping Ansible deployment (production only)")
            logs.append("")
            logs.append("✅ Database provisioned successfully")
            if database_url:
                logs.append(f"   DATABASE_URL: {database_url}")
            logs.append("")
            logs.append(f"📍 App will be available at: {manifest.defaultPath}")
            logs.append("ℹ️  For production: deploy via Ansible from Proxmox host")
            return True, logs
        
        # Production: Use Ansible
        # Determine inventory
        inventory = (
            self.inventory_staging 
            if deploy_config.environment == 'staging' 
            else self.inventory_production
        )
        
        # Prepare extra vars
        extra_vars = {
            'deploy_app': manifest.id,
            'deploy_from_branch': 'true',
            'deploy_branch': deploy_config.githubBranch,
        }
        
        # Add GitHub token if provided
        if deploy_config.githubToken:
            extra_vars['github_token'] = deploy_config.githubToken
        
        # Execute deployment playbook
        stdout, stderr, code = await self.execute_playbook(
            playbook='site.yml',
            inventory=inventory,
            extra_vars=extra_vars,
            tags=['app_deployer']
        )
        
        # Parse output
        for line in stdout.split('\n'):
            if line.strip():
                logs.append(line)
        
        if code != 0:
            logs.append(f"ERROR: Deployment failed with exit code {code}")
            for line in stderr.split('\n'):
                if line.strip():
                    logs.append(f"STDERR: {line}")
            return False, logs
        
        logs.append("Deployment completed successfully")
        return True, logs


async def get_container_ip(app_name: str, environment: str) -> str:
    """Get container IP for app"""
    # For now, return apps container IP
    # TODO: Make this configurable per app
    if environment == 'staging':
        return config.apps_container_ip_staging
    return config.apps_container_ip
