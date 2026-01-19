"""
Speech Synthesis Service.

Provides text-to-speech using Piper TTS for local, fast synthesis.
"""

import asyncio
import io
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

import numpy as np
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class SynthesisResult:
    """Result of speech synthesis."""

    audio: np.ndarray
    sample_rate: int
    duration_seconds: float
    text: str


class PiperTTS:
    """
    Text-to-Speech using Piper.
    
    Piper is a fast, local neural TTS system that produces
    high-quality speech with low latency.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        speaker: Optional[int] = None,
        sample_rate: int = 16000,
    ):
        settings = get_settings()
        
        self._model = model or settings.piper_model
        self._speaker = speaker if speaker is not None else settings.piper_speaker
        self._sample_rate = sample_rate
        
        self._piper_path: Optional[str] = None
        self._model_path: Optional[str] = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize Piper TTS."""
        if self._initialized:
            return True
        
        try:
            logger.info("Initializing Piper TTS", model=self._model)
            
            # Check for piper binary
            self._piper_path = self._find_piper()
            if not self._piper_path:
                logger.warning("Piper binary not found, TTS will be unavailable")
                return False
            
            # Check for model file
            self._model_path = self._find_model()
            if not self._model_path:
                logger.warning(f"Piper model {self._model} not found")
                return False
            
            self._initialized = True
            logger.info("Piper TTS initialized", model_path=self._model_path)
            return True
            
        except Exception as e:
            logger.error("Failed to initialize Piper TTS", error=str(e))
            return False

    def _find_piper(self) -> Optional[str]:
        """Find the Piper binary."""
        # Check common locations
        locations = [
            "/usr/bin/piper",
            "/usr/local/bin/piper",
            os.path.expanduser("~/.local/bin/piper"),
            "/opt/piper/piper",
        ]
        
        for path in locations:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        
        # Check if piper is in PATH
        try:
            result = subprocess.run(
                ["which", "piper"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        
        return None

    def _find_model(self) -> Optional[str]:
        """Find the Piper model file."""
        # Check common model locations
        model_dirs = [
            "/opt/voice-agent/models",
            "/opt/piper/models",
            os.path.expanduser("~/.local/share/piper/models"),
            "/usr/share/piper/models",
        ]
        
        model_name = self._model
        
        for model_dir in model_dirs:
            # Try exact name
            model_path = os.path.join(model_dir, f"{model_name}.onnx")
            if os.path.isfile(model_path):
                return model_path
            
            # Try with directory structure
            model_path = os.path.join(model_dir, model_name, f"{model_name}.onnx")
            if os.path.isfile(model_path):
                return model_path
        
        return None

    async def synthesize(self, text: str) -> Optional[SynthesisResult]:
        """
        Synthesize speech from text.
        
        Args:
            text: Text to synthesize
            
        Returns:
            SynthesisResult with audio data
        """
        if not self._initialized:
            if not await self.initialize():
                return None
        
        if not self._piper_path or not self._model_path:
            logger.error("Piper not properly initialized")
            return None
        
        try:
            # Create temp file for output
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                output_path = f.name
            
            # Run Piper
            cmd = [
                self._piper_path,
                "--model", self._model_path,
                "--output_file", output_path,
            ]
            
            if self._speaker is not None:
                cmd.extend(["--speaker", str(self._speaker)])
            
            # Run async
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            stdout, stderr = await process.communicate(input=text.encode())
            
            if process.returncode != 0:
                logger.error("Piper synthesis failed", stderr=stderr.decode())
                return None
            
            # Read output audio
            import soundfile as sf
            audio, sample_rate = sf.read(output_path)
            
            # Clean up
            os.unlink(output_path)
            
            # Convert to float32
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            
            # Ensure mono
            if len(audio.shape) > 1:
                audio = audio.mean(axis=1)
            
            # Resample if needed
            if sample_rate != self._sample_rate:
                audio = self._resample(audio, sample_rate, self._sample_rate)
                sample_rate = self._sample_rate
            
            duration = len(audio) / sample_rate
            
            return SynthesisResult(
                audio=audio,
                sample_rate=sample_rate,
                duration_seconds=duration,
                text=text,
            )
            
        except Exception as e:
            logger.error("Speech synthesis failed", error=str(e))
            return None

    async def synthesize_streaming(
        self,
        text: str,
        chunk_duration_ms: int = 100,
    ) -> AsyncIterator[np.ndarray]:
        """
        Synthesize speech and stream audio chunks.
        
        Args:
            text: Text to synthesize
            chunk_duration_ms: Size of audio chunks to yield
            
        Yields:
            Audio chunks as numpy arrays
        """
        result = await self.synthesize(text)
        if not result:
            return
        
        # Calculate chunk size
        chunk_samples = int(result.sample_rate * chunk_duration_ms / 1000)
        
        # Yield chunks
        for i in range(0, len(result.audio), chunk_samples):
            yield result.audio[i:i + chunk_samples]

    def _resample(
        self,
        audio: np.ndarray,
        orig_sr: int,
        target_sr: int,
    ) -> np.ndarray:
        """Resample audio to target sample rate."""
        if orig_sr == target_sr:
            return audio
        
        try:
            from scipy import signal
            
            # Calculate number of samples
            num_samples = int(len(audio) * target_sr / orig_sr)
            resampled = signal.resample(audio, num_samples)
            return resampled.astype(np.float32)
            
        except ImportError:
            # Fallback: simple linear interpolation
            ratio = target_sr / orig_sr
            old_indices = np.arange(len(audio))
            new_length = int(len(audio) * ratio)
            new_indices = np.linspace(0, len(audio) - 1, new_length)
            resampled = np.interp(new_indices, old_indices, audio)
            return resampled.astype(np.float32)

    @property
    def is_available(self) -> bool:
        """Check if TTS is available."""
        return self._initialized and self._piper_path is not None


class FallbackTTS:
    """
    Fallback TTS using espeak or festival.
    
    Used when Piper is not available.
    """

    def __init__(self, sample_rate: int = 16000):
        self._sample_rate = sample_rate
        self._engine: Optional[str] = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize fallback TTS."""
        # Check for espeak
        try:
            result = subprocess.run(
                ["which", "espeak-ng"],
                capture_output=True,
            )
            if result.returncode == 0:
                self._engine = "espeak-ng"
                self._initialized = True
                return True
        except Exception:
            pass
        
        # Check for espeak
        try:
            result = subprocess.run(
                ["which", "espeak"],
                capture_output=True,
            )
            if result.returncode == 0:
                self._engine = "espeak"
                self._initialized = True
                return True
        except Exception:
            pass
        
        logger.warning("No fallback TTS engine found")
        return False

    async def synthesize(self, text: str) -> Optional[SynthesisResult]:
        """Synthesize speech using fallback engine."""
        if not self._initialized:
            if not await self.initialize():
                return None
        
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                output_path = f.name
            
            cmd = [
                self._engine,
                "-w", output_path,
                text,
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            await process.communicate()
            
            if process.returncode != 0:
                return None
            
            import soundfile as sf
            audio, sample_rate = sf.read(output_path)
            os.unlink(output_path)
            
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            
            if len(audio.shape) > 1:
                audio = audio.mean(axis=1)
            
            return SynthesisResult(
                audio=audio,
                sample_rate=sample_rate,
                duration_seconds=len(audio) / sample_rate,
                text=text,
            )
            
        except Exception as e:
            logger.error("Fallback TTS failed", error=str(e))
            return None


class SpeechSynthesisService:
    """
    Main speech synthesis service.
    
    Provides unified interface to TTS, with automatic
    fallback to alternative engines.
    """

    def __init__(self):
        self._piper = PiperTTS()
        self._fallback = FallbackTTS()
        self._initialized = False
        self._use_fallback = False

    async def initialize(self) -> bool:
        """Initialize the synthesis service."""
        if self._initialized:
            return True
        
        # Try Piper first
        if await self._piper.initialize():
            self._initialized = True
            self._use_fallback = False
            logger.info("Using Piper TTS")
            return True
        
        # Try fallback
        if await self._fallback.initialize():
            self._initialized = True
            self._use_fallback = True
            logger.info("Using fallback TTS")
            return True
        
        logger.error("No TTS engine available")
        return False

    async def synthesize(self, text: str) -> Optional[SynthesisResult]:
        """Synthesize speech from text."""
        if not self._initialized:
            await self.initialize()
        
        if self._use_fallback:
            return await self._fallback.synthesize(text)
        return await self._piper.synthesize(text)

    async def synthesize_streaming(
        self,
        text: str,
        chunk_duration_ms: int = 100,
    ) -> AsyncIterator[np.ndarray]:
        """Synthesize and stream audio chunks."""
        if self._use_fallback:
            result = await self._fallback.synthesize(text)
            if result:
                chunk_samples = int(result.sample_rate * chunk_duration_ms / 1000)
                for i in range(0, len(result.audio), chunk_samples):
                    yield result.audio[i:i + chunk_samples]
        else:
            async for chunk in self._piper.synthesize_streaming(text, chunk_duration_ms):
                yield chunk

    @property
    def is_available(self) -> bool:
        """Check if TTS is available."""
        return self._initialized


# Singleton instance
_tts_service: Optional[SpeechSynthesisService] = None


def get_tts_service() -> SpeechSynthesisService:
    """Get the global TTS service instance."""
    global _tts_service
    if _tts_service is None:
        _tts_service = SpeechSynthesisService()
    return _tts_service
