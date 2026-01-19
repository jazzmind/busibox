"""
Transcription Service.

Provides real-time speech-to-text using faster-whisper.
Handles streaming transcription with interim results.
"""

import asyncio
import logging
from datetime import datetime
from typing import AsyncIterator, Callable, Optional
from uuid import UUID

import numpy as np
import structlog

from config.settings import get_settings
from models.call_state import Speaker
from models.transcript import TranscriptSegment

logger = structlog.get_logger(__name__)


class TranscriptionResult:
    """Result from transcription service."""

    def __init__(
        self,
        text: str,
        confidence: float,
        start_time_ms: int,
        end_time_ms: int,
        is_final: bool = True,
        language: str = "en",
    ):
        self.text = text
        self.confidence = confidence
        self.start_time_ms = start_time_ms
        self.end_time_ms = end_time_ms
        self.is_final = is_final
        self.language = language


class TranscriptionService:
    """
    Real-time transcription using faster-whisper.
    
    Provides:
    - Streaming transcription with interim results
    - Speaker diarization (future)
    - Language detection
    """

    def __init__(self):
        settings = get_settings()
        self._model_name = settings.whisper_model
        self._device = settings.whisper_device
        self._compute_type = settings.whisper_compute_type
        self._model = None
        self._initialized = False
        
        # Track transcription sessions
        self._sessions: dict[UUID, dict] = {}

    async def initialize(self) -> bool:
        """Initialize the Whisper model."""
        if self._initialized:
            return True
        
        try:
            logger.info(
                "Initializing Whisper model",
                model=self._model_name,
                device=self._device,
            )
            
            # Import here to avoid loading at module import time
            from faster_whisper import WhisperModel
            
            # Determine device
            device = self._device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            
            self._model = WhisperModel(
                self._model_name,
                device=device,
                compute_type=self._compute_type,
            )
            
            self._initialized = True
            logger.info("Whisper model initialized", device=device)
            return True
            
        except Exception as e:
            logger.error("Failed to initialize Whisper", error=str(e))
            return False

    def start_session(self, channel_id: UUID) -> None:
        """Start a transcription session for a channel."""
        self._sessions[channel_id] = {
            "start_time": datetime.utcnow(),
            "segments": [],
            "total_audio_ms": 0,
        }

    def end_session(self, channel_id: UUID) -> list[TranscriptSegment]:
        """End a transcription session and return all segments."""
        if channel_id not in self._sessions:
            return []
        
        session = self._sessions.pop(channel_id)
        return session.get("segments", [])

    async def transcribe(
        self,
        audio: np.ndarray,
        channel_id: UUID,
        speaker: Speaker = Speaker.REMOTE,
    ) -> list[TranscriptionResult]:
        """
        Transcribe audio data.
        
        Args:
            audio: Audio data as numpy array (float32, mono)
            channel_id: Channel ID for session tracking
            speaker: Speaker identification
            
        Returns:
            List of transcription results
        """
        if not self._initialized:
            await self.initialize()
        
        if self._model is None:
            logger.error("Whisper model not available")
            return []
        
        try:
            # Get session info
            session = self._sessions.get(channel_id, {})
            offset_ms = session.get("total_audio_ms", 0)
            
            # Run transcription
            segments, info = self._model.transcribe(
                audio,
                beam_size=5,
                language="en",
                vad_filter=True,
                vad_parameters=dict(
                    min_speech_duration_ms=250,
                    min_silence_duration_ms=500,
                ),
            )
            
            results = []
            for segment in segments:
                result = TranscriptionResult(
                    text=segment.text.strip(),
                    confidence=segment.avg_logprob,
                    start_time_ms=offset_ms + int(segment.start * 1000),
                    end_time_ms=offset_ms + int(segment.end * 1000),
                    is_final=True,
                    language=info.language,
                )
                results.append(result)
                
                # Store in session
                if channel_id in self._sessions:
                    segment_model = TranscriptSegment(
                        speaker=speaker,
                        text=result.text,
                        start_time_ms=result.start_time_ms,
                        end_time_ms=result.end_time_ms,
                        confidence=result.confidence,
                        language=result.language,
                    )
                    self._sessions[channel_id]["segments"].append(segment_model)
            
            # Update offset
            audio_duration_ms = int(len(audio) / 16000 * 1000)
            if channel_id in self._sessions:
                self._sessions[channel_id]["total_audio_ms"] += audio_duration_ms
            
            return results
            
        except Exception as e:
            logger.error(
                "Transcription error",
                channel_id=str(channel_id),
                error=str(e),
            )
            return []

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[np.ndarray],
        channel_id: UUID,
        on_result: Callable[[TranscriptionResult], None],
    ) -> None:
        """
        Transcribe a stream of audio chunks.
        
        Accumulates audio and transcribes when VAD detects end of speech.
        """
        settings = get_settings()
        buffer = []
        buffer_duration_ms = 0
        min_transcribe_ms = 1000  # Minimum audio before transcribing
        
        async for chunk in audio_stream:
            buffer.append(chunk)
            buffer_duration_ms += int(len(chunk) / settings.audio_sample_rate * 1000)
            
            # Transcribe when we have enough audio
            if buffer_duration_ms >= min_transcribe_ms:
                combined = np.concatenate(buffer)
                results = await self.transcribe(combined, channel_id)
                
                for result in results:
                    on_result(result)
                
                buffer = []
                buffer_duration_ms = 0
        
        # Transcribe remaining audio
        if buffer:
            combined = np.concatenate(buffer)
            results = await self.transcribe(combined, channel_id)
            for result in results:
                on_result(result)


# Singleton instance
_transcription_service: Optional[TranscriptionService] = None


def get_transcription_service() -> TranscriptionService:
    """Get the global transcription service instance."""
    global _transcription_service
    if _transcription_service is None:
        _transcription_service = TranscriptionService()
    return _transcription_service
