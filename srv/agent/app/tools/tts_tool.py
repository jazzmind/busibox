"""Text-to-speech tool for conversational agents."""

import json
from typing import Optional

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import RunContext, Tool

from app.agents.core import BusiboxDeps
from app.api.llm import _litellm_text_to_speech
from app.config.settings import get_settings

settings = get_settings()


class TTSOutput(BaseModel):
    """Output schema for text-to-speech generation."""

    success: bool = Field(description="Whether speech generation succeeded")
    audio_url: Optional[str] = Field(default=None, description="URL where generated audio can be fetched")
    duration_seconds: Optional[float] = Field(default=None, description="Audio duration in seconds if available")
    error: Optional[str] = Field(default=None, description="Error message when generation fails")


async def _upload_audio_via_data_api(
    token: str,
    audio_bytes: bytes,
    mime_type: str,
    filename: str,
) -> str:
    """Upload generated audio through data-api and return a portal-relative media URL.
    
    Returns /portal/api/media/{file_id} which the browser can reach through
    the portal's media proxy route (handles auth + streams the file inline).
    """
    base_url = str(settings.data_api_url).rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    files = {
        "file": (filename, audio_bytes, mime_type),
    }
    data = {
        "visibility": "personal",
        "metadata": json.dumps({"source": "text_to_speech", "generated": True}),
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
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
        return f"/portal/api/media/{file_id}"


async def text_to_speech(
    ctx: RunContext[BusiboxDeps],
    text: str,
    voice: str = "alloy",
    speed: float = 1.0,
) -> TTSOutput:
    """
    Convert text to speech and return a URL to the generated audio.
    """
    try:
        audio_bytes, content_type = await _litellm_text_to_speech(
            model="voice",
            input_text=text,
            voice=voice,
            response_format="mp3",
            speed=speed,
        )

        token = getattr(ctx.deps.busibox_client, "_token", None)
        if not token:
            return TTSOutput(
                success=False,
                error="No authenticated token available for audio upload",
            )

        audio_url = await _upload_audio_via_data_api(
            token=token,
            audio_bytes=audio_bytes,
            mime_type=content_type or "audio/mpeg",
            filename="generated-speech.mp3",
        )

        return TTSOutput(
            success=True,
            audio_url=audio_url,
            duration_seconds=None,
        )
    except Exception as e:
        return TTSOutput(
            success=False,
            error=f"Text-to-speech failed: {str(e)}",
        )


tts_tool = Tool(
    text_to_speech,
    takes_ctx=True,
    name="text_to_speech",
    description=(
        "Convert text into speech audio and return a URL to the generated audio file. "
        "Use this when users request spoken output."
    ),
)

