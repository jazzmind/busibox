"""
Hold vs Human Detection Service.

Classifies audio to detect:
- Hold music
- IVR prompts
- Human speech
- Silence
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np
import structlog

from config.settings import get_settings
from models.call_state import AudioDetectionType
from services.transcription import get_transcription_service, TranscriptionResult
from services.vad import SileroVAD, SpeechSegment, get_vad

logger = structlog.get_logger(__name__)


# IVR/Hold detection keywords
IVR_KEYWORDS = [
    "press",
    "dial",
    "option",
    "menu",
    "for billing",
    "for sales",
    "for support",
    "for assistance",
    "please hold",
    "your call is important",
    "thank you for calling",
    "all representatives are busy",
    "estimated wait time",
    "you are number",
    "in queue",
    "next available",
    "hours of operation",
    "if you know your party",
    "extension",
]

HUMAN_INDICATORS = [
    "how can i help",
    "may i have your",
    "what is your",
    "can you tell me",
    "i understand",
    "let me check",
    "one moment",
    "thank you for holding",
    "this is",
    "speaking with",
    "my name is",
]


@dataclass
class DetectionResult:
    """Result of audio detection."""

    detection_type: AudioDetectionType
    confidence: float
    reason: str
    transcript: Optional[str] = None
    features: Optional[Dict] = None


class AudioClassifier:
    """
    Classifies audio into categories:
    - SILENCE: No significant audio
    - HOLD_MUSIC: Music or repetitive patterns
    - IVR_PROMPT: Automated voice prompts
    - HUMAN_SPEECH: Live human conversation
    """

    def __init__(self):
        self._vad = get_vad()
        self._transcription = get_transcription_service()
        
        # Detection history for smoothing
        self._history: Deque[DetectionResult] = deque(maxlen=10)
        
        # Audio feature history for pattern detection
        self._feature_history: Deque[Dict] = deque(maxlen=50)
        
        # Hold music detection state
        self._music_pattern_count = 0
        self._last_ivr_text: Optional[str] = None

    async def initialize(self) -> bool:
        """Initialize the classifier."""
        await self._vad.initialize()
        await self._transcription.initialize()
        return True

    async def classify(
        self,
        audio: np.ndarray,
        include_transcript: bool = True,
    ) -> DetectionResult:
        """
        Classify an audio segment.
        
        Args:
            audio: Audio data (float32, mono, 16kHz)
            include_transcript: Whether to run transcription
            
        Returns:
            DetectionResult with classification
        """
        # Extract audio features
        features = self._extract_features(audio)
        self._feature_history.append(features)
        
        # Quick checks first
        if features["rms_energy"] < 0.001:
            return DetectionResult(
                detection_type=AudioDetectionType.SILENCE,
                confidence=0.95,
                reason="Very low energy",
                features=features,
            )
        
        # Check for music patterns
        music_score = self._detect_music_patterns(features)
        if music_score > 0.7:
            return DetectionResult(
                detection_type=AudioDetectionType.HOLD_MUSIC,
                confidence=music_score,
                reason="Detected music patterns",
                features=features,
            )
        
        # Check for speech using VAD
        is_speech, speech_prob = await self._vad.process_chunk(audio)
        
        if not is_speech and speech_prob < 0.3:
            if music_score > 0.3:
                return DetectionResult(
                    detection_type=AudioDetectionType.HOLD_MUSIC,
                    confidence=0.5 + music_score * 0.3,
                    reason="Low speech, possible hold music",
                    features=features,
                )
            return DetectionResult(
                detection_type=AudioDetectionType.UNKNOWN,
                confidence=0.5,
                reason="Low speech probability",
                features=features,
            )
        
        # Speech detected - run transcription to classify
        transcript = None
        if include_transcript and len(audio) > 8000:  # At least 0.5 second
            results = await self._transcription.transcribe(
                audio,
                channel_id=None,  # No session tracking for detection
            )
            if results:
                transcript = " ".join(r.text for r in results)
        
        # Classify based on transcript content
        if transcript:
            classification = self._classify_transcript(transcript)
            classification.transcript = transcript
            classification.features = features
            return classification
        
        # No transcript, use audio features
        return self._classify_from_features(features, speech_prob)

    def _extract_features(self, audio: np.ndarray) -> Dict:
        """Extract audio features for classification."""
        # Basic energy
        rms_energy = np.sqrt(np.mean(audio ** 2))
        
        # Zero crossing rate (higher for music)
        zero_crossings = np.sum(np.abs(np.diff(np.signbit(audio)))) / len(audio)
        
        # Spectral features (simplified)
        fft = np.abs(np.fft.rfft(audio))
        freqs = np.fft.rfftfreq(len(audio), 1/16000)
        
        # Spectral centroid (higher for bright/music sounds)
        spectral_centroid = np.sum(freqs * fft) / (np.sum(fft) + 1e-10)
        
        # Spectral flatness (higher for noise/music, lower for speech)
        geometric_mean = np.exp(np.mean(np.log(fft + 1e-10)))
        arithmetic_mean = np.mean(fft)
        spectral_flatness = geometric_mean / (arithmetic_mean + 1e-10)
        
        # Peak frequency
        peak_freq = freqs[np.argmax(fft)]
        
        return {
            "rms_energy": float(rms_energy),
            "zero_crossing_rate": float(zero_crossings),
            "spectral_centroid": float(spectral_centroid),
            "spectral_flatness": float(spectral_flatness),
            "peak_frequency": float(peak_freq),
        }

    def _detect_music_patterns(self, features: Dict) -> float:
        """Detect music patterns from feature history."""
        if len(self._feature_history) < 5:
            return 0.0
        
        score = 0.0
        
        # High spectral flatness suggests music/noise
        if features["spectral_flatness"] > 0.1:
            score += 0.3
        
        # High zero crossing rate suggests music
        if features["zero_crossing_rate"] > 0.1:
            score += 0.2
        
        # Check for repetitive patterns in energy
        energies = [f["rms_energy"] for f in self._feature_history]
        if len(energies) >= 10:
            # Look for periodic patterns
            energy_std = np.std(energies[-10:])
            if energy_std < 0.02 and features["rms_energy"] > 0.01:
                # Consistent energy = likely music
                score += 0.3
        
        # High spectral centroid (bright sound) can indicate music
        if features["spectral_centroid"] > 2000:
            score += 0.2
        
        return min(1.0, score)

    def _classify_transcript(self, transcript: str) -> DetectionResult:
        """Classify based on transcript content."""
        text_lower = transcript.lower()
        
        # Check for IVR keywords
        ivr_matches = sum(1 for kw in IVR_KEYWORDS if kw in text_lower)
        if ivr_matches >= 2:
            return DetectionResult(
                detection_type=AudioDetectionType.IVR_PROMPT,
                confidence=min(0.9, 0.5 + ivr_matches * 0.1),
                reason=f"Detected {ivr_matches} IVR keywords",
            )
        
        # Check for human indicators
        human_matches = sum(1 for kw in HUMAN_INDICATORS if kw in text_lower)
        if human_matches >= 1:
            return DetectionResult(
                detection_type=AudioDetectionType.HUMAN_SPEECH,
                confidence=min(0.95, 0.6 + human_matches * 0.15),
                reason=f"Detected {human_matches} human speech indicators",
            )
        
        # Heuristics for speech patterns
        words = text_lower.split()
        
        # Questions often indicate live human
        if any(text_lower.startswith(q) for q in ["what", "how", "can", "may", "will", "do"]):
            return DetectionResult(
                detection_type=AudioDetectionType.HUMAN_SPEECH,
                confidence=0.7,
                reason="Detected question pattern",
            )
        
        # Short responses often indicate live human
        if len(words) <= 10 and len(transcript) < 50:
            return DetectionResult(
                detection_type=AudioDetectionType.HUMAN_SPEECH,
                confidence=0.6,
                reason="Short response pattern",
            )
        
        # Longer text without IVR keywords might still be IVR
        if len(words) > 20:
            return DetectionResult(
                detection_type=AudioDetectionType.IVR_PROMPT,
                confidence=0.5,
                reason="Long automated message pattern",
            )
        
        return DetectionResult(
            detection_type=AudioDetectionType.UNKNOWN,
            confidence=0.4,
            reason="Could not determine from transcript",
        )

    def _classify_from_features(
        self,
        features: Dict,
        speech_prob: float,
    ) -> DetectionResult:
        """Classify when no transcript is available."""
        # High speech probability suggests human
        if speech_prob > 0.8:
            return DetectionResult(
                detection_type=AudioDetectionType.HUMAN_SPEECH,
                confidence=0.5,
                reason="High speech probability (no transcript)",
                features=features,
            )
        
        return DetectionResult(
            detection_type=AudioDetectionType.UNKNOWN,
            confidence=0.3,
            reason="Insufficient data for classification",
            features=features,
        )

    def get_smoothed_detection(self) -> Tuple[AudioDetectionType, float]:
        """Get smoothed detection from history."""
        if not self._history:
            return AudioDetectionType.UNKNOWN, 0.0
        
        # Count detection types
        type_counts: Dict[AudioDetectionType, float] = {}
        for result in self._history:
            t = result.detection_type
            type_counts[t] = type_counts.get(t, 0) + result.confidence
        
        # Return highest confidence type
        best_type = max(type_counts, key=type_counts.get)
        avg_confidence = type_counts[best_type] / sum(
            1 for r in self._history if r.detection_type == best_type
        )
        
        return best_type, avg_confidence

    def reset(self) -> None:
        """Reset classifier state."""
        self._history.clear()
        self._feature_history.clear()
        self._music_pattern_count = 0
        self._last_ivr_text = None
        self._vad.reset()


# Singleton instance
_classifier: Optional[AudioClassifier] = None


def get_audio_classifier() -> AudioClassifier:
    """Get the global audio classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = AudioClassifier()
    return _classifier
