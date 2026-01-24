"""
Deployment Service Configuration

Environment variables and settings.
"""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Service configuration from environment"""
    
    # Service
    port: int = int(os.getenv('DEPLOY_PORT', '8011'))
    debug: bool = os.getenv('DEBUG', 'false').lower() == 'true'
    
    # Authz service for token validation
    authz_url: str = os.getenv('AUTHZ_URL', 'http://localhost:8010')
    
    # Ansible
    ansible_dir: str = os.getenv('ANSIBLE_DIR', '/root/busibox/provision/ansible')
    
    # PostgreSQL (for database provisioning)
    postgres_host: str = os.getenv('POSTGRES_HOST', '10.96.200.202')
    postgres_port: int = int(os.getenv('POSTGRES_PORT', '5432'))
    postgres_admin_user: str = os.getenv('POSTGRES_ADMIN_USER', 'postgres')
    postgres_admin_password: str = os.getenv('POSTGRES_ADMIN_PASSWORD', '')
    
    # Container IPs
    apps_container_ip: str = os.getenv('APPS_CONTAINER_IP', '10.96.200.201')
    apps_container_ip_staging: str = os.getenv('APPS_CONTAINER_IP_STAGING', '10.96.201.201')
    
    # SSH
    ssh_key_path: str = os.getenv('SSH_KEY_PATH', '/root/.ssh/id_rsa')
    
    # Nginx (on nginx container)
    nginx_host: str = os.getenv('NGINX_HOST', '10.96.200.200')
    nginx_config_dir: str = os.getenv('NGINX_CONFIG_DIR', '/etc/nginx/sites-available/apps')
    nginx_enabled_dir: str = os.getenv('NGINX_ENABLED_DIR', '/etc/nginx/sites-enabled')
    
    # Rate limiting (1 minute for testing, increase for production)
    rate_limit_per_app_minutes: int = int(os.getenv('RATE_LIMIT_MINUTES', '1'))


config = Config()
