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

import base64
import json
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
    """Get connection info for the config database."""
    return config.postgres_host, config.postgres_port


def get_config_db_name() -> str:
    """Get the config store database name."""
    return config.config_database


async def query_config(sql: str, database: str | None = None):
    """Execute a query against the config database."""
    if database is None:
        database = get_config_db_name()
    stdout, stderr, code = await execute_sql(sql, database)
    if code != 0:
        logger.error(f"Config query failed: {stderr}")
        raise HTTPException(status_code=500, detail=f"Database error: {stderr}")
    return stdout.strip()


async def ensure_config_database():
    """
    Ensure the config database and config table exist.
    
    Called at startup. On Docker, the 'busibox' database is created via
    POSTGRES_DB in docker-compose.yml. On Proxmox, we need to create it
    ourselves since the Ansible pg role only creates per-service databases.
    """
    db_name = get_config_db_name()
    logger.info(f"Ensuring config database '{db_name}' exists...")

    # Check if the database exists (query the 'postgres' system database)
    check_sql = f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"
    stdout, stderr, code = await execute_sql(check_sql, 'postgres')

    if stdout.strip() != '1':
        # Database doesn't exist — create it
        logger.info(f"Config database '{db_name}' not found, creating...")
        stdout, stderr, code = await execute_sql(f"CREATE DATABASE {db_name}", 'postgres')
        if code != 0:
            logger.error(f"Failed to create config database: {stderr}")
            return False
        logger.info(f"Config database '{db_name}' created successfully")

    # Ensure the config table exists
    create_table_sql = """
        CREATE TABLE IF NOT EXISTS config (
            key       TEXT PRIMARY KEY,
            value     TEXT NOT NULL DEFAULT '',
            encrypted BOOLEAN NOT NULL DEFAULT FALSE,
            category  TEXT,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """
    try:
        await query_config(create_table_sql)
        logger.info(f"Config table ensured in database '{db_name}'")
        return True
    except Exception as e:
        logger.error(f"Failed to ensure config table: {e}")
        return False


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


# -------------------------------------------------------------------------
# Config keys → Docker Compose env-var names (BRIDGE_* prefix used by compose)
# The config table stores e.g. SMTP_HOST; compose reads BRIDGE_SMTP_HOST.
# -------------------------------------------------------------------------
_CONFIG_TO_COMPOSE_ENV: dict[str, str] = {
    "SMTP_HOST":     "BRIDGE_SMTP_HOST",
    "SMTP_PORT":     "BRIDGE_SMTP_PORT",
    "SMTP_USER":     "BRIDGE_SMTP_USER",
    "SMTP_PASSWORD": "BRIDGE_SMTP_PASSWORD",
    "SMTP_SECURE":   "BRIDGE_SMTP_SECURE",
    "EMAIL_FROM":    "BRIDGE_EMAIL_FROM",
    "RESEND_API_KEY": "BRIDGE_RESEND_API_KEY",
}

# Config keys → bridge .env variable names (Proxmox: no BRIDGE_ prefix)
_CONFIG_TO_BRIDGE_ENV: dict[str, str] = {
    "SMTP_HOST":     "SMTP_HOST",
    "SMTP_PORT":     "SMTP_PORT",
    "SMTP_USER":     "SMTP_USER",
    "SMTP_PASSWORD": "SMTP_PASSWORD",
    "SMTP_SECURE":   "SMTP_SECURE",
    "EMAIL_FROM":    "EMAIL_FROM",
    "RESEND_API_KEY": "RESEND_API_KEY",
}


async def _read_bridge_raw_config_from_db() -> dict[str, str]:
    """Read all smtp-category config rows and return as {config_key: value}."""
    sql = "SELECT key, value FROM config WHERE category = 'smtp'"
    result = await query_config(sql)
    raw: dict[str, str] = {}
    if result:
        for line in result.split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split('|', 1)
            if len(parts) == 2:
                raw[parts[0].strip()] = parts[1].strip()
    return raw


async def _read_bridge_config_from_db() -> dict[str, str]:
    """Read all smtp-category config rows and return as {COMPOSE_ENV: value}."""
    raw = await _read_bridge_raw_config_from_db()
    _COMPOSE_BOOLEAN_KEYS = {"BRIDGE_SMTP_SECURE", "BRIDGE_EMAIL_ENABLED"}
    env: dict[str, str] = {}
    for cfg_key, cfg_val in raw.items():
        compose_key = _CONFIG_TO_COMPOSE_ENV.get(cfg_key)
        if compose_key:
            # Normalise booleans: empty string -> "false"
            if compose_key in _COMPOSE_BOOLEAN_KEYS and cfg_val.strip() == "":
                cfg_val = "false"
            env[compose_key] = cfg_val
    # Derive EMAIL_ENABLED from whether any provider is configured
    has_smtp = bool(env.get("BRIDGE_SMTP_HOST"))
    has_resend = bool(env.get("BRIDGE_RESEND_API_KEY"))
    env["BRIDGE_EMAIL_ENABLED"] = "true" if (has_smtp or has_resend) else "false"
    return env


async def _apply_bridge_config_docker() -> dict:
    """
    Docker path: read config from DB, export as env vars, and recreate the
    bridge-api container via ``docker compose up -d bridge-api`` so it picks
    up the new environment.
    """
    import asyncio, os

    env_overrides = await _read_bridge_config_from_db()
    logger.info(f"[APPLY] Bridge env overrides: { {k: ('****' if 'PASSWORD' in k or 'KEY' in k else v) for k, v in env_overrides.items()} }")

    # Build the shell environment — start with current env (which already has
    # COMPOSE_PROJECT_NAME, BUSIBOX_HOST_PATH, POSTGRES_PASSWORD, etc.) and
    # layer the smtp overrides on top.
    compose_env = {**os.environ, **env_overrides}

    # Compose files and project name come from the deploy-api container env
    compose_files = os.environ.get("COMPOSE_FILES", "-f docker-compose.yml")
    repo_root = os.environ.get("BUSIBOX_HOST_PATH") or os.environ.get("BUSIBOX_REPO_ROOT", "/busibox")

    cmd = f"docker compose {compose_files} up -d bridge-api"
    logger.info(f"[APPLY] Running: {cmd}  (cwd={repo_root})")

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=repo_root,
        env=compose_env,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

    if proc.returncode != 0:
        err = stderr.decode().strip() if stderr else "unknown error"
        logger.error(f"[APPLY] docker compose up failed: {err}")
        raise HTTPException(status_code=500, detail=f"Failed to restart bridge-api: {err}")

    out = stdout.decode().strip() if stdout else ""
    logger.info(f"[APPLY] bridge-api recreated: {out}")
    return {"success": True, "message": "bridge-api restarted with updated email config.", "output": out}


async def _apply_bridge_config_proxmox() -> dict:
    """
    Proxmox path: read config from DB, SSH to the bridge container,
    patch the email-related env vars in the bridge .env file, and restart
    the bridge systemd service.

    This avoids a full Ansible re-run which is slow and requires vault
    secrets to already contain the new values.
    """
    from .database import execute_ssh_command

    raw = await _read_bridge_raw_config_from_db()
    logger.info(f"[APPLY-PROXMOX] Bridge raw config keys: {list(raw.keys())}")

    # Build the env var updates for the bridge .env file
    # Boolean-type env vars that Pydantic expects as "true"/"false"
    _BOOLEAN_KEYS = {"SMTP_SECURE", "EMAIL_ENABLED"}

    env_updates: dict[str, str] = {}
    for cfg_key, cfg_val in raw.items():
        bridge_key = _CONFIG_TO_BRIDGE_ENV.get(cfg_key)
        if bridge_key:
            # Normalise booleans: empty string -> "false"
            if bridge_key in _BOOLEAN_KEYS and cfg_val.strip() == "":
                cfg_val = "false"
            env_updates[bridge_key] = cfg_val

    # Derive EMAIL_ENABLED
    has_smtp = bool(env_updates.get("SMTP_HOST"))
    has_resend = bool(env_updates.get("RESEND_API_KEY"))
    env_updates["EMAIL_ENABLED"] = "true" if (has_smtp or has_resend) else "false"

    logger.info(f"[APPLY-PROXMOX] Updating bridge .env: { {k: ('****' if 'PASSWORD' in k or 'KEY' in k else v) for k, v in env_updates.items()} }")

    # The bridge .env file is at /srv/bridge/.env on the bridge container
    # SSH to 'bridge' hostname (resolved via /etc/hosts from internal_dns role)
    bridge_host = "bridge"
    env_file = "/srv/bridge/.env"

    # Use a Python script on the remote host to safely patch the .env file.
    # This avoids sed/shell escaping issues with special chars in passwords.
    updates_b64 = base64.b64encode(json.dumps(env_updates).encode()).decode()
    combined = (
        f"python3 -c '"
        f"import json, base64, os; "
        f"updates = json.loads(base64.b64decode(\"{updates_b64}\").decode()); "
        f"env_file = \"{env_file}\"; "
        f"lines = open(env_file).readlines() if os.path.exists(env_file) else []; "
        f"found = set(); "
        f"new_lines = []; "
        f"["
        f"(found.add(l.split(\"=\", 1)[0]), new_lines.append(l.split(\"=\", 1)[0] + \"=\" + updates[l.split(\"=\", 1)[0]] + \"\\n\")) "
        f"if \"=\" in l and l.split(\"=\", 1)[0] in updates "
        f"else new_lines.append(l) "
        f"for l in lines"
        f"]; "
        f"[new_lines.append(k + \"=\" + v + \"\\n\") for k, v in updates.items() if k not in found]; "
        f"open(env_file, \"w\").writelines(new_lines)"
        f"' && systemctl restart bridge"
    )

    stdout, stderr, code = await execute_ssh_command(bridge_host, combined)

    if code != 0:
        err = stderr.strip() if stderr else "unknown error"
        logger.error(f"[APPLY-PROXMOX] Failed to apply bridge config: {err}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to apply bridge config via SSH: {err}"
        )

    logger.info(f"[APPLY-PROXMOX] Bridge config applied and service restarted")
    return {
        "success": True,
        "message": "Bridge email config updated and service restarted.",
        "keys_updated": list(env_updates.keys()),
    }


@router.post("/apply/{service}")
async def apply_config(
    service: str,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Apply configuration changes to a service.

    In **Docker** environments: reads config values from the ``config`` table,
    exports them as compose env vars, and recreates the container via
    ``docker compose up -d``.

    In **Proxmox** environments: SSHes to the service container, patches the
    relevant env vars in the service's ``.env`` file, and restarts the systemd
    service.

    Currently supported services:
    - bridge: Multi-channel communication (email, Signal)

    Requires admin authentication.
    """
    from .core_app_executor import is_docker_environment

    logger.info(f"Applying config for service={service}, user={token_payload.get('user_id')}")

    supported = {"bridge"}
    if service not in supported:
        raise HTTPException(
            status_code=400,
            detail=f"Service '{service}' is not supported. Supported: {', '.join(sorted(supported))}"
        )

    # ----- Docker path -----
    if is_docker_environment():
        return await _apply_bridge_config_docker()

    # ----- Proxmox path: patch .env and restart -----
    return await _apply_bridge_config_proxmox()


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
