"""
Audio Processing Pipeline.

Handles real-time audio processing including:
- Audio capture from FreeSWITCH streams
- Buffering and chunking
- VAD (Voice Activity Detection)
- Audio format conversion
- Hold vs Human detection
"""

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import AsyncIterator, Callable, Optional
from uuid import UUID

import numpy as np
import structlog

from config.settings import get_settings
from models.call_state import AudioDetectionType
from services.vad import SileroVAD, SpeechSegment, get_vad
from services.detector import AudioClassifier, DetectionResult, get_audio_classifier

logger = structlog.get_logger(__name__)


class AudioChunk:
    """Represents a chunk of audio data."""

    def __init__(
        self,
        data: np.ndarray,
        sample_rate: int,
        timestamp: datetime,
        channel_id: Optional[UUID] = None,
    ):
        self.data = data
        self.sample_rate = sample_rate
        self.timestamp = timestamp
        self.channel_id = channel_id
        self.duration_ms = len(data) / sample_rate * 1000

    def to_bytes(self) -> bytes:
        """Convert to raw bytes (16-bit PCM)."""
        return (self.data * 32767).astype(np.int16).tobytes()

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        sample_rate: int,
        timestamp: Optional[datetime] = None,
        channel_id: Optional[UUID] = None,
    ) -> "AudioChunk":
        """Create from raw bytes (16-bit PCM)."""
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32767
        return cls(
            data=audio,
            sample_rate=sample_rate,
            timestamp=timestamp or datetime.utcnow(),
            channel_id=channel_id,
        )


class AudioBuffer:
    """
    Thread-safe audio buffer for accumulating chunks.
    
    Provides windowing for VAD and transcription.
    """

    def __init__(
        self,
        max_duration_seconds: float = 30.0,
        sample_rate: int = 16000,
    ):
        self.max_duration_seconds = max_duration_seconds
        self.sample_rate = sample_rate
        self._buffer: deque = deque()
        self._lock = asyncio.Lock()
        self._total_samples = 0
        self._max_samples = int(max_duration_seconds * sample_rate)

    async def add(self, chunk: AudioChunk) -> None:
        """Add a chunk to the buffer."""
        async with self._lock:
            self._buffer.append(chunk)
            self._total_samples += len(chunk.data)
            
            # Trim old data if buffer is full
            while self._total_samples > self._max_samples and self._buffer:
                old_chunk = self._buffer.popleft()
                self._total_samples -= len(old_chunk.data)

    async def get_window(
        self,
        duration_seconds: float,
    ) -> Optional[np.ndarray]:
        """Get the last N seconds of audio."""
        async with self._lock:
            if not self._buffer:
                return None
            
            target_samples = int(duration_seconds * self.sample_rate)
            chunks = []
            total = 0
            
            for chunk in reversed(list(self._buffer)):
                chunks.insert(0, chunk.data)
                total += len(chunk.data)
                if total >= target_samples:
                    break
            
            if not chunks:
                return None
            
            combined = np.concatenate(chunks)
            if len(combined) > target_samples:
                combined = combined[-target_samples:]
            
            return combined

    async def clear(self) -> None:
        """Clear the buffer."""
        async with self._lock:
            self._buffer.clear()
            self._total_samples = 0

    @property
    def duration_seconds(self) -> float:
        """Get current buffer duration."""
        return self._total_samples / self.sample_rate


class AudioProcessor:
    """
    Main audio processing pipeline.
    
    Coordinates audio capture, VAD, detection, and transcription.
    """

    def __init__(self):
        settings = get_settings()
        self.sample_rate = settings.audio_sample_rate
        self.chunk_duration_ms = settings.audio_chunk_duration_ms
        
        # Audio buffers per channel
        self._buffers: dict[UUID, AudioBuffer] = {}
        
        # VAD instances per channel
        self._vad_instances: dict[UUID, SileroVAD] = {}
        
        # Classifier instances per channel
        self._classifiers: dict[UUID, AudioClassifier] = {}
        
        # Detection state per channel
        self._detection_states: dict[UUID, dict] = {}
        
        # Callbacks
        self._on_speech_start: Optional[Callable] = None
        self._on_speech_end: Optional[Callable] = None
        self._on_audio_classification: Optional[Callable] = None
        self._on_human_detected: Optional[Callable] = None

    async def initialize(self) -> bool:
        """Initialize audio processing components."""
        # Pre-initialize shared VAD and classifier
        vad = get_vad()
        await vad.initialize()
        
        classifier = get_audio_classifier()
        await classifier.initialize()
        
        logger.info("Audio processor initialized")
        return True

    def get_buffer(self, channel_id: UUID) -> AudioBuffer:
        """Get or create buffer for a channel."""
        if channel_id not in self._buffers:
            self._buffers[channel_id] = AudioBuffer(sample_rate=self.sample_rate)
        return self._buffers[channel_id]

    def _get_vad(self, channel_id: UUID) -> SileroVAD:
        """Get or create VAD for a channel."""
        if channel_id not in self._vad_instances:
            self._vad_instances[channel_id] = SileroVAD()
        return self._vad_instances[channel_id]

    def _get_classifier(self, channel_id: UUID) -> AudioClassifier:
        """Get or create classifier for a channel."""
        if channel_id not in self._classifiers:
            self._classifiers[channel_id] = AudioClassifier()
        return self._classifiers[channel_id]

    def _get_detection_state(self, channel_id: UUID) -> dict:
        """Get or create detection state for a channel."""
        if channel_id not in self._detection_states:
            self._detection_states[channel_id] = {
                "last_detection": AudioDetectionType.UNKNOWN,
                "detection_confidence": 0.0,
                "human_detected": False,
                "human_detected_at": None,
                "consecutive_human_count": 0,
                "consecutive_hold_count": 0,
            }
        return self._detection_states[channel_id]

    async def process_chunk(
        self,
        channel_id: UUID,
        audio_bytes: bytes,
    ) -> Optional[DetectionResult]:
        """
        Process an incoming audio chunk.
        
        Args:
            channel_id: Channel/call UUID
            audio_bytes: Raw audio data (16-bit PCM)
            
        Returns:
            Detection result if classification was performed
        """
        chunk = AudioChunk.from_bytes(
            data=audio_bytes,
            sample_rate=self.sample_rate,
            channel_id=channel_id,
        )
        
        buffer = self.get_buffer(channel_id)
        await buffer.add(chunk)
        
        # Run VAD
        vad = self._get_vad(channel_id)
        
        async def on_speech_start(start_ms: int):
            if self._on_speech_start:
                await self._on_speech_start(channel_id, start_ms)
        
        async def on_speech_end(segment: SpeechSegment):
            if self._on_speech_end:
                await self._on_speech_end(channel_id, segment)
            
            # When speech ends, run classification on the segment
            if segment.audio is not None and len(segment.audio) > 8000:
                result = await self._classify_segment(channel_id, segment.audio)
                return result
        
        is_speech, confidence = await vad.process_chunk(
            chunk.data,
            on_speech_start=on_speech_start,
            on_speech_end=on_speech_end,
        )
        
        return None

    async def _classify_segment(
        self,
        channel_id: UUID,
        audio: np.ndarray,
    ) -> DetectionResult:
        """Classify an audio segment."""
        classifier = self._get_classifier(channel_id)
        state = self._get_detection_state(channel_id)
        
        result = await classifier.classify(audio, include_transcript=True)
        
        # Update detection state
        state["last_detection"] = result.detection_type
        state["detection_confidence"] = result.confidence
        
        # Track consecutive detections for smoothing
        if result.detection_type == AudioDetectionType.HUMAN_SPEECH:
            state["consecutive_human_count"] += 1
            state["consecutive_hold_count"] = 0
            
            # Require multiple consecutive human detections
            if state["consecutive_human_count"] >= 2 and not state["human_detected"]:
                state["human_detected"] = True
                state["human_detected_at"] = datetime.utcnow()
                
                if self._on_human_detected:
                    await self._on_human_detected(channel_id, result.confidence)
        
        elif result.detection_type == AudioDetectionType.HOLD_MUSIC:
            state["consecutive_hold_count"] += 1
            state["consecutive_human_count"] = 0
        
        else:
            # Unknown or IVR
            state["consecutive_human_count"] = 0
            state["consecutive_hold_count"] = 0
        
        # Notify classification callback
        if self._on_audio_classification:
            await self._on_audio_classification(channel_id, result)
        
        return result

    async def get_audio_for_transcription(
        self,
        channel_id: UUID,
        duration_seconds: float = 5.0,
    ) -> Optional[np.ndarray]:
        """Get audio window for transcription."""
        buffer = self.get_buffer(channel_id)
        return await buffer.get_window(duration_seconds)

    async def classify_audio(
        self,
        channel_id: UUID,
    ) -> AudioDetectionType:
        """
        Classify the current audio type.
        
        Returns whether it's hold music, IVR, human speech, etc.
        """
        buffer = self.get_buffer(channel_id)
        audio = await buffer.get_window(3.0)  # 3 seconds for classification
        
        if audio is None or len(audio) < 8000:
            return AudioDetectionType.SILENCE
        
        result = await self._classify_segment(channel_id, audio)
        return result.detection_type

    def is_human_detected(self, channel_id: UUID) -> bool:
        """Check if human has been detected on this channel."""
        state = self._get_detection_state(channel_id)
        return state.get("human_detected", False)

    def get_detection_state(self, channel_id: UUID) -> dict:
        """Get current detection state for a channel."""
        return self._get_detection_state(channel_id).copy()

    def set_callbacks(
        self,
        on_speech_start: Optional[Callable] = None,
        on_speech_end: Optional[Callable] = None,
        on_audio_classification: Optional[Callable] = None,
        on_human_detected: Optional[Callable] = None,
    ) -> None:
        """Set callback functions for audio events."""
        self._on_speech_start = on_speech_start
        self._on_speech_end = on_speech_end
        self._on_audio_classification = on_audio_classification
        self._on_human_detected = on_human_detected

    async def cleanup_channel(self, channel_id: UUID) -> None:
        """Clean up resources for a channel."""
        if channel_id in self._buffers:
            await self._buffers[channel_id].clear()
            del self._buffers[channel_id]
        
        if channel_id in self._vad_instances:
            self._vad_instances[channel_id].reset()
            del self._vad_instances[channel_id]
        
        if channel_id in self._classifiers:
            self._classifiers[channel_id].reset()
            del self._classifiers[channel_id]
        
        if channel_id in self._detection_states:
            del self._detection_states[channel_id]


# Singleton instance
_audio_processor: Optional[AudioProcessor] = None


def get_audio_processor() -> AudioProcessor:
    """Get the global audio processor instance."""
    global _audio_processor
    if _audio_processor is None:
        _audio_processor = AudioProcessor()
    return _audio_processor
