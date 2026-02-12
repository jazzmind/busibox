"""
Config API Routes

Provides runtime configuration management via database storage.
This replaces runtime secrets in Ansible vault with database-stored configuration
that can be changed after installation.

Categories:
- smtp: SMTP/email server configuration
- api_keys: External API keys (OpenAI, HuggingFace, etc.)
- oauth: OAuth provider credentials (Microsoft, GitHub)
- email: Email settings (allowed domains, admin email)
- feature_flags: Feature toggles

What goes in the database (runtime, changeable):
- SMTP credentials
- External API keys
- OAuth provider credentials
- Email settings
- Feature flags

What stays in Ansible vault (infrastructure):
- Network configuration
- Database passwords
- MinIO credentials
- JWT/auth secrets (used at startup)
- SSH keys
- SSL certificates
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from .models import (
    ConfigValue,
    ConfigSetRequest,
    ConfigBulkSetRequest,
    ConfigListResponse,
    ConfigCategory,
)
from .auth import verify_admin_token
from .config import config
from .database import execute_sql

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/config", tags=["config"])


async def get_config_db_connection():
    """Get connection info for the config database (busibox)."""
    # The config table is in the main busibox database
    return config.postgres_host, config.postgres_port


async def query_config(sql: str, database: str = 'busibox'):
    """Execute a query against the config database."""
    stdout, stderr, code = await execute_sql(sql, database)
    if code != 0:
        logger.error(f"Config query failed: {stderr}")
        raise HTTPException(status_code=500, detail=f"Database error: {stderr}")
    return stdout.strip()


@router.get("", response_model=ConfigListResponse)
async def list_configs(
    category: Optional[str] = Query(None, description="Filter by category"),
    token_payload: dict = Depends(verify_admin_token)
):
    """
    List all configuration keys.
    
    Requires admin authentication.
    Optionally filter by category.
    """
    logger.info(f"Listing configs, category={category}, user={token_payload.get('user_id')}")
    
    if category:
        sql = f"""
            SELECT key, value, encrypted, category, description, 
                   created_at::text, updated_at::text
            FROM config 
            WHERE category = '{category}'
            ORDER BY key
        """
    else:
        sql = """
            SELECT key, value, encrypted, category, description,
                   created_at::text, updated_at::text
            FROM config 
            ORDER BY category NULLS LAST, key
        """
    
    result = await query_config(sql)
    
    configs = []
    if result:
        for line in result.split('\n'):
            if line.strip():
                parts = line.split('|')
                if len(parts) >= 7:
                    # Mask encrypted values
                    value = parts[1]
                    is_encrypted = parts[2].lower() == 't' or parts[2].lower() == 'true'
                    if is_encrypted:
                        value = "********"  # Mask encrypted values
                    
                    configs.append(ConfigValue(
                        key=parts[0],
                        value=value,
                        encrypted=is_encrypted,
                        category=parts[3] if parts[3] else None,
                        description=parts[4] if parts[4] else None,
                        # Timestamps are returned as strings
                    ))
    
    return ConfigListResponse(
        configs=configs,
        total=len(configs)
    )


@router.get("/categories")
async def list_categories(
    token_payload: dict = Depends(verify_admin_token)
):
    """
    List all configuration categories with their key counts.
    
    Requires admin authentication.
    """
    sql = """
        SELECT category, array_agg(key) as keys, count(*) as count
        FROM config 
        WHERE category IS NOT NULL
        GROUP BY category
        ORDER BY category
    """
    
    result = await query_config(sql)
    
    categories = []
    if result:
        for line in result.split('\n'):
            if line.strip():
                parts = line.split('|')
                if len(parts) >= 3:
                    # Parse array format {key1,key2,key3}
                    keys_str = parts[1].strip('{}')
                    keys = keys_str.split(',') if keys_str else []
                    categories.append(ConfigCategory(
                        category=parts[0],
                        keys=keys,
                        count=int(parts[2])
                    ))
    
    return {"categories": categories}


@router.get("/{key}", response_model=ConfigValue)
async def get_config(
    key: str,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Get a configuration value by key.
    
    Requires admin authentication.
    Returns masked value if encrypted.
    """
    # Sanitize key to prevent SQL injection
    if not key.replace('_', '').replace('-', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid key format")
    
    sql = f"""
        SELECT key, value, encrypted, category, description,
               created_at::text, updated_at::text
        FROM config 
        WHERE key = '{key}'
    """
    
    result = await query_config(sql)
    
    if not result:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    
    parts = result.split('|')
    if len(parts) < 7:
        raise HTTPException(status_code=500, detail="Invalid database response")
    
    # Mask encrypted values
    value = parts[1]
    is_encrypted = parts[2].lower() == 't' or parts[2].lower() == 'true'
    if is_encrypted:
        value = "********"
    
    return ConfigValue(
        key=parts[0],
        value=value,
        encrypted=is_encrypted,
        category=parts[3] if parts[3] else None,
        description=parts[4] if parts[4] else None,
    )


@router.get("/{key}/raw")
async def get_config_raw(
    key: str,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Get the raw (unmasked) configuration value.
    
    Requires admin authentication.
    Use this when you need the actual value for service configuration.
    
    CAUTION: This returns the actual secret value. Handle with care.
    """
    # Sanitize key to prevent SQL injection
    if not key.replace('_', '').replace('-', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid key format")
    
    sql = f"""
        SELECT value, encrypted
        FROM config 
        WHERE key = '{key}'
    """
    
    result = await query_config(sql)
    
    if not result:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    
    parts = result.split('|')
    if len(parts) < 2:
        raise HTTPException(status_code=500, detail="Invalid database response")
    
    value = parts[0]
    is_encrypted = parts[1].lower() == 't' or parts[1].lower() == 'true'
    
    # TODO: If encrypted, decrypt the value here
    # For now, we store values in plaintext (encryption TBD)
    
    return {"key": key, "value": value, "encrypted": is_encrypted}


@router.put("/{key}", response_model=ConfigValue)
async def set_config(
    key: str,
    request: ConfigSetRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Set a configuration value.
    
    Requires admin authentication.
    Creates the key if it doesn't exist, updates if it does.
    """
    logger.info(f"Setting config {key}, user={token_payload.get('user_id')}")
    
    # Sanitize key to prevent SQL injection
    if not key.replace('_', '').replace('-', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid key format")
    
    # Escape single quotes in value
    value = request.value.replace("'", "''")
    category = request.category.replace("'", "''") if request.category else None
    description = request.description.replace("'", "''") if request.description else None
    
    # Use UPSERT to create or update
    sql = f"""
        INSERT INTO config (key, value, encrypted, category, description)
        VALUES ('{key}', '{value}', {str(request.encrypted).lower()}, 
                {f"'{category}'" if category else 'NULL'}, 
                {f"'{description}'" if description else 'NULL'})
        ON CONFLICT (key) DO UPDATE SET
            value = EXCLUDED.value,
            encrypted = EXCLUDED.encrypted,
            category = COALESCE(EXCLUDED.category, config.category),
            description = COALESCE(EXCLUDED.description, config.description),
            updated_at = CURRENT_TIMESTAMP
        RETURNING key, value, encrypted, category, description
    """
    
    result = await query_config(sql)
    
    if not result:
        raise HTTPException(status_code=500, detail="Failed to set config")
    
    parts = result.split('|')
    
    # Mask value in response if encrypted
    response_value = "********" if request.encrypted else request.value
    
    return ConfigValue(
        key=key,
        value=response_value,
        encrypted=request.encrypted,
        category=request.category,
        description=request.description,
    )


@router.delete("/{key}")
async def delete_config(
    key: str,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Delete a configuration value.
    
    Requires admin authentication.
    """
    logger.info(f"Deleting config {key}, user={token_payload.get('user_id')}")
    
    # Sanitize key to prevent SQL injection
    if not key.replace('_', '').replace('-', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid key format")
    
    # Check if exists
    check_sql = f"SELECT 1 FROM config WHERE key = '{key}'"
    exists = await query_config(check_sql)
    
    if not exists:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    
    sql = f"DELETE FROM config WHERE key = '{key}'"
    await query_config(sql)
    
    return {"deleted": True, "key": key}


@router.post("/bulk", response_model=ConfigListResponse)
async def bulk_set_configs(
    request: ConfigBulkSetRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Set multiple configuration values at once.
    
    Requires admin authentication.
    """
    logger.info(f"Bulk setting {len(request.configs)} configs, user={token_payload.get('user_id')}")
    
    results = []
    
    for key, config_req in request.configs.items():
        # Sanitize key
        if not key.replace('_', '').replace('-', '').isalnum():
            logger.warning(f"Skipping invalid key: {key}")
            continue
        
        # Escape single quotes
        value = config_req.value.replace("'", "''")
        category = config_req.category.replace("'", "''") if config_req.category else None
        description = config_req.description.replace("'", "''") if config_req.description else None
        
        sql = f"""
            INSERT INTO config (key, value, encrypted, category, description)
            VALUES ('{key}', '{value}', {str(config_req.encrypted).lower()}, 
                    {f"'{category}'" if category else 'NULL'}, 
                    {f"'{description}'" if description else 'NULL'})
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                encrypted = EXCLUDED.encrypted,
                category = COALESCE(EXCLUDED.category, config.category),
                description = COALESCE(EXCLUDED.description, config.description),
                updated_at = CURRENT_TIMESTAMP
        """
        
        try:
            await query_config(sql)
            
            results.append(ConfigValue(
                key=key,
                value="********" if config_req.encrypted else config_req.value,
                encrypted=config_req.encrypted,
                category=config_req.category,
                description=config_req.description,
            ))
        except Exception as e:
            logger.error(f"Failed to set config {key}: {e}")
    
    return ConfigListResponse(
        configs=results,
        total=len(results)
    )


@router.post("/apply/{service}")
async def apply_config(
    service: str,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Apply configuration changes to a service by triggering an Ansible redeploy.
    
    This endpoint:
    1. Triggers `make <service>` on the Proxmox host via SSH
    2. Which re-renders the service's .env.j2 from vault/defaults
    3. And restarts the service
    
    Currently supported services:
    - bridge: Multi-channel communication (email, Signal)
    
    Requires admin authentication.
    """
    from .ansible_executor import INFRASTRUCTURE_ANSIBLE_MAP, AnsibleExecutor
    
    logger.info(f"Applying config for service={service}, user={token_payload.get('user_id')}")
    
    if service not in INFRASTRUCTURE_ANSIBLE_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Service '{service}' is not supported. "
                   f"Supported: {', '.join(sorted(INFRASTRUCTURE_ANSIBLE_MAP.keys()))}"
        )
    
    _, _, description = INFRASTRUCTURE_ANSIBLE_MAP[service]
    
    executor = AnsibleExecutor()
    
    # Determine environment from config or default to production
    environment = getattr(config, 'environment', 'production')
    if environment not in ('staging', 'production'):
        environment = 'production'
    
    # Collect output from the streaming install
    logs = []
    success = False
    
    try:
        async for event in executor.install_infrastructure_service_stream(service, environment):
            msg_type = event.get('type', 'log')
            message = event.get('message', '')
            done = event.get('done', False)
            
            logs.append(f"[{msg_type}] {message}")
            
            if done:
                success = msg_type == 'success'
    except Exception as exc:
        logger.error(f"Failed to apply config for {service}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to restart {service}: {str(exc)}")
    
    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to restart {description}. Check logs for details."
        )
    
    return {
        "success": True,
        "message": f"{description} configuration applied and service restarted.",
        "service": service,
    }


@router.get("/export/all")
async def export_configs(
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Export all configuration values (for backup/migration).
    
    Requires admin authentication.
    WARNING: This returns all values including encrypted ones.
    """
    logger.info(f"Exporting all configs, user={token_payload.get('user_id')}")
    
    sql = """
        SELECT key, value, encrypted, category, description
        FROM config 
        ORDER BY category NULLS LAST, key
    """
    
    result = await query_config(sql)
    
    configs = {}
    if result:
        for line in result.split('\n'):
            if line.strip():
                parts = line.split('|')
                if len(parts) >= 5:
                    configs[parts[0]] = {
                        "value": parts[1],
                        "encrypted": parts[2].lower() == 't',
                        "category": parts[3] if parts[3] else None,
                        "description": parts[4] if parts[4] else None,
                    }
    
    return {"configs": configs, "total": len(configs)}
