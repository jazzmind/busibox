"""
Database access layer for deployment management tables.

Uses the existing execute_sql() pattern from database.py (raw psql subprocess).
All queries target the 'data' database where the deployment tables live.
"""

import json
import logging
from datetime import datetime
from typing import Optional, List, Tuple, Any

from .database import execute_sql
from . import authz_crypto

logger = logging.getLogger(__name__)

# All deployment tables are in the 'data' database
DB_NAME = 'data'


# ============================================================================
# Helpers
# ============================================================================

def _escape(value: Any) -> str:
    """Escape a value for safe SQL insertion via psql."""
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return f"'{value.isoformat()}'"
    if isinstance(value, list):
        # PostgreSQL array literal
        items = ','.join(f'"{v}"' for v in value)
        return f"'{{{items}}}'"
    # String – escape single quotes
    s = str(value).replace("'", "''")
    return f"'{s}'"


def _parse_rows(stdout: str, columns: List[str]) -> List[dict]:
    """Parse pipe-separated psql output into list of dicts."""
    rows = []
    if not stdout or not stdout.strip():
        return rows
    for line in stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('|')
        if len(parts) < len(columns):
            continue
        row = {}
        for i, col in enumerate(columns):
            val = parts[i] if i < len(parts) else ''
            row[col] = val if val != '' else None
        rows.append(row)
    return rows


def _parse_bool(val: Optional[str]) -> bool:
    if val is None:
        return False
    return val.lower() in ('t', 'true', '1')


def _parse_array(val: Optional[str]) -> List[str]:
    """Parse PostgreSQL array string like {repo,read:user} into list."""
    if not val or val in ('{}', ''):
        return []
    # Remove braces and split
    inner = val.strip('{}')
    if not inner:
        return []
    return [s.strip('"') for s in inner.split(',')]


async def _query(sql: str) -> str:
    """Execute a query against the data database and return stdout."""
    stdout, stderr, code = await execute_sql(sql, DB_NAME)
    if code != 0:
        logger.error(f"Query failed: {stderr}")
        raise RuntimeError(f"Database error: {stderr}")
    return stdout.strip()


async def _execute(sql: str) -> None:
    """Execute a statement against the data database."""
    stdout, stderr, code = await execute_sql(sql, DB_NAME)
    if code != 0:
        logger.error(f"Execute failed: {stderr}")
        raise RuntimeError(f"Database error: {stderr}")


# ============================================================================
# GitHub Connections
# ============================================================================

_GH_CONN_COLS = [
    'id', 'user_id', 'access_token', 'refresh_token', 'token_expires_at',
    'github_user_id', 'github_username', 'scopes', 'created_at', 'updated_at'
]
_GH_CONN_SELECT = ', '.join(_GH_CONN_COLS)


def _row_to_gh_conn(row: dict) -> dict:
    return {
        'id': row['id'],
        'user_id': row['user_id'],
        'access_token': row.get('access_token'),
        'refresh_token': row.get('refresh_token'),
        'token_expires_at': row.get('token_expires_at'),
        'github_user_id': row['github_user_id'],
        'github_username': row['github_username'],
        'scopes': _parse_array(row.get('scopes')),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


async def get_github_connection_by_user(user_id: str) -> Optional[dict]:
    sql = f"SELECT {_GH_CONN_SELECT} FROM github_connections WHERE user_id = {_escape(user_id)}"
    result = await _query(sql)
    rows = _parse_rows(result, _GH_CONN_COLS)
    return _row_to_gh_conn(rows[0]) if rows else None


async def upsert_github_connection(
    user_id: str,
    access_token: str,
    refresh_token: Optional[str],
    token_expires_at: Optional[datetime],
    github_user_id: str,
    github_username: str,
    scopes: List[str],
) -> dict:
    """Insert or update a GitHub connection for a user."""
    enc_access = await authz_crypto.encrypt(access_token, f"github:{user_id}:access")
    enc_refresh = await authz_crypto.encrypt(refresh_token, f"github:{user_id}:refresh") if refresh_token else None

    sql = f"""
        INSERT INTO github_connections (user_id, access_token, refresh_token, token_expires_at, github_user_id, github_username, scopes)
        VALUES ({_escape(user_id)}, {_escape(enc_access)}, {_escape(enc_refresh)}, {_escape(token_expires_at)}, {_escape(github_user_id)}, {_escape(github_username)}, {_escape(scopes)})
        ON CONFLICT (user_id) DO UPDATE SET
            access_token = EXCLUDED.access_token,
            refresh_token = EXCLUDED.refresh_token,
            token_expires_at = EXCLUDED.token_expires_at,
            github_user_id = EXCLUDED.github_user_id,
            github_username = EXCLUDED.github_username,
            scopes = EXCLUDED.scopes,
            updated_at = CURRENT_TIMESTAMP
        RETURNING {_GH_CONN_SELECT}
    """
    result = await _query(sql)
    rows = _parse_rows(result, _GH_CONN_COLS)
    if not rows:
        raise RuntimeError("Failed to upsert github connection")
    return _row_to_gh_conn(rows[0])


async def delete_github_connection_by_user(user_id: str) -> bool:
    sql = f"DELETE FROM github_connections WHERE user_id = {_escape(user_id)}"
    await _execute(sql)
    return True


async def get_decrypted_github_token(user_id: str) -> Optional[str]:
    """Get the decrypted access token for a user's GitHub connection."""
    conn = await get_github_connection_by_user(user_id)
    if not conn or not conn.get('access_token'):
        return None
    return await authz_crypto.decrypt(conn['access_token'], f"github:{user_id}:access")


# ============================================================================
# Deployment Configs
# ============================================================================

_DC_COLS = [
    'id', 'app_id', 'github_connection_id', 'github_repo_owner',
    'github_repo_name', 'github_branch', 'deploy_path', 'port',
    'health_endpoint', 'build_command', 'start_command',
    'auto_deploy_enabled', 'staging_enabled', 'staging_port',
    'staging_path', 'created_at', 'updated_at'
]
_DC_SELECT = ', '.join(_DC_COLS)


def _row_to_dc(row: dict) -> dict:
    return {
        'id': row['id'],
        'app_id': row['app_id'],
        'github_connection_id': row['github_connection_id'],
        'github_repo_owner': row['github_repo_owner'],
        'github_repo_name': row['github_repo_name'],
        'github_branch': row.get('github_branch', 'main'),
        'deploy_path': row['deploy_path'],
        'port': int(row['port']) if row.get('port') else None,
        'health_endpoint': row.get('health_endpoint', '/api/health'),
        'build_command': row.get('build_command'),
        'start_command': row.get('start_command'),
        'auto_deploy_enabled': _parse_bool(row.get('auto_deploy_enabled')),
        'staging_enabled': _parse_bool(row.get('staging_enabled')),
        'staging_port': int(row['staging_port']) if row.get('staging_port') else None,
        'staging_path': row.get('staging_path'),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


async def list_deployment_configs() -> List[dict]:
    sql = f"SELECT {_DC_SELECT} FROM deployment_configs ORDER BY created_at DESC"
    result = await _query(sql)
    return [_row_to_dc(r) for r in _parse_rows(result, _DC_COLS)]


async def get_deployment_config(config_id: str) -> Optional[dict]:
    sql = f"SELECT {_DC_SELECT} FROM deployment_configs WHERE id = {_escape(config_id)}"
    result = await _query(sql)
    rows = _parse_rows(result, _DC_COLS)
    return _row_to_dc(rows[0]) if rows else None


async def get_deployment_config_by_app(app_id: str) -> Optional[dict]:
    sql = f"SELECT {_DC_SELECT} FROM deployment_configs WHERE app_id = {_escape(app_id)}"
    result = await _query(sql)
    rows = _parse_rows(result, _DC_COLS)
    return _row_to_dc(rows[0]) if rows else None


async def create_deployment_config(
    app_id: str,
    github_connection_id: str,
    github_repo_owner: str,
    github_repo_name: str,
    deploy_path: str,
    port: int,
    github_branch: str = 'main',
    health_endpoint: str = '/api/health',
    build_command: Optional[str] = None,
    start_command: Optional[str] = None,
    auto_deploy_enabled: bool = False,
    staging_enabled: bool = False,
    staging_port: Optional[int] = None,
    staging_path: Optional[str] = None,
) -> dict:
    sql = f"""
        INSERT INTO deployment_configs (
            app_id, github_connection_id, github_repo_owner, github_repo_name,
            github_branch, deploy_path, port, health_endpoint,
            build_command, start_command, auto_deploy_enabled,
            staging_enabled, staging_port, staging_path
        ) VALUES (
            {_escape(app_id)}, {_escape(github_connection_id)}, {_escape(github_repo_owner)}, {_escape(github_repo_name)},
            {_escape(github_branch)}, {_escape(deploy_path)}, {_escape(port)}, {_escape(health_endpoint)},
            {_escape(build_command)}, {_escape(start_command)}, {_escape(auto_deploy_enabled)},
            {_escape(staging_enabled)}, {_escape(staging_port)}, {_escape(staging_path)}
        ) RETURNING {_DC_SELECT}
    """
    result = await _query(sql)
    rows = _parse_rows(result, _DC_COLS)
    if not rows:
        raise RuntimeError("Failed to create deployment config")
    return _row_to_dc(rows[0])


async def update_deployment_config(config_id: str, **kwargs) -> Optional[dict]:
    sets = []
    for key, val in kwargs.items():
        if val is not None:
            sets.append(f"{key} = {_escape(val)}")
    if not sets:
        return await get_deployment_config(config_id)
    sql = f"UPDATE deployment_configs SET {', '.join(sets)} WHERE id = {_escape(config_id)} RETURNING {_DC_SELECT}"
    result = await _query(sql)
    rows = _parse_rows(result, _DC_COLS)
    return _row_to_dc(rows[0]) if rows else None


async def delete_deployment_config(config_id: str) -> bool:
    sql = f"DELETE FROM deployment_configs WHERE id = {_escape(config_id)}"
    await _execute(sql)
    return True


async def get_used_ports() -> List[int]:
    """Get all ports currently used by deployment configs."""
    sql = "SELECT port, staging_port FROM deployment_configs"
    result = await _query(sql)
    ports = set()
    for row in _parse_rows(result, ['port', 'staging_port']):
        if row.get('port'):
            ports.add(int(row['port']))
        if row.get('staging_port'):
            ports.add(int(row['staging_port']))
    return list(ports)


# ============================================================================
# Deployments
# ============================================================================

_DEPLOY_COLS = [
    'id', 'deployment_config_id', 'environment', 'status', 'deployment_type',
    'release_tag', 'release_id', 'commit_sha', 'deployed_by',
    'started_at', 'completed_at', 'error_message', 'logs',
    'previous_deployment_id', 'is_rollback', 'created_at'
]
_DEPLOY_SELECT = ', '.join(_DEPLOY_COLS)


def _row_to_deployment(row: dict) -> dict:
    return {
        'id': row['id'],
        'deployment_config_id': row['deployment_config_id'],
        'environment': row['environment'],
        'status': row['status'],
        'deployment_type': row.get('deployment_type', 'RELEASE'),
        'release_tag': row.get('release_tag'),
        'release_id': row.get('release_id'),
        'commit_sha': row.get('commit_sha'),
        'deployed_by': row['deployed_by'],
        'started_at': row['started_at'],
        'completed_at': row.get('completed_at'),
        'error_message': row.get('error_message'),
        'logs': row.get('logs'),
        'previous_deployment_id': row.get('previous_deployment_id'),
        'is_rollback': _parse_bool(row.get('is_rollback')),
        'created_at': row.get('created_at'),
    }


async def get_deployment(deployment_id: str) -> Optional[dict]:
    sql = f"SELECT {_DEPLOY_SELECT} FROM deployments WHERE id = {_escape(deployment_id)}"
    result = await _query(sql)
    rows = _parse_rows(result, _DEPLOY_COLS)
    return _row_to_deployment(rows[0]) if rows else None


async def list_deployments_for_config(
    config_id: str,
    environment: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10,
) -> List[dict]:
    conditions = [f"deployment_config_id = {_escape(config_id)}"]
    if environment:
        conditions.append(f"environment = {_escape(environment)}")
    if status:
        conditions.append(f"status = {_escape(status)}")
    where = ' AND '.join(conditions)
    sql = f"SELECT {_DEPLOY_SELECT} FROM deployments WHERE {where} ORDER BY started_at DESC LIMIT {limit}"
    result = await _query(sql)
    return [_row_to_deployment(r) for r in _parse_rows(result, _DEPLOY_COLS)]


async def get_latest_deployment(
    config_id: str,
    environment: str = 'PRODUCTION',
    status: str = 'COMPLETED',
) -> Optional[dict]:
    deps = await list_deployments_for_config(config_id, environment=environment, status=status, limit=1)
    return deps[0] if deps else None


async def create_deployment(
    deployment_config_id: str,
    deployed_by: str,
    environment: str = 'PRODUCTION',
    deployment_type: str = 'RELEASE',
    release_tag: Optional[str] = None,
    release_id: Optional[str] = None,
    commit_sha: Optional[str] = None,
    previous_deployment_id: Optional[str] = None,
    is_rollback: bool = False,
) -> dict:
    sql = f"""
        INSERT INTO deployments (
            deployment_config_id, environment, status, deployment_type,
            release_tag, release_id, commit_sha, deployed_by,
            previous_deployment_id, is_rollback
        ) VALUES (
            {_escape(deployment_config_id)}, {_escape(environment)}, 'PENDING', {_escape(deployment_type)},
            {_escape(release_tag)}, {_escape(release_id)}, {_escape(commit_sha)}, {_escape(deployed_by)},
            {_escape(previous_deployment_id)}, {_escape(is_rollback)}
        ) RETURNING {_DEPLOY_SELECT}
    """
    result = await _query(sql)
    rows = _parse_rows(result, _DEPLOY_COLS)
    if not rows:
        raise RuntimeError("Failed to create deployment")
    return _row_to_deployment(rows[0])


async def update_deployment(deployment_id: str, **kwargs) -> Optional[dict]:
    sets = []
    for key, val in kwargs.items():
        if key in ('status', 'completed_at', 'error_message', 'logs'):
            sets.append(f"{key} = {_escape(val)}")
    if not sets:
        return await get_deployment(deployment_id)
    sql = f"UPDATE deployments SET {', '.join(sets)} WHERE id = {_escape(deployment_id)} RETURNING {_DEPLOY_SELECT}"
    result = await _query(sql)
    rows = _parse_rows(result, _DEPLOY_COLS)
    return _row_to_deployment(rows[0]) if rows else None


# ============================================================================
# App Secrets
# ============================================================================

_SECRET_COLS = [
    'id', 'deployment_config_id', 'key', 'encrypted_value',
    'type', 'description', 'created_at', 'updated_at'
]
_SECRET_SELECT = ', '.join(_SECRET_COLS)

# Public columns (no encrypted_value)
_SECRET_PUBLIC_COLS = ['id', 'deployment_config_id', 'key', 'type', 'description', 'created_at', 'updated_at']
_SECRET_PUBLIC_SELECT = ', '.join(_SECRET_PUBLIC_COLS)


def _row_to_secret(row: dict, include_value: bool = False) -> dict:
    d = {
        'id': row['id'],
        'deployment_config_id': row['deployment_config_id'],
        'key': row['key'],
        'type': row.get('type', 'CUSTOM'),
        'description': row.get('description'),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }
    if include_value and row.get('encrypted_value'):
        d['encrypted_value'] = row['encrypted_value']
    return d


async def list_secrets(config_id: str) -> List[dict]:
    sql = f"SELECT {_SECRET_PUBLIC_SELECT} FROM app_secrets WHERE deployment_config_id = {_escape(config_id)} ORDER BY key ASC"
    result = await _query(sql)
    return [_row_to_secret(r) for r in _parse_rows(result, _SECRET_PUBLIC_COLS)]


async def upsert_secret(
    config_id: str,
    key: str,
    value: str,
    secret_type: str = 'CUSTOM',
    description: Optional[str] = None,
) -> dict:
    enc_value = await authz_crypto.encrypt(value, f"secret:{config_id}:{key}")
    sql = f"""
        INSERT INTO app_secrets (deployment_config_id, key, encrypted_value, type, description)
        VALUES ({_escape(config_id)}, {_escape(key)}, {_escape(enc_value)}, {_escape(secret_type)}, {_escape(description)})
        ON CONFLICT (deployment_config_id, key) DO UPDATE SET
            encrypted_value = EXCLUDED.encrypted_value,
            type = EXCLUDED.type,
            description = EXCLUDED.description,
            updated_at = CURRENT_TIMESTAMP
        RETURNING {_SECRET_PUBLIC_SELECT}
    """
    result = await _query(sql)
    rows = _parse_rows(result, _SECRET_PUBLIC_COLS)
    if not rows:
        raise RuntimeError("Failed to upsert secret")
    return _row_to_secret(rows[0])


async def delete_secret(secret_id: str) -> bool:
    sql = f"DELETE FROM app_secrets WHERE id = {_escape(secret_id)}"
    await _execute(sql)
    return True


async def get_secret_decrypted(config_id: str, key: str) -> Optional[str]:
    """Get a decrypted secret value by config ID and key."""
    sql = f"SELECT encrypted_value FROM app_secrets WHERE deployment_config_id = {_escape(config_id)} AND key = {_escape(key)}"
    result = await _query(sql)
    rows = _parse_rows(result, ['encrypted_value'])
    if not rows or not rows[0].get('encrypted_value'):
        return None
    return await authz_crypto.decrypt(rows[0]['encrypted_value'], f"secret:{config_id}:{key}")


# ============================================================================
# GitHub Releases
# ============================================================================

_RELEASE_COLS = [
    'id', 'deployment_config_id', 'release_id', 'tag_name', 'release_name',
    'body', 'commit_sha', 'published_at', 'is_prerelease', 'is_draft',
    'tarball_url', 'created_at'
]
_RELEASE_SELECT = ', '.join(_RELEASE_COLS)


def _row_to_release(row: dict) -> dict:
    return {
        'id': row['id'],
        'deployment_config_id': row['deployment_config_id'],
        'release_id': row['release_id'],
        'tag_name': row['tag_name'],
        'release_name': row.get('release_name'),
        'body': row.get('body'),
        'commit_sha': row.get('commit_sha'),
        'published_at': row['published_at'],
        'is_prerelease': _parse_bool(row.get('is_prerelease')),
        'is_draft': _parse_bool(row.get('is_draft')),
        'tarball_url': row.get('tarball_url'),
        'created_at': row['created_at'],
    }


async def list_releases(config_id: str) -> List[dict]:
    sql = f"SELECT {_RELEASE_SELECT} FROM github_releases WHERE deployment_config_id = {_escape(config_id)} ORDER BY published_at DESC"
    result = await _query(sql)
    return [_row_to_release(r) for r in _parse_rows(result, _RELEASE_COLS)]


async def upsert_release(
    config_id: str,
    release_id: str,
    tag_name: str,
    release_name: Optional[str] = None,
    body: Optional[str] = None,
    commit_sha: Optional[str] = None,
    published_at: Optional[datetime] = None,
    is_prerelease: bool = False,
    is_draft: bool = False,
    tarball_url: Optional[str] = None,
) -> dict:
    sql = f"""
        INSERT INTO github_releases (
            deployment_config_id, release_id, tag_name, release_name,
            body, commit_sha, published_at, is_prerelease, is_draft, tarball_url
        ) VALUES (
            {_escape(config_id)}, {_escape(release_id)}, {_escape(tag_name)}, {_escape(release_name)},
            {_escape(body)}, {_escape(commit_sha)}, {_escape(published_at)}, {_escape(is_prerelease)},
            {_escape(is_draft)}, {_escape(tarball_url)}
        ) ON CONFLICT (deployment_config_id, release_id) DO UPDATE SET
            tag_name = EXCLUDED.tag_name,
            release_name = EXCLUDED.release_name,
            body = EXCLUDED.body,
            commit_sha = EXCLUDED.commit_sha,
            published_at = EXCLUDED.published_at,
            is_prerelease = EXCLUDED.is_prerelease,
            is_draft = EXCLUDED.is_draft,
            tarball_url = EXCLUDED.tarball_url
        RETURNING {_RELEASE_SELECT}
    """
    result = await _query(sql)
    rows = _parse_rows(result, _RELEASE_COLS)
    if not rows:
        raise RuntimeError("Failed to upsert release")
    return _row_to_release(rows[0])


# ============================================================================
# App Databases
# ============================================================================

_APPDB_COLS = [
    'id', 'deployment_config_id', 'database_name', 'database_user',
    'encrypted_password', 'host', 'port', 'created_at', 'updated_at'
]
_APPDB_SELECT = ', '.join(_APPDB_COLS)
_APPDB_PUBLIC_COLS = ['id', 'deployment_config_id', 'database_name', 'database_user', 'host', 'port', 'created_at', 'updated_at']
_APPDB_PUBLIC_SELECT = ', '.join(_APPDB_PUBLIC_COLS)


def _row_to_appdb(row: dict, include_password: bool = False) -> dict:
    d = {
        'id': row['id'],
        'deployment_config_id': row['deployment_config_id'],
        'database_name': row['database_name'],
        'database_user': row['database_user'],
        'host': row.get('host', 'postgres'),
        'port': int(row['port']) if row.get('port') else 5432,
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }
    if include_password and row.get('encrypted_password'):
        d['encrypted_password'] = row['encrypted_password']
    return d


async def get_app_database(config_id: str) -> Optional[dict]:
    sql = f"SELECT {_APPDB_PUBLIC_SELECT} FROM app_databases WHERE deployment_config_id = {_escape(config_id)}"
    result = await _query(sql)
    rows = _parse_rows(result, _APPDB_PUBLIC_COLS)
    return _row_to_appdb(rows[0]) if rows else None


async def create_app_database(
    config_id: str,
    database_name: str,
    database_user: str,
    password: str,
    host: str = 'postgres',
    port: int = 5432,
) -> dict:
    enc_password = await authz_crypto.encrypt(password, f"dbpass:{config_id}")
    sql = f"""
        INSERT INTO app_databases (deployment_config_id, database_name, database_user, encrypted_password, host, port)
        VALUES ({_escape(config_id)}, {_escape(database_name)}, {_escape(database_user)}, {_escape(enc_password)}, {_escape(host)}, {_escape(port)})
        RETURNING {_APPDB_PUBLIC_SELECT}
    """
    result = await _query(sql)
    rows = _parse_rows(result, _APPDB_PUBLIC_COLS)
    if not rows:
        raise RuntimeError("Failed to create app database record")
    return _row_to_appdb(rows[0])


async def get_app_database_url(config_id: str) -> Optional[str]:
    """Get the full DATABASE_URL for an app, decrypting the password."""
    sql = f"SELECT {_APPDB_SELECT} FROM app_databases WHERE deployment_config_id = {_escape(config_id)}"
    result = await _query(sql)
    rows = _parse_rows(result, _APPDB_COLS)
    if not rows:
        return None
    row = rows[0]
    password = await authz_crypto.decrypt(row['encrypted_password'], f"dbpass:{config_id}")
    return f"postgresql://{row['database_user']}:{password}@{row['host']}:{row['port']}/{row['database_name']}"


async def delete_app_database(config_id: str) -> bool:
    sql = f"DELETE FROM app_databases WHERE deployment_config_id = {_escape(config_id)}"
    await _execute(sql)
    return True


# ============================================================================
# Enrichment helpers (joins across tables)
# ============================================================================

async def get_github_username_for_config(config_id: str) -> Optional[str]:
    """Get the GitHub username associated with a deployment config."""
    sql = f"""
        SELECT gc.github_username
        FROM deployment_configs dc
        JOIN github_connections gc ON gc.id = dc.github_connection_id
        WHERE dc.id = {_escape(config_id)}
    """
    result = await _query(sql)
    rows = _parse_rows(result, ['github_username'])
    return rows[0]['github_username'] if rows else None


async def enrich_config_with_relations(dc: dict) -> dict:
    """Add github_username, latest_deployment, etc. to a deployment config."""
    config_id = dc['id']
    # GitHub username
    dc['github_username'] = await get_github_username_for_config(config_id)
    # Latest production deployment
    dc['latest_deployment'] = await get_latest_deployment(config_id)
    return dc
