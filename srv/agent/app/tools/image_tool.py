"""Image generation tool for conversational agents.

Supports both URL-based models (DALL-E 3, FLUX) and base64-only models
(gpt-image-1, gpt-image-1-mini) via the LiteLLM /images/generations proxy.

Generated images are uploaded to MinIO via the data-api (stored in the user's
personal MEDIA library) and served through the portal's media proxy route
(/portal/api/media/{fileId}) which handles auth and streams the file inline.
"""

import base64
import json
import logging
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import RunContext, Tool

from app.agents.core import BusiboxDeps
from app.api.llm import _litellm_generate_image
from app.config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ImageOutput(BaseModel):
    """Output schema for image generation."""

    success: bool = Field(description="Whether image generation succeeded")
    image_url: Optional[str] = Field(
        default=None,
        description="Portal-relative URL to serve the image (/portal/api/media/{fileId})"
    )
    file_id: Optional[str] = Field(default=None, description="File ID in the data store")
    revised_prompt: Optional[str] = Field(default=None, description="Model-revised prompt if provided")
    error: Optional[str] = Field(default=None, description="Error message when generation fails")


def _data_api_token(deps: Any) -> Optional[str]:
    """The caller's JWT for data-api, from the tool context's BusiboxClient.

    The client keeps a default token plus per-audience exchanged tokens and
    exposes them through ``token_for``; it has never had a ``_token``
    attribute, which is what this function used to look for — so every
    upload failed with "no authenticated token". Test doubles may still
    provide a bare ``_token``; accept that last.
    """
    client = getattr(deps, "busibox_client", None)
    if client is None:
        return None
    token_for = getattr(client, "token_for", None)
    if callable(token_for):
        try:
            return token_for("data-api") or None
        except Exception:  # noqa: BLE001 — fall through to the attribute probes
            pass
    return getattr(client, "_default_token", None) or getattr(client, "_token", None)


async def _upload_image_via_data_api(
    token: str,
    image_bytes: bytes,
    mime_type: str,
    filename: str,
) -> str:
    """Upload generated image through data-api and return the file ID.
    
    The data-api automatically routes image uploads to the user's personal
    MEDIA library when visibility is 'personal'.
    
    Returns the file_id; the caller constructs a portal-relative URL
    (/portal/api/media/{file_id}) that the browser can reach.
    """
    base_url = str(settings.data_api_url).rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    files = {
        "file": (filename, image_bytes, mime_type),
    }
    data = {
        "visibility": "personal",
        "metadata": json.dumps({"source": "image_generation", "generated": True}),
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        upload_resp = await client.post(
            f"{base_url}/upload",
            headers=headers,
            files=files,
            data=data,
        )
        upload_resp.raise_for_status()
        upload_data = upload_resp.json()
        file_id = upload_data.get("fileId")
        if not file_id:
            raise ValueError("Data API upload response missing fileId")
        return file_id


async def _download_image_from_url(url: str) -> bytes:
    """Download image bytes from an external URL (e.g. DALL-E temporary URL)."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def generate_image(
    ctx: RunContext[BusiboxDeps],
    prompt: str,
    size: str = "1024x1024",
    style: Optional[str] = None,
) -> ImageOutput:
    """
    Generate an image from a text prompt.

    The image is stored in the user's personal MEDIA library in MinIO and a
    portal-relative URL (/portal/api/media/{fileId}) is returned so the UI
    can render the image through the portal's authenticated media proxy.
    
    Handles two response formats from LiteLLM:
    - URL-based models (DALL-E 3, FLUX): response contains url field
    - Base64 models (gpt-image-1, gpt-image-1-mini): response contains b64_json field
    """
    try:
        effective_prompt = prompt if not style else f"{prompt}. Style: {style}"
        
        result = await _litellm_generate_image(
            model="image",
            prompt=effective_prompt,
            size=size,
            n=1,
        )
        
        data = result.get("data", [])
        first = data[0] if data else {}
        revised_prompt = first.get("revised_prompt")
        
        # Get the raw image bytes and determine format
        image_bytes: Optional[bytes] = None
        mime_type = "image/png"
        
        # Check for base64 data first (gpt-image-1, gpt-image-1-mini)
        if first.get("b64_json"):
            b64_data = first["b64_json"]
            image_bytes = base64.b64decode(b64_data)
            logger.info(f"Image tool: decoded b64_json response ({len(image_bytes)} bytes)")
        
        # Check for URL (DALL-E 3, FLUX, etc.) - download the image
        elif first.get("url"):
            raw_url = first["url"]
            logger.info(f"Image tool: downloading image from URL: {raw_url[:80]}...")
            image_bytes = await _download_image_from_url(raw_url)
            # Infer MIME type from URL or default to png
            if ".jpg" in raw_url or ".jpeg" in raw_url:
                mime_type = "image/jpeg"
            elif ".webp" in raw_url:
                mime_type = "image/webp"
            logger.info(f"Image tool: downloaded image ({len(image_bytes)} bytes)")
        
        if not image_bytes:
            logger.warning(f"Image tool: no url or b64_json in response. Keys: {list(first.keys())}")
            return ImageOutput(
                success=False,
                error="Image model returned no image data. Check LiteLLM logs for details.",
            )
        
        # Upload to MinIO via data-api
        token = _data_api_token(ctx.deps)
        if not token:
            return ImageOutput(
                success=False,
                error="No authenticated token available for image upload",
            )
        
        ext = "png" if mime_type == "image/png" else ("jpg" if mime_type == "image/jpeg" else "webp")
        filename = f"generated-image.{ext}"
        
        file_id = await _upload_image_via_data_api(
            token=token,
            image_bytes=image_bytes,
            mime_type=mime_type,
            filename=filename,
        )
        
        # Build a portal-relative URL the browser can reach
        media_url = f"/portal/api/media/{file_id}"
        
        logger.info(
            f"Image tool: uploaded to MinIO, file_id={file_id}, "
            f"media_url={media_url}"
        )
        
        return ImageOutput(
            success=True,
            image_url=media_url,
            file_id=file_id,
            revised_prompt=revised_prompt,
        )
    except Exception as e:
        logger.error(f"Image generation failed: {e}", exc_info=True)
        error_str = str(e)
        if "APIConnectionError" in error_str or "Connection error" in error_str:
            user_error = "Image generation is temporarily unavailable (service connection error). Please try again later."
        elif "RateLimitError" in error_str or "rate_limit" in error_str:
            user_error = "Image generation rate limit reached. Please wait a moment and try again."
        elif "AuthenticationError" in error_str:
            user_error = "Image generation service authentication failed. Please contact an administrator."
        else:
            user_error = f"Image generation failed: {error_str.split('{')[0].strip()}" if '{' in error_str else f"Image generation failed: {error_str}"
        return ImageOutput(
            success=False,
            error=user_error,
        )


image_tool = Tool(
    generate_image,
    takes_ctx=True,
    name="generate_image",
    description=(
        "Generate an image from text and return a URL to the generated image. "
        "Use this when the user asks for visual content, concepts, mockups, or illustrations."
    ),
)

