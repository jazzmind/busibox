"""
Admin routes — require Admin role.

Full CRUD on config_entries and app_registry.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import require_admin, require_admin_or_scope, require_secrets_scope, SENSITIVE_CATEGORIES

_config_read = require_admin_or_scope("config.email.read")
from config import config as svc_config
from services import app_registry, config_store
from services.encryption import decrypt_value, encrypt_value

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ConfigSetRequest(BaseModel):
    value: str
    encrypted: bool = False
    scope: str = "platform"
    app_id: Optional[str] = None
    tier: str = "admin"
    category: Optional[str] = None
    description: Optional[str] = None


class ConfigBulkItem(BaseModel):
    key: str
    value: str
    encrypted: bool = False
    scope: str = "platform"
    app_id: Optional[str] = None
    tier: str = "admin"
    category: Optional[str] = None
    description: Optional[str] = None


class ConfigBulkSetRequest(BaseModel):
    configs: List[ConfigBulkItem]


class BrandingUpdateRequest(BaseModel):
    companyName: Optional[str] = None
    siteName: Optional[str] = None
    slogan: Optional[str] = None
    logoUrl: Optional[str] = None
    faviconUrl: Optional[str] = None
    primaryColor: Optional[str] = None
    secondaryColor: Optional[str] = None
    textColor: Optional[str] = None
    addressLine1: Optional[str] = None
    addressLine2: Optional[str] = None
    addressCity: Optional[str] = None
    addressState: Optional[str] = None
    addressZip: Optional[str] = None
    addressCountry: Optional[str] = None
    supportEmail: Optional[str] = None
    supportPhone: Optional[str] = None
    customCss: Optional[str] = None
    setupComplete: Optional[bool] = None


class AppCreateRequest(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    type: str = "LIBRARY"
    ssoAudience: Optional[str] = None
    url: Optional[str] = None
    deployedPath: Optional[str] = None
    iconUrl: Optional[str] = None
    selectedIcon: Optional[str] = None
    displayOrder: int = 0
    isActive: bool = True
    healthEndpoint: Optional[str] = None
    githubRepo: Optional[str] = None
    deployedVersion: Optional[str] = None
    latestVersion: Optional[str] = None
    updateAvailable: bool = False
    devMode: bool = False
    primaryColor: Optional[str] = None
    secondaryColor: Optional[str] = None
    lastDeploymentStatus: Optional[str] = None


class AppUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    ssoAudience: Optional[str] = None
    url: Optional[str] = None
    deployedPath: Optional[str] = None
    iconUrl: Optional[str] = None
    selectedIcon: Optional[str] = None
    displayOrder: Optional[int] = None
    isActive: Optional[bool] = None
    healthEndpoint: Optional[str] = None
    githubRepo: Optional[str] = None
    deployedVersion: Optional[str] = None
    latestVersion: Optional[str] = None
    updateAvailable: Optional[bool] = None
    devMode: Optional[bool] = None
    primaryColor: Optional[str] = None
    secondaryColor: Optional[str] = None
    lastDeploymentStatus: Optional[str] = None


class ReorderItem(BaseModel):
    id: str
    displayOrder: int


class ReorderRequest(BaseModel):
    updates: List[ReorderItem]


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------

@router.get("/config")
async def list_configs(
    category: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    app_id: Optional[str] = Query(None),
    _user: dict = Depends(_config_read),
):
    """List config entries. Scope-only callers are restricted to category=smtp."""
    if _user.get("_scope_only"):
        category = "smtp"
    entries = await config_store.list_entries(
        category=category, scope=scope, app_id=app_id
    )
    configs = []
    for e in entries:
        value = e["value"]
        if e["encrypted"]:
            value = "********"
        configs.append({
            "key": e["key"],
            "value": value,
            "encrypted": e["encrypted"],
            "scope": e["scope"],
            "appId": e["app_id"],
            "tier": e["tier"],
            "category": e["category"],
            "description": e["description"],
        })
    return {"configs": configs, "total": len(configs)}


@router.get("/config/categories")
async def list_categories(_user: dict = Depends(require_admin)):
    categories = await config_store.list_categories()
    return {"categories": categories}


@router.get("/config/export")
async def export_configs(_user: dict = Depends(require_admin)):
    """Export all config entries. Sensitive category values are redacted unless
    the caller also has config.secrets.read scope (only granted to agent-api)."""
    has_secrets_scope = "config.secrets.read" in _user.get("scopes", [])
    entries = await config_store.export_all()
    configs = {}
    for e in entries:
        category = e.get("category") or ""
        is_sensitive = category in SENSITIVE_CATEGORIES
        value = e["value"]
        if is_sensitive and not has_secrets_scope:
            value = "********"
        elif e["encrypted"] and svc_config.encryption_key:
            try:
                value = decrypt_value(value, svc_config.encryption_key)
            except Exception:
                value = "********"
        configs[e["key"]] = {
            "value": value,
            "encrypted": e["encrypted"],
            "scope": e["scope"],
            "appId": e["app_id"],
            "tier": e["tier"],
            "category": e["category"],
            "description": e["description"],
        }
    return {"configs": configs, "total": len(configs)}


@router.post("/config/bulk")
async def bulk_set(
    body: ConfigBulkSetRequest,
    _user: dict = Depends(require_admin),
):
    """Bulk create/update config entries."""
    raw = []
    for item in body.configs:
        d = item.model_dump()
        if d["encrypted"] and svc_config.encryption_key:
            d["value"] = encrypt_value(d["value"], svc_config.encryption_key)
        raw.append(d)
    count = await config_store.bulk_upsert(raw)
    return {"count": count}


@router.get("/config/{key}")
async def get_config(
    key: str,
    app_id: Optional[str] = Query(None),
    _user: dict = Depends(_config_read),
):
    entry = await config_store.get_entry(key, app_id=app_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    if _user.get("_scope_only") and entry.get("category") != "smtp":
        raise HTTPException(status_code=403, detail="Scope restricted to smtp config")
    value = "********" if entry["encrypted"] else entry["value"]
    return {
        "key": entry["key"],
        "value": value,
        "encrypted": entry["encrypted"],
        "scope": entry["scope"],
        "appId": entry["app_id"],
        "tier": entry["tier"],
        "category": entry["category"],
        "description": entry["description"],
    }


@router.get("/config/{key}/raw")
async def get_config_raw(
    key: str,
    app_id: Optional[str] = Query(None),
    _user: dict = Depends(_config_read),
):
    """Get raw (unmasked/decrypted) value.

    For entries in sensitive categories (e.g. llm-keys), the caller must also
    hold config.secrets.read scope, which is only injected by authz when the
    token exchange originated inside agent-api (subject token aud=agent-api).
    This prevents admins from directly exfiltrating cloud provider credentials.
    """
    entry = await config_store.get_entry(key, app_id=app_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    if _user.get("_scope_only") and entry.get("category") != "smtp":
        raise HTTPException(status_code=403, detail="Scope restricted to smtp config")
    category = entry.get("category") or ""
    if category in SENSITIVE_CATEGORIES and "config.secrets.read" not in _user.get("scopes", []):
        raise HTTPException(
            status_code=403,
            detail="config.secrets.read scope required to read raw values for this config category",
        )
    value = entry["value"]
    if entry["encrypted"] and svc_config.encryption_key:
        value = decrypt_value(value, svc_config.encryption_key)
    return {"key": entry["key"], "value": value, "encrypted": entry["encrypted"]}


@router.put("/config/{key}")
async def set_config(
    key: str,
    body: ConfigSetRequest,
    _user: dict = Depends(require_admin),
):
    """Create or update a config entry."""
    value = body.value
    if body.encrypted and svc_config.encryption_key:
        value = encrypt_value(value, svc_config.encryption_key)

    entry = await config_store.upsert_entry(
        key,
        value,
        encrypted=body.encrypted,
        scope=body.scope,
        app_id=body.app_id,
        tier=body.tier,
        category=body.category,
        description=body.description,
    )
    display_value = "********" if entry["encrypted"] else entry["value"]
    return {
        "key": entry["key"],
        "value": display_value,
        "encrypted": entry["encrypted"],
        "scope": entry["scope"],
        "appId": entry["app_id"],
        "tier": entry["tier"],
        "category": entry["category"],
        "description": entry["description"],
    }


@router.delete("/config/{key}")
async def delete_config(
    key: str,
    app_id: Optional[str] = Query(None),
    _user: dict = Depends(require_admin),
):
    deleted = await config_store.delete_entry(key, app_id=app_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    return {"deleted": True, "key": key}


# ---------------------------------------------------------------------------
# Branding admin
# ---------------------------------------------------------------------------

@router.put("/branding")
async def update_branding(
    body: BrandingUpdateRequest,
    _user: dict = Depends(require_admin),
):
    """Update branding config entries (scope=branding, tier=public)."""
    updates = body.model_dump(exclude_none=True)
    for key, value in updates.items():
        str_value = str(value) if not isinstance(value, str) else value
        await config_store.upsert_entry(
            key,
            str_value,
            scope="branding",
            tier="public",
            category="branding",
            description=f"Portal branding: {key}",
        )
    # Return current branding state
    entries = await config_store.list_entries(scope="branding", tier="public")
    branding = {e["key"]: e["value"] for e in entries}
    return {"branding": branding}


# ---------------------------------------------------------------------------
# App registry admin
# ---------------------------------------------------------------------------

@router.get("/apps")
async def admin_list_apps(
    include_inactive: bool = Query(True),
    _user: dict = Depends(require_admin),
):
    apps = await app_registry.list_apps(active_only=not include_inactive)
    return {"apps": apps}


@router.post("/apps")
async def admin_create_app(
    body: AppCreateRequest,
    _user: dict = Depends(require_admin),
):
    existing = await app_registry.get_app(body.id)
    if existing:
        raise HTTPException(status_code=409, detail=f"App already exists: {body.id}")
    app = await app_registry.create_app(body.model_dump())
    return {"app": app}


@router.put("/apps/reorder")
async def admin_reorder_apps(
    body: ReorderRequest,
    _user: dict = Depends(require_admin),
):
    count = await app_registry.reorder_apps([item.model_dump() for item in body.updates])
    return {"updated": count}


@router.get("/apps/{app_id}")
async def admin_get_app(app_id: str, _user: dict = Depends(require_admin)):
    app = await app_registry.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")
    return {"app": app}


@router.put("/apps/{app_id}")
async def admin_update_app(
    app_id: str,
    body: AppUpdateRequest,
    _user: dict = Depends(require_admin),
):
    updates = body.model_dump(exclude_none=True)
    app = await app_registry.update_app(app_id, updates)
    if not app:
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")
    return {"app": app}


@router.delete("/apps/{app_id}")
async def admin_delete_app(app_id: str, _user: dict = Depends(require_admin)):
    deleted = await app_registry.delete_app(app_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"App not found: {app_id}")
    return {"deleted": True, "id": app_id}
