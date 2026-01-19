"""
Voice Activity Detection Service.

Uses Silero VAD for accurate voice activity detection.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Tuple

import numpy as np
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class SpeechSegment:
    """Represents a detected speech segment."""

    start_ms: int
    end_ms: int
    confidence: float
    audio: Optional[np.ndarray] = None


class SileroVAD:
    """
    Voice Activity Detection using Silero VAD.
    
    Provides:
    - Real-time speech detection
    - Speech segment extraction
    - Configurable sensitivity
    """

    def __init__(
        self,
        threshold: Optional[float] = None,
        min_speech_duration_ms: Optional[int] = None,
        min_silence_duration_ms: Optional[int] = None,
        sample_rate: int = 16000,
    ):
        settings = get_settings()
        
        self.threshold = threshold or settings.vad_threshold
        self.min_speech_duration_ms = min_speech_duration_ms or settings.vad_min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms or settings.vad_min_silence_duration_ms
        self.sample_rate = sample_rate
        
        self._model = None
        self._initialized = False
        
        # Internal state
        self._speech_buffer: List[np.ndarray] = []
        self._speech_start_ms: Optional[int] = None
        self._silence_start_ms: Optional[int] = None
        self._is_speaking = False
        self._current_offset_ms = 0

    async def initialize(self) -> bool:
        """Initialize the Silero VAD model."""
        if self._initialized:
            return True
        
        try:
            logger.info("Initializing Silero VAD")
            
            # Import torch and silero
            import torch
            
            # Load Silero VAD model
            self._model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=True,  # Use ONNX for faster inference
            )
            
            self._initialized = True
            logger.info("Silero VAD initialized")
            return True
            
        except Exception as e:
            logger.error("Failed to initialize Silero VAD", error=str(e))
            # Fall back to energy-based VAD
            self._initialized = True
            return True

    def reset(self) -> None:
        """Reset VAD state for a new session."""
        self._speech_buffer = []
        self._speech_start_ms = None
        self._silence_start_ms = None
        self._is_speaking = False
        self._current_offset_ms = 0

    async def process_chunk(
        self,
        audio: np.ndarray,
        on_speech_start: Optional[Callable[[int], None]] = None,
        on_speech_end: Optional[Callable[[SpeechSegment], None]] = None,
    ) -> Tuple[bool, float]:
        """
        Process an audio chunk through VAD.
        
        Args:
            audio: Audio data (float32, mono, 16kHz)
            on_speech_start: Callback when speech starts
            on_speech_end: Callback when speech ends with segment
            
        Returns:
            Tuple of (is_speech, confidence)
        """
        chunk_duration_ms = int(len(audio) / self.sample_rate * 1000)
        
        # Get speech probability
        speech_prob = await self._get_speech_probability(audio)
        is_speech = speech_prob > self.threshold
        
        # State machine logic
        if is_speech:
            if not self._is_speaking:
                # Speech started
                if self._speech_start_ms is None:
                    self._speech_start_ms = self._current_offset_ms
                    self._speech_buffer = [audio]
                    
                    # Check if speech has been long enough
                    if on_speech_start:
                        on_speech_start(self._speech_start_ms)
                else:
                    self._speech_buffer.append(audio)
                
                # Reset silence counter
                self._silence_start_ms = None
                
                # Check min speech duration
                speech_duration = self._current_offset_ms - self._speech_start_ms + chunk_duration_ms
                if speech_duration >= self.min_speech_duration_ms:
                    self._is_speaking = True
            else:
                # Continuing speech
                self._speech_buffer.append(audio)
                self._silence_start_ms = None
        else:
            if self._is_speaking:
                # Silence during speech
                if self._silence_start_ms is None:
                    self._silence_start_ms = self._current_offset_ms
                    self._speech_buffer.append(audio)
                else:
                    self._speech_buffer.append(audio)
                    
                    # Check if silence is long enough to end speech
                    silence_duration = self._current_offset_ms - self._silence_start_ms + chunk_duration_ms
                    if silence_duration >= self.min_silence_duration_ms:
                        # Speech ended
                        segment = SpeechSegment(
                            start_ms=self._speech_start_ms,
                            end_ms=self._current_offset_ms,
                            confidence=speech_prob,
                            audio=np.concatenate(self._speech_buffer) if self._speech_buffer else None,
                        )
                        
                        if on_speech_end:
                            on_speech_end(segment)
                        
                        # Reset state
                        self._is_speaking = False
                        self._speech_start_ms = None
                        self._silence_start_ms = None
                        self._speech_buffer = []
            else:
                # Continued silence
                if self._speech_start_ms is not None:
                    # Was collecting potential speech, but too short
                    self._speech_start_ms = None
                    self._speech_buffer = []
        
        self._current_offset_ms += chunk_duration_ms
        return is_speech, speech_prob

    async def _get_speech_probability(self, audio: np.ndarray) -> float:
        """Get speech probability for audio chunk."""
        if self._model is None:
            # Fallback: energy-based detection
            energy = np.sqrt(np.mean(audio ** 2))
            # Map energy to probability-like value
            return min(1.0, energy * 10)
        
        try:
            import torch
            
            # Ensure correct shape
            if len(audio.shape) == 1:
                audio = audio.reshape(1, -1)
            
            # Convert to tensor
            audio_tensor = torch.from_numpy(audio).float()
            
            # Get probability
            with torch.no_grad():
                speech_prob = self._model(audio_tensor, self.sample_rate).item()
            
            return speech_prob
            
        except Exception as e:
            logger.warning("VAD inference error, using energy fallback", error=str(e))
            energy = np.sqrt(np.mean(audio ** 2))
            return min(1.0, energy * 10)

    @property
    def is_speaking(self) -> bool:
        """Check if currently detecting speech."""
        return self._is_speaking

    def get_current_speech_duration_ms(self) -> int:
        """Get duration of current speech segment."""
        if self._speech_start_ms is None:
            return 0
        return self._current_offset_ms - self._speech_start_ms


# Singleton instance
_vad: Optional[SileroVAD] = None


def get_vad() -> SileroVAD:
    """Get the global VAD instance."""
    global _vad
    if _vad is None:
        _vad = SileroVAD()
    return _vad
