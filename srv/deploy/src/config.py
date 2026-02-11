"""
Deployment Service Configuration

Environment variables and settings.

DNS Hostnames vs IP Addresses:
------------------------------
On Proxmox, services are accessed via DNS hostnames defined in /etc/hosts
(configured by the internal_dns Ansible role). This allows the deploy-api
to work regardless of the network configuration.

On Docker, services are accessed via Docker's internal DNS (container names).

The DEPLOYMENT_BACKEND environment variable explicitly sets the backend:
- "docker": Running in Docker (local development)
- "proxmox": Running on Proxmox LXC containers  
- Not set: Auto-detect based on POSTGRES_HOST format
"""

import os
import re
from dataclasses import dataclass


def _is_ip_address(value: str) -> bool:
    """Check if a string looks like an IP address."""
    # Match IPv4 pattern (e.g., 10.96.200.201)
    return bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', value))


@dataclass
class Config:
    """Service configuration from environment"""
    
    # Service
    port: int = int(os.getenv('DEPLOY_PORT', '8011'))
    debug: bool = os.getenv('DEBUG', 'false').lower() == 'true'
    
    # Deployment backend: "docker" or "proxmox"
    # If not set, auto-detected from POSTGRES_HOST format
    deployment_backend: str = os.getenv('DEPLOYMENT_BACKEND', '')
    
    # Environment: "staging" or "production"
    busibox_env: str = os.getenv('BUSIBOX_ENV', os.getenv('ENVIRONMENT', 'production'))
    
    # LLM Backend: "vllm", "mlx", or "cloud"
    llm_backend: str = os.getenv('LLM_BACKEND', 'vllm')
    
    # Whether staging uses production vLLM (true by default for staging)
    # When true, staging doesn't deploy its own vLLM, it uses production's
    use_production_vllm: bool = os.getenv('USE_PRODUCTION_VLLM', 'false').lower() == 'true'
    
    # Authz service for token validation
    authz_url: str = os.getenv('AUTHZ_URL', 'http://localhost:8010')
    
    # Busibox paths
    busibox_host_path: str = os.getenv('BUSIBOX_HOST_PATH', '/root/busibox')
    
    # Ansible
    ansible_dir: str = os.getenv('ANSIBLE_DIR', '/root/busibox/provision/ansible')
    
    # PostgreSQL (for database provisioning)
    # Use DNS hostname on Proxmox, 'postgres' container name on Docker
    postgres_host: str = os.getenv('POSTGRES_HOST', 'postgres')
    postgres_port: int = int(os.getenv('POSTGRES_PORT', '5432'))
    postgres_admin_user: str = os.getenv('POSTGRES_ADMIN_USER', 'postgres')
    postgres_admin_password: str = os.getenv('POSTGRES_ADMIN_PASSWORD', '')
    
    # Container hosts (use DNS hostnames, resolved via /etc/hosts on Proxmox)
    # Core apps container (ai-portal, agent-manager)
    core_apps_container_ip: str = os.getenv('CORE_APPS_CONTAINER_IP', 'ai-portal')
    core_apps_container_ip_staging: str = os.getenv('CORE_APPS_CONTAINER_IP_STAGING', 'ai-portal')
    # User apps container (external apps deployed via AI Portal)
    user_apps_container_ip: str = os.getenv('USER_APPS_CONTAINER_IP', 'user-apps')
    user_apps_container_ip_staging: str = os.getenv('USER_APPS_CONTAINER_IP_STAGING', 'user-apps')
    # Legacy alias for backwards compatibility
    apps_container_ip: str = os.getenv('APPS_CONTAINER_IP', 'ai-portal')
    apps_container_ip_staging: str = os.getenv('APPS_CONTAINER_IP_STAGING', 'ai-portal')
    
    # SSH
    ssh_key_path: str = os.getenv('SSH_KEY_PATH', '/root/.ssh/id_rsa')
    
    # Proxmox host (for running make install commands)
    # The Proxmox host has the vault and vault password files
    # deploy-api SSHes to Proxmox to run Ansible playbooks
    proxmox_host: str = os.getenv('PROXMOX_HOST', '')
    
    # Nginx (on nginx container)
    nginx_host: str = os.getenv('NGINX_HOST', 'nginx')
    # For LXC: location snippets go in app-locations/ which is included inside the server block
    # For Docker: location snippets are written directly to the mounted nginx config volume
    nginx_config_dir: str = os.getenv('NGINX_CONFIG_DIR', '/etc/nginx/app-locations')
    nginx_enabled_dir: str = os.getenv('NGINX_ENABLED_DIR', '/etc/nginx/sites-enabled')
    
    # Rate limiting (in seconds for flexibility, default 10 seconds for testing)
    rate_limit_seconds: int = int(os.getenv('RATE_LIMIT_SECONDS', '10'))
    
    def is_docker_backend(self) -> bool:
        """Check if running on Docker backend.
        
        Detection order:
        1. Explicit DEPLOYMENT_BACKEND environment variable
        2. Fallback: Check if POSTGRES_HOST is a container name (not an IP)
        
        Returns:
            True if Docker backend, False if Proxmox/LXC
        """
        # Explicit setting takes precedence
        if self.deployment_backend:
            return self.deployment_backend.lower() == 'docker'
        
        # Auto-detect: Docker uses container names like 'postgres'
        # Proxmox uses either IP addresses or DNS hostnames
        # If POSTGRES_HOST looks like an IP or is a known DNS hostname, it's Proxmox
        if _is_ip_address(self.postgres_host):
            return False  # IP address = Proxmox
        
        # DNS hostnames from internal_dns role indicate Proxmox
        proxmox_hostnames = {'postgres', 'pg', 'pg-lxc'}
        if self.postgres_host.lower() in proxmox_hostnames:
            # This is ambiguous - 'postgres' could be Docker container name or Proxmox DNS
            # Check for other Proxmox indicators
            # If SSH key path is set to the default Proxmox path, assume Proxmox
            if self.ssh_key_path == '/root/.ssh/id_rsa':
                # Check if running in a container (Docker sets /.dockerenv)
                if os.path.exists('/.dockerenv'):
                    return True  # Docker container
                return False  # Proxmox (not in Docker)
            return True  # Non-default SSH path suggests Docker development
        
        # Default to Docker for container names not matching Proxmox DNS
        return True


config = Config()
