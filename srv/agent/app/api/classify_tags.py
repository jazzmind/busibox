"""
Classify Tags API - semantic grouping of document tags/keywords.

POST /classify-tags: Group related tags using LLM for document library Tags tab.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.dependencies import get_principal
from app.config.settings import get_settings
from app.schemas.auth import Principal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["classify-tags"])
settings = get_settings()


# =============================================================================
# Request/Response Models
# =============================================================================


class ClassifyTagsRequest(BaseModel):
    """Request body for tag classification."""
    tags: List[str] = Field(..., description="List of tags/keywords to group")
    maxGroups: Optional[int] = Field(10, ge=1, le=30, description="Maximum number of groups")
    context: Optional[str] = Field(None, description="Optional context (e.g. 'library document keywords')")


class TagGroupOut(BaseModel):
    """A single tag group in the response."""
    name: str
    tags: List[str]
    confidence: Optional[float] = None


class ClassifyTagsResponse(BaseModel):
    """Response with semantically grouped tags."""
    groups: List[TagGroupOut]


# =============================================================================
# Helpers
# =============================================================================


def _get_litellm_base_url() -> str:
    """Get LiteLLM base URL."""
    url = str(settings.litellm_base_url).rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def _get_litellm_headers() -> Dict[str, str]:
    """Get auth headers for LiteLLM."""
    headers = {"Content-Type": "application/json"}
    if settings.litellm_api_key:
        headers["Authorization"] = f"Bearer {settings.litellm_api_key}"
    return headers


def _parse_json_from_content(content: str) -> Optional[Dict[str, Any]]:
    """Extract JSON from LLM response, handling markdown code blocks."""
    content = content.strip()
    # Try to find ```json ... ``` block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if match:
        content = match.group(1).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


# =============================================================================
# Endpoint
# =============================================================================


@router.post("/classify-tags", response_model=ClassifyTagsResponse)
async def classify_tags(
    request: ClassifyTagsRequest,
    principal: Principal = Depends(get_principal),
) -> ClassifyTagsResponse:
    """
    Group related tags semantically using an LLM.
    
    Used by the document library Tags tab to cluster keywords (e.g. cash, credit, tax
    -> "Financial"; renewable, solar -> "Energy").
    """
    if not request.tags:
        return ClassifyTagsResponse(groups=[])

    tags = [t.strip() for t in request.tags if t and t.strip()]
    if not tags:
        return ClassifyTagsResponse(groups=[])

    max_groups = request.maxGroups or 10
    context = request.context or "document keywords"

    system_prompt = """You are a tag classifier. Group the given tags into semantic clusters.
Each cluster should contain related tags (e.g. "cash", "credit", "tax" -> Financial; "solar", "renewable" -> Energy).
Return ONLY valid JSON in this exact format, no other text:
{"groups": [{"name": "Group Label", "tags": ["tag1", "tag2"], "confidence": 0.9}, ...]}
- Use "name" as a short label for the group (2-4 words).
- Include every input tag in exactly one group.
- Aim for roughly """ + str(max_groups) + """ groups; fewer if tags are very similar.
- confidence: 0.0-1.0 how confident the grouping is."""

    user_prompt = f"Context: {context}\n\nTags to group:\n" + "\n".join(f"- {t}" for t in tags)

    base_url = _get_litellm_base_url()
    headers = _get_litellm_headers()
    body = {
        "model": "agent",  # Use agent purpose (fast local model)
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.warning(f"LiteLLM classify-tags error {e.response.status_code}: {e.response.text}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM service unavailable for tag classification",
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cannot connect to LLM service",
        )

    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = message.get("content", "")

    parsed = _parse_json_from_content(content)
    if not parsed or "groups" not in parsed:
        logger.warning(f"Classify-tags: could not parse LLM response as JSON: {content[:200]}...")
        # Fallback: one tag per group
        groups = [
            TagGroupOut(name=t, tags=[t], confidence=0.6)
            for t in tags
        ]
        return ClassifyTagsResponse(groups=groups)

    # Validate and normalize
    seen = set()
    out_groups: List[TagGroupOut] = []
    for g in parsed.get("groups", []):
        if not isinstance(g, dict):
            continue
        name = str(g.get("name", "Other")).strip() or "Other"
        raw_tags = g.get("tags", [])
        group_tags = [str(t).strip() for t in raw_tags if t and str(t).strip() and str(t).strip() not in seen]
        for t in group_tags:
            seen.add(t)
        if group_tags:
            out_groups.append(
                TagGroupOut(
                    name=name,
                    tags=group_tags,
                    confidence=float(g["confidence"]) if isinstance(g.get("confidence"), (int, float)) else 0.8,
                )
            )

    # Ensure all input tags are in some group
    missing = set(tags) - seen
    if missing:
        for t in missing:
            out_groups.append(TagGroupOut(name=t, tags=[t], confidence=0.6))

    return ClassifyTagsResponse(groups=out_groups)
