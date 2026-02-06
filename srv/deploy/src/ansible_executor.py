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
    # Internal DNS (updates /etc/hosts on all containers)
    'internal-dns': ('all', ['internal_dns'], 'Internal DNS (/etc/hosts)'),
    'dns': ('all', ['internal_dns'], 'Internal DNS (/etc/hosts)'),  # alias
    
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
        
        On Proxmox: SSHes to the Proxmox host and runs `make install SERVICE=<service>`
        The Proxmox host has the vault and vault password files needed for Ansible.
        
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
                'message': f'Service {service} is not supported for installation. '
                           f'Supported services: {", ".join(sorted(INFRASTRUCTURE_ANSIBLE_MAP.keys()))}',
                'done': True
            }
            return
        
        _, _, description = INFRASTRUCTURE_ANSIBLE_MAP[service]
        
        yield {
            'type': 'info',
            'message': f'Installing {description}...'
        }
        
        # Check if Proxmox host is configured
        proxmox_host = config.proxmox_host
        if not proxmox_host:
            yield {
                'type': 'error',
                'message': 'PROXMOX_HOST not configured. '
                           'Set PROXMOX_HOST in inventory group_vars to the Proxmox management IP, '
                           'then redeploy internal_dns and deploy-api.',
                'done': True
            }
            return
        
        yield {
            'type': 'info',
            'message': f'Connecting to Proxmox host ({proxmox_host})...'
        }
        
        # Map service names to make targets
        # Some services use different names in the Makefile
        service_to_make_target = {
            'internal-dns': 'internal-dns',  # Updates /etc/hosts on all containers
            'dns': 'internal-dns',  # alias
            'redis': 'redis',  # Redis has its own target with data_install tag
            'postgres': 'pg',
            'minio': 'files',
            'milvus': 'milvus',
            'litellm': 'litellm',
            'vllm': 'vllm',
            'embedding-api': 'embedding-api',
            'embedding': 'embedding-api',
            'data-api': 'data-api',
            'data': 'data',
            'search-api': 'search-api',
            'search': 'search-api',
            'agent-api': 'agent',
            'agent': 'agent',
            'authz-api': 'authz',
            'authz': 'authz',
            'deploy-api': 'deploy-api',
            'deploy': 'deploy-api',
            'docs-api': 'docs',
            'docs': 'docs',
            'nginx': 'nginx',
            'apps': 'apps',
        }
        
        make_target = service_to_make_target.get(service, service)
        
        # Build the make command
        # Use the environment to set the correct inventory
        if environment == 'staging':
            make_cmd = f'cd /root/busibox/provision/ansible && make {make_target} INV=inventory/staging'
        else:
            make_cmd = f'cd /root/busibox/provision/ansible && make {make_target} INV=inventory/production'
        
        yield {
            'type': 'info',
            'message': f'Running: make {make_target} (environment: {environment})'
        }
        
        # Build SSH command
        ssh_key = config.ssh_key_path
        ssh_cmd = [
            'ssh',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=10',
        ]
        
        if ssh_key and os.path.exists(ssh_key):
            ssh_cmd.extend(['-i', ssh_key])
        
        ssh_cmd.extend([f'root@{proxmox_host}', make_cmd])
        
        logger.info(f"[INSTALL] Executing: {' '.join(ssh_cmd[:8])}... {make_cmd}")
        
        # Execute with streaming output
        try:
            proc = await asyncio.create_subprocess_exec(
                *ssh_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
            )
            
            # Stream output line by line
            async for line_bytes in proc.stdout:
                line = line_bytes.decode('utf-8', errors='replace').rstrip()
                if line:
                    # Determine message type based on content
                    msg_type = 'log'
                    if 'FAILED' in line or 'fatal:' in line or 'ERROR' in line or 'Error' in line:
                        msg_type = 'error'
                    elif 'changed:' in line or 'ok:' in line:
                        msg_type = 'log'
                    elif 'TASK' in line or 'PLAY' in line:
                        msg_type = 'info'
                    elif 'RECAP' in line:
                        msg_type = 'info'
                    elif line.startswith('make[') or line.startswith('make:'):
                        msg_type = 'info'
                    elif 'skipping:' in line:
                        msg_type = 'log'
                    
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
                    'message': f'Installation failed with exit code {proc.returncode}',
                    'done': True
                }
                
        except Exception as e:
            logger.error(f"[INSTALL] Error executing command on Proxmox host: {e}")
            yield {
                'type': 'error',
                'message': f'Error connecting to Proxmox host: {str(e)}',
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
