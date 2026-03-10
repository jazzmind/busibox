"""
App Registry — DB operations for app_registry table.
"""

from typing import List, Optional

# Module-level pool reference, set from main.py at startup
_pool = None


def set_pool(pool):
    global _pool
    _pool = pool


def _get_pool():
    if _pool is None:
        raise RuntimeError("App registry pool not initialised")
    return _pool


def _row_to_dict(row) -> dict:
    """Convert an asyncpg Record to a camelCase dict matching the frontend type."""
    if row is None:
        return None
    d = dict(row)
    return {
        "id": d["id"],
        "name": d["name"],
        "description": d.get("description"),
        "type": d["type"],
        "ssoAudience": d.get("sso_audience"),
        "url": d.get("url"),
        "deployedPath": d.get("deployed_path"),
        "iconUrl": d.get("icon_url"),
        "selectedIcon": d.get("selected_icon"),
        "displayOrder": d.get("display_order", 0),
        "isActive": d.get("is_active", True),
        "healthEndpoint": d.get("health_endpoint"),
        "githubRepo": d.get("github_repo"),
        "deployedVersion": d.get("deployed_version"),
        "latestVersion": d.get("latest_version"),
        "updateAvailable": d.get("update_available", False),
        "devMode": d.get("dev_mode", False),
        "primaryColor": d.get("primary_color"),
        "secondaryColor": d.get("secondary_color"),
        "lastDeploymentStatus": d.get("last_deployment_status"),
        "createdAt": d["created_at"].isoformat() if d.get("created_at") else None,
        "updatedAt": d["updated_at"].isoformat() if d.get("updated_at") else None,
    }


async def list_apps(*, active_only: bool = True, app_type: Optional[str] = None) -> List[dict]:
    """List registered apps."""
    pool = _get_pool()
    conditions = []
    params = []
    idx = 1

    if active_only:
        conditions.append("is_active = TRUE")
    if app_type:
        conditions.append(f"type = ${idx}")
        params.append(app_type)
        idx += 1

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM app_registry {where} ORDER BY display_order, name"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_row_to_dict(r) for r in rows]


async def get_app(app_id: str) -> Optional[dict]:
    pool = _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM app_registry WHERE id = $1", app_id)
    return _row_to_dict(row)


async def create_app(data: dict) -> dict:
    pool = _get_pool()
    sql = """
        INSERT INTO app_registry (
            id, name, description, type, sso_audience, url, deployed_path,
            icon_url, selected_icon, display_order, is_active, health_endpoint,
            github_repo, deployed_version, latest_version, update_available,
            dev_mode, primary_color, secondary_color, last_deployment_status
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20
        )
        RETURNING *
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            sql,
            data["id"],
            data["name"],
            data.get("description"),
            data.get("type", "LIBRARY"),
            data.get("ssoAudience"),
            data.get("url"),
            data.get("deployedPath"),
            data.get("iconUrl"),
            data.get("selectedIcon"),
            data.get("displayOrder", 0),
            data.get("isActive", True),
            data.get("healthEndpoint"),
            data.get("githubRepo"),
            data.get("deployedVersion"),
            data.get("latestVersion"),
            data.get("updateAvailable", False),
            data.get("devMode", False),
            data.get("primaryColor"),
            data.get("secondaryColor"),
            data.get("lastDeploymentStatus"),
        )
    return _row_to_dict(row)


async def update_app(app_id: str, updates: dict) -> Optional[dict]:
    """Update an app. Only supplied fields are changed."""
    pool = _get_pool()

    field_map = {
        "name": "name",
        "description": "description",
        "type": "type",
        "ssoAudience": "sso_audience",
        "url": "url",
        "deployedPath": "deployed_path",
        "iconUrl": "icon_url",
        "selectedIcon": "selected_icon",
        "displayOrder": "display_order",
        "isActive": "is_active",
        "healthEndpoint": "health_endpoint",
        "githubRepo": "github_repo",
        "deployedVersion": "deployed_version",
        "latestVersion": "latest_version",
        "updateAvailable": "update_available",
        "devMode": "dev_mode",
        "primaryColor": "primary_color",
        "secondaryColor": "secondary_color",
        "lastDeploymentStatus": "last_deployment_status",
    }

    set_parts = []
    params = []
    idx = 1

    for camel, col in field_map.items():
        if camel in updates:
            set_parts.append(f"{col} = ${idx}")
            params.append(updates[camel])
            idx += 1

    if not set_parts:
        return await get_app(app_id)

    params.append(app_id)
    sql = f"UPDATE app_registry SET {', '.join(set_parts)} WHERE id = ${idx} RETURNING *"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *params)
    return _row_to_dict(row) if row else None


async def delete_app(app_id: str) -> bool:
    pool = _get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM app_registry WHERE id = $1", app_id)
    return result.endswith("1")


async def reorder_apps(order_updates: List[dict]) -> int:
    """Batch update display_order. Each entry: {id, displayOrder}."""
    pool = _get_pool()
    count = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in order_updates:
                await conn.execute(
                    "UPDATE app_registry SET display_order = $1 WHERE id = $2",
                    item["displayOrder"],
                    item["id"],
                )
                count += 1
    return count
