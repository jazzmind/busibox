"""
Coding agent virtual-key management.

Lets an admin issue a scoped LiteLLM virtual key per coding agent (Claude
Code, OpenCode, Hermes, Pi) instead of handing out the shared LiteLLM master
key (the previous CLI-only flow — see cli/busibox/src/screens/utilities.rs).
Each key is restricted to the code-* purposes plus fast/frontier/fallback,
carries its own budget/rate limit, and is independently revocable.

LiteLLM remains the source of truth for a key's live state (budget, models,
spend, status) — see app.services.litellm_client. This router's own table
(CodingAgentKey) only indexes which key aliases exist and what Busibox calls
them, so "list all configured agents" doesn't require already knowing every
key value up front.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import get_litellm_connection, require_admin
from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.models.coding_agent_key import CodingAgentKey
from app.schemas.auth import Principal
from app.services.litellm_client import delete_key_by_alias, generate_key, get_litellm_base_url, list_keys_by_alias

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/coding-agents", tags=["coding-agents"])

# Coding agents only ever need the code-* purposes plus the general-purpose
# escalation tiers — not media/embedding/reranking purposes.
DEFAULT_ALLOWED_MODELS = [
    "code-writing", "code-reading", "code-testing",
    "code-securing", "code-planning", "code-documenting",
    "fast", "agent", "frontier", "fallback",
]

# Ready-to-use client config shape per coding agent. Data-driven so adding a
# 5th tool is a config addition, not a new code branch.
_CONFIG_TEMPLATES = {
    "claude-code": {
        "kind": "claude-code-settings-json",
        "instructions": "Merge into ~/.claude/settings.json",
        "build": lambda base_url, key: {
            "env": {
                "ANTHROPIC_BASE_URL": base_url,
                "ANTHROPIC_AUTH_TOKEN": key,
                "ANTHROPIC_MODEL": "code-writing",
            }
        },
    },
    "opencode": {
        "kind": "opencode-config-json",
        "instructions": (
            "Merge into opencode.json, then run `/connect` in OpenCode and paste "
            "the key when it asks for the LiteLLM provider API key (the key itself "
            "is not embedded in this file)."
        ),
        "build": lambda base_url, key: {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "litellm": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "LiteLLM",
                    "options": {"baseURL": f"{base_url}/v1"},
                    "models": {p: {"name": p} for p in DEFAULT_ALLOWED_MODELS},
                }
            },
        },
    },
    "hermes": {
        "kind": "hermes-config-yaml",
        "instructions": "Merge into ~/.hermes/config.yaml",
        "build": lambda base_url, key: {
            "model": {
                "default": "code-writing",
                "provider": "custom",
                "base_url": f"{base_url}/v1",
                "api_key": key,
                "context_length": 64000,
            }
        },
    },
    "pi-dev": {
        "kind": "pi-models-json",
        "instructions": (
            "Merge into ~/.pi/agent/models.json, and export "
            "BUSIBOX_LITELLM_KEY=<key> in your shell profile."
        ),
        "build": lambda base_url, key: {
            "providers": {
                "busibox-litellm": {
                    "baseUrl": f"{base_url}/v1",
                    "api": "openai-completions",
                    "apiKey": "$BUSIBOX_LITELLM_KEY",
                    "models": [{"id": p} for p in DEFAULT_ALLOWED_MODELS],
                }
            }
        },
    },
}

# Fallback for any custom/unrecognized agent name — LiteLLM speaks an
# OpenAI-compatible API, which most coding-agent tools accept generically.
_GENERIC_CONFIG_TEMPLATE = {
    "kind": "generic-openai-compatible",
    "instructions": (
        "This tool isn't one of the known presets — point it at an OpenAI-compatible "
        "base_url/api_key pair and use one of the suggested model names for the task type."
    ),
    "build": lambda base_url, key: {
        "base_url": f"{base_url}/v1",
        "api_key": key,
        "suggested_models": {
            "repo-scanning": "code-reading",
            "codegen-refactor": "code-writing",
            "test-writing": "code-testing",
            "docs": "code-documenting",
            "architecture-review": "code-planning",
            "security-review": "code-securing",
        },
    },
}


# =============================================================================
# Request/Response Models
# =============================================================================

class CodingAgentCreateRequest(BaseModel):
    agent_name: str = Field(..., description="e.g. claude-code, opencode, hermes, pi-dev, or a custom name")
    developer: Optional[str] = Field(None, description="Developer username, for per-developer keys")
    models: List[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_MODELS))
    max_budget: Optional[float] = Field(None, description="Cloud/frontier spend cap in dollars")
    budget_duration: Optional[str] = Field(None, description="e.g. '30d'")
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None


class CodingAgentInfo(BaseModel):
    agent_name: str
    developer: Optional[str] = None
    key_alias: str
    models: List[str] = Field(default_factory=list)
    max_budget: Optional[float] = None
    spend: float = 0.0
    last_used_at: Optional[datetime] = None
    created_at: datetime
    status: str = "active"


class CodingAgentCreateResponse(BaseModel):
    agent: CodingAgentInfo
    key: str = Field(..., description="Raw LiteLLM virtual key — shown once, cannot be retrieved again")


class CodingAgentListResponse(BaseModel):
    agents: List[CodingAgentInfo]


class CodingAgentConfigResponse(BaseModel):
    agent_name: str
    kind: str
    instructions: str
    base_url: str
    config: Dict[str, Any]
    note: str = "The virtual key is not embedded in this response for keys created before now; rotate via POST to get a fresh one if you no longer have it."


def _make_key_alias(agent_name: str, developer: Optional[str]) -> str:
    return f"{agent_name}:{developer}" if developer else agent_name


async def _last_used_at(key_alias: str) -> Optional[datetime]:
    """Best-effort lookup of a key's most recent request time from
    LiteLLM_SpendLogs. Returns None if the LiteLLM DB isn't reachable or the
    key has never been used."""
    conn = await get_litellm_connection()
    if not conn:
        return None
    try:
        row = await conn.fetchrow(
            'SELECT MAX("startTime") as last_used FROM "LiteLLM_SpendLogs" '
            'WHERE "api_key" IN (SELECT token FROM "LiteLLM_VerificationToken" WHERE key_alias = $1)',
            key_alias,
        )
        return row["last_used"] if row else None
    except Exception as e:
        logger.warning(f"Failed to look up last-used time for key_alias={key_alias}: {e}")
        return None
    finally:
        await conn.close()


def _info_from_litellm(row: CodingAgentKey, litellm_data: Optional[Dict[str, Any]], last_used: Optional[datetime]) -> CodingAgentInfo:
    keys = (litellm_data or {}).get("keys") or (litellm_data or {}).get("data") or []
    key_info = keys[0] if keys else {}
    return CodingAgentInfo(
        agent_name=row.agent_name,
        developer=row.developer,
        key_alias=row.key_alias,
        models=key_info.get("models") or [],
        max_budget=key_info.get("max_budget"),
        spend=key_info.get("spend") or 0.0,
        last_used_at=last_used,
        created_at=row.created_at,
        status="revoked" if row.revoked_at else ("blocked" if key_info.get("blocked") else "active"),
    )


# =============================================================================
# Endpoints
# =============================================================================

@router.get("", response_model=CodingAgentListResponse)
async def list_coding_agents(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CodingAgentListResponse:
    """List all configured coding-agent virtual keys, merged with their live
    LiteLLM state (models, budget, spend, status). Requires admin role."""
    require_admin(principal)

    result = await session.execute(
        select(CodingAgentKey).where(CodingAgentKey.revoked_at.is_(None)).order_by(CodingAgentKey.created_at.desc())
    )
    rows = result.scalars().all()

    agents = []
    for row in rows:
        litellm_data = await list_keys_by_alias(row.key_alias)
        last_used = await _last_used_at(row.key_alias)
        agents.append(_info_from_litellm(row, litellm_data, last_used))

    return CodingAgentListResponse(agents=agents)


@router.post("", response_model=CodingAgentCreateResponse)
async def create_coding_agent(
    body: CodingAgentCreateRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CodingAgentCreateResponse:
    """Issue a new scoped LiteLLM virtual key for a coding agent. Requires
    admin role. The raw key is returned once and cannot be retrieved again —
    only rotated (delete + recreate)."""
    require_admin(principal)

    key_alias = _make_key_alias(body.agent_name, body.developer)

    existing = await session.execute(
        select(CodingAgentKey).where(CodingAgentKey.key_alias == key_alias, CodingAgentKey.revoked_at.is_(None))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An active key already exists for '{key_alias}'")

    result = await generate_key(
        key_alias=key_alias,
        models=body.models or list(DEFAULT_ALLOWED_MODELS),
        metadata={
            "agent_name": body.agent_name,
            "developer": body.developer,
            "created_by": "admin-ui",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        max_budget=body.max_budget,
        budget_duration=body.budget_duration,
        rpm_limit=body.rpm_limit,
        tpm_limit=body.tpm_limit,
    )
    if not result or "key" not in result:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LiteLLM key generation failed")

    row = CodingAgentKey(
        agent_name=body.agent_name,
        developer=body.developer,
        key_alias=key_alias,
        created_by=principal.email or principal.sub,
    )
    session.add(row)
    await session.commit()

    return CodingAgentCreateResponse(
        agent=CodingAgentInfo(
            agent_name=row.agent_name,
            developer=row.developer,
            key_alias=row.key_alias,
            models=body.models or list(DEFAULT_ALLOWED_MODELS),
            max_budget=body.max_budget,
            spend=0.0,
            last_used_at=None,
            created_at=row.created_at,
            status="active",
        ),
        key=result["key"],
    )


@router.delete("/{key_alias}")
async def revoke_coding_agent(
    key_alias: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, bool]:
    """Revoke a coding agent's virtual key. Requires admin role."""
    require_admin(principal)

    result = await session.execute(
        select(CodingAgentKey).where(CodingAgentKey.key_alias == key_alias, CodingAgentKey.revoked_at.is_(None))
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No active key found for '{key_alias}'")

    ok = await delete_key_by_alias(key_alias)
    if not ok:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LiteLLM key revocation failed")

    row.revoked_at = datetime.now(timezone.utc)
    await session.commit()
    return {"revoked": True}


@router.get("/{key_alias}/config", response_model=CodingAgentConfigResponse)
async def get_coding_agent_config(
    key_alias: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CodingAgentConfigResponse:
    """Return a ready-to-use client config snippet for the given coding
    agent. Does not include the raw key (only shown once at creation) —
    the caller must have saved it, or rotate the key to get a fresh one."""
    require_admin(principal)

    result = await session.execute(
        select(CodingAgentKey).where(CodingAgentKey.key_alias == key_alias, CodingAgentKey.revoked_at.is_(None))
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No active key found for '{key_alias}'")

    base_url = get_litellm_base_url()

    template = _CONFIG_TEMPLATES.get(row.agent_name, _GENERIC_CONFIG_TEMPLATE)
    placeholder = "<paste your virtual key here>"
    return CodingAgentConfigResponse(
        agent_name=row.agent_name,
        kind=template["kind"],
        instructions=template["instructions"],
        base_url=base_url,
        config=template["build"](base_url, placeholder),
    )
