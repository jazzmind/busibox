"""
Ansible Execution

Executes Ansible playbooks for app deployment.
In Docker/local environments, skips actual deployment.
"""

import asyncio
import logging
import os
from typing import Dict, Any, List, Tuple
from .models import BusiboxManifest, DeploymentConfig
from .config import config
from .core_app_executor import is_docker_environment

logger = logging.getLogger(__name__)


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
