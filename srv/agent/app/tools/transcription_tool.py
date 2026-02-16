"""Audio transcription tool for conversational agents."""

from typing import Optional

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import RunContext, Tool

from app.agents.core import BusiboxDeps
from app.api.llm import _litellm_transcribe_audio


class TranscriptionOutput(BaseModel):
    """Output schema for audio transcription."""

    success: bool = Field(description="Whether transcription succeeded")
    text: Optional[str] = Field(default=None, description="Transcribed text")
    language: Optional[str] = Field(default=None, description="Detected/requested language")
    duration: Optional[float] = Field(default=None, description="Audio duration in seconds if available")
    error: Optional[str] = Field(default=None, description="Error message if transcription fails")


async def transcribe_audio(
    ctx: RunContext[BusiboxDeps],
    file_url: str,
    language: Optional[str] = None,
) -> TranscriptionOutput:
    """
    Transcribe an audio file from a URL.

    The URL is typically a MinIO presigned URL or an attachment URL.
    """
    try:
        headers = {}
        token = getattr(ctx.deps.busibox_client, "_token", None)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            download_response = await client.get(file_url, headers=headers)
            download_response.raise_for_status()
            file_bytes = download_response.content
            content_type = download_response.headers.get("content-type", "audio/wav")

        result = await _litellm_transcribe_audio(
            file_bytes=file_bytes,
            filename="audio-input.wav",
            content_type=content_type,
            model="transcribe",
            language=language,
        )

        return TranscriptionOutput(
            success=True,
            text=result.get("text"),
            language=result.get("language", language),
            duration=result.get("duration"),
        )
    except Exception as e:
        return TranscriptionOutput(
            success=False,
            error=f"Audio transcription failed: {str(e)}",
        )


transcription_tool = Tool(
    transcribe_audio,
    takes_ctx=True,
    name="transcribe_audio",
    description=(
        "Transcribe an audio file from a URL into text. "
        "Use this for voice notes, recordings, meetings, or spoken content."
    ),
)

