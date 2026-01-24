"""
Database Provisioning

Provisions PostgreSQL databases for apps.
Supports both direct connection (Docker) and SSH (production LXC).
"""

import asyncio
import secrets
import logging
import os
from typing import Tuple, Optional
from .models import BusiboxManifest, DatabaseProvisionResult
from .config import config

logger = logging.getLogger(__name__)


def generate_password(length: int = 32) -> str:
    """Generate a secure random password"""
    return secrets.token_urlsafe(length)[:length]


def is_docker_environment() -> bool:
    """Check if running in Docker (no SSH needed for local postgres)"""
    # In Docker, POSTGRES_HOST is typically 'postgres' (container name) not an IP
    return not config.postgres_host.startswith('10.')


async def execute_psql_direct(sql: str, database: str = 'postgres') -> Tuple[str, str, int]:
    """Execute psql command directly (for Docker environment)"""
    env = os.environ.copy()
    env['PGPASSWORD'] = config.postgres_admin_password
    
    proc = await asyncio.create_subprocess_exec(
        'psql',
        '-h', config.postgres_host,
        '-p', str(config.postgres_port),
        '-U', config.postgres_admin_user,
        '-d', database,
        '-tAc', sql,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env
    )
    
    stdout, stderr = await proc.communicate()
    return stdout.decode(), stderr.decode(), proc.returncode or 0


async def execute_ssh_command(host: str, command: str) -> Tuple[str, str, int]:
    """Execute command on remote host via SSH (for LXC production)"""
    ssh_command = [
        'ssh',
        '-F', '/dev/null',  # Ignore user SSH config (avoids macOS-specific options like UseKeychain)
        '-i', config.ssh_key_path,
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'ConnectTimeout=10',
        f'root@{host}',
        command
    ]
    
    proc = await asyncio.create_subprocess_exec(
        *ssh_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await proc.communicate()
    return stdout.decode(), stderr.decode(), proc.returncode or 0


async def execute_sql(sql: str, database: str = 'postgres') -> Tuple[str, str, int]:
    """Execute SQL command - uses direct connection or SSH based on environment"""
    if is_docker_environment():
        return await execute_psql_direct(sql, database)
    else:
        # SSH to postgres host and run psql there
        command = f"PGPASSWORD='{config.postgres_admin_password}' psql -h localhost -U {config.postgres_admin_user} -d {database} -tAc \"{sql}\""
        return await execute_ssh_command(config.postgres_host, command)


async def database_exists(db_name: str) -> bool:
    """Check if database already exists"""
    if not config.postgres_admin_password:
        raise ValueError("POSTGRES_ADMIN_PASSWORD not set")
    
    sql = f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"
    stdout, stderr, code = await execute_sql(sql)
    return stdout.strip() == '1'


async def create_database(
    db_name: str,
    db_user: str,
    password: str
) -> DatabaseProvisionResult:
    """Create database and user with privileges"""
    if not config.postgres_admin_password:
        return DatabaseProvisionResult(
            success=False,
            error="POSTGRES_ADMIN_PASSWORD not configured"
        )
    
    logger.info(f"Creating database {db_name} on {config.postgres_host}")
    
    # Check if exists
    if await database_exists(db_name):
        return DatabaseProvisionResult(
            success=False,
            error=f"Database {db_name} already exists"
        )
    
    # Commands to execute
    commands = [
        f"CREATE DATABASE {db_name}",
        f"CREATE USER {db_user} WITH PASSWORD '{password}'",
        f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user}",
    ]
    
    # Execute in postgres database
    for sql in commands:
        stdout, stderr, code = await execute_sql(sql)
        
        if code != 0:
            logger.error(f"Database creation failed: {stderr}")
            return DatabaseProvisionResult(
                success=False,
                error=f"Failed to execute: {sql}\n{stderr}"
            )
    
    # Grant schema privileges (PostgreSQL 15+)
    schema_commands = [
        f"GRANT ALL ON SCHEMA public TO {db_user}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {db_user}",
    ]
    
    for sql in schema_commands:
        stdout, stderr, code = await execute_sql(sql, db_name)
        # Non-fatal if these fail
        if code != 0:
            logger.warning(f"Schema privilege grant warning: {stderr}")
    
    # Construct DATABASE_URL
    database_url = f"postgresql://{db_user}:{password}@{config.postgres_host}:{config.postgres_port}/{db_name}"
    
    logger.info(f"Database {db_name} created successfully")
    
    return DatabaseProvisionResult(
        success=True,
        databaseName=db_name,
        databaseUser=db_user,
        databaseUrl=database_url
    )


async def provision_database(manifest: BusiboxManifest) -> DatabaseProvisionResult:
    """Provision database for app if required"""
    if not manifest.database or not manifest.database.required:
        return DatabaseProvisionResult(
            success=True,
            error="Database not required"
        )
    
    db_name = manifest.database.preferredName
    db_user = f"{db_name}_user"
    password = generate_password()
    
    return await create_database(db_name, db_user, password)


async def delete_database(db_name: str) -> bool:
    """Delete database and user (for rollback/cleanup)"""
    if not config.postgres_admin_password:
        logger.error("POSTGRES_ADMIN_PASSWORD not set")
        return False
    
    db_user = f"{db_name}_user"
    
    commands = [
        f"DROP DATABASE IF EXISTS {db_name}",
        f"DROP USER IF EXISTS {db_user}",
    ]
    
    for sql in commands:
        stdout, stderr, code = await execute_sql(sql)
        
        if code != 0:
            logger.error(f"Database deletion failed: {stderr}")
            return False
    
    logger.info(f"Database {db_name} deleted")
    return True
