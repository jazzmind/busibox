"""
Call Manager Service.

Orchestrates call sessions, manages parallel calls, and coordinates
between FreeSWITCH, audio processing, and transcription services.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional
from uuid import UUID, uuid4

import structlog

from config.settings import get_settings
from models.call_state import (
    AudioDetectionType,
    CallLine,
    CallSession,
    CallState,
    Speaker,
    TranscriptEntry,
    is_valid_transition,
)
from services.audio_processor import get_audio_processor
from services.freeswitch_client import FreeSwitchClient, get_freeswitch_client
from services.transcription import get_transcription_service

logger = structlog.get_logger(__name__)


class CallManagerError(Exception):
    """Call manager operation error."""

    pass


class CallManager:
    """
    Manages call sessions and coordinates all call-related services.
    
    Responsibilities:
    - Create and manage call sessions
    - Coordinate FreeSWITCH calls
    - Route audio to transcription
    - Handle state transitions
    - Manage backup calls
    - Notify UI of events
    """

    def __init__(self):
        self._sessions: Dict[UUID, CallSession] = {}
        self._line_to_session: Dict[UUID, UUID] = {}  # line_id -> session_id
        self._fs_uuid_to_line: Dict[str, UUID] = {}  # FreeSWITCH UUID -> line_id
        
        self._freeswitch: FreeSwitchClient = get_freeswitch_client()
        self._audio_processor = get_audio_processor()
        self._transcription = get_transcription_service()
        
        # Event callbacks
        self._on_state_change: Optional[Callable] = None
        self._on_transcript: Optional[Callable] = None
        self._on_human_detected: Optional[Callable] = None
        
        # Background tasks
        self._monitor_tasks: Dict[UUID, asyncio.Task] = {}

    async def initialize(self) -> bool:
        """Initialize the call manager and connect to FreeSWITCH."""
        logger.info("Initializing Call Manager")
        
        # Connect to FreeSWITCH
        if not await self._freeswitch.connect():
            logger.error("Failed to connect to FreeSWITCH")
            return False
        
        # Initialize transcription
        if not await self._transcription.initialize():
            logger.warning("Transcription service initialization failed")
        
        # Register FreeSWITCH event handlers
        self._freeswitch.on_event("CHANNEL_ANSWER", self._on_channel_answer)
        self._freeswitch.on_event("CHANNEL_HANGUP", self._on_channel_hangup)
        self._freeswitch.on_event("DTMF", self._on_dtmf)
        
        logger.info("Call Manager initialized")
        return True

    async def shutdown(self) -> None:
        """Shutdown the call manager."""
        logger.info("Shutting down Call Manager")
        
        # Cancel all monitor tasks
        for task in self._monitor_tasks.values():
            task.cancel()
        
        # Hangup all active calls
        for session in self._sessions.values():
            for line in session.active_lines:
                if line.freeswitch_uuid:
                    await self._freeswitch.hangup(line.freeswitch_uuid)
        
        # Disconnect from FreeSWITCH
        await self._freeswitch.disconnect()
        
        logger.info("Call Manager shutdown complete")

    def set_callbacks(
        self,
        on_state_change: Optional[Callable] = None,
        on_transcript: Optional[Callable] = None,
        on_human_detected: Optional[Callable] = None,
    ) -> None:
        """Set event callbacks for UI notifications."""
        self._on_state_change = on_state_change
        self._on_transcript = on_transcript
        self._on_human_detected = on_human_detected

    async def create_session(
        self,
        user_id: str,
        phone_number: str,
        target_name: Optional[str] = None,
        ai_mode: bool = True,
        auto_navigate_ivr: bool = True,
        max_parallel_lines: int = 2,
    ) -> CallSession:
        """
        Create a new call session.
        
        Args:
            user_id: Owner of the session
            phone_number: Target phone number
            target_name: Friendly name (e.g., "IRS")
            ai_mode: Enable AI conversation mode
            auto_navigate_ivr: Auto-navigate IVR menus
            max_parallel_lines: Max parallel calls for backup
            
        Returns:
            New CallSession
        """
        settings = get_settings()
        
        session = CallSession(
            user_id=user_id,
            target_phone_number=phone_number,
            target_name=target_name,
            ai_mode_enabled=ai_mode,
            auto_navigate_ivr=auto_navigate_ivr,
            max_parallel_lines=min(max_parallel_lines, settings.max_parallel_calls),
        )
        
        self._sessions[session.id] = session
        
        logger.info(
            "Created call session",
            session_id=str(session.id),
            user_id=user_id,
            phone_number=phone_number,
        )
        
        return session

    async def start_call(self, session_id: UUID) -> CallLine:
        """
        Start the primary call for a session.
        
        Args:
            session_id: Session ID
            
        Returns:
            The new CallLine
        """
        session = self._get_session(session_id)
        
        # Create primary line
        line = session.add_line()
        session.primary_line_id = line.id
        self._line_to_session[line.id] = session.id
        
        # Transition states
        await self._transition_line_state(line, CallState.DIALING)
        await self._transition_session_state(session, CallState.DIALING)
        
        # Originate call via FreeSWITCH
        try:
            fs_uuid = await self._freeswitch.originate(
                phone_number=line.phone_number,
            )
            line.freeswitch_uuid = fs_uuid
            self._fs_uuid_to_line[fs_uuid] = line.id
            
            logger.info(
                "Call originated",
                session_id=str(session_id),
                line_id=str(line.id),
                fs_uuid=fs_uuid,
            )
            
            # Start monitoring this line
            self._monitor_tasks[line.id] = asyncio.create_task(
                self._monitor_line(session, line)
            )
            
        except Exception as e:
            logger.error(
                "Failed to originate call",
                session_id=str(session_id),
                error=str(e),
            )
            line.error_message = str(e)
            await self._transition_line_state(line, CallState.FAILED)
            raise CallManagerError(f"Failed to start call: {e}")
        
        # Start transcription session
        self._transcription.start_session(line.id)
        
        return line

    async def add_backup_line(self, session_id: UUID) -> CallLine:
        """Add a backup call line to an existing session."""
        session = self._get_session(session_id)
        
        if len(session.active_lines) >= session.max_parallel_lines:
            raise CallManagerError(
                f"Maximum parallel lines ({session.max_parallel_lines}) reached"
            )
        
        line = session.add_line()
        self._line_to_session[line.id] = session.id
        
        await self._transition_line_state(line, CallState.DIALING)
        
        try:
            fs_uuid = await self._freeswitch.originate(
                phone_number=line.phone_number,
            )
            line.freeswitch_uuid = fs_uuid
            self._fs_uuid_to_line[fs_uuid] = line.id
            
            self._monitor_tasks[line.id] = asyncio.create_task(
                self._monitor_line(session, line)
            )
            
            self._transcription.start_session(line.id)
            
        except Exception as e:
            line.error_message = str(e)
            await self._transition_line_state(line, CallState.FAILED)
            raise CallManagerError(f"Failed to start backup call: {e}")
        
        return line

    async def hangup_line(
        self,
        session_id: UUID,
        line_id: UUID,
    ) -> None:
        """Hangup a specific call line."""
        session = self._get_session(session_id)
        line = session.get_line(line_id)
        
        if not line:
            raise CallManagerError(f"Line {line_id} not found")
        
        if line.freeswitch_uuid:
            await self._freeswitch.hangup(line.freeswitch_uuid)
        
        await self._transition_line_state(line, CallState.ENDED)
        
        # Cancel monitoring task
        if line_id in self._monitor_tasks:
            self._monitor_tasks[line_id].cancel()
            del self._monitor_tasks[line_id]
        
        # End transcription session
        self._transcription.end_session(line_id)
        
        # Check if session should end
        if not session.active_lines:
            await self._transition_session_state(session, CallState.ENDED)

    async def hangup_session(self, session_id: UUID) -> None:
        """Hangup all lines in a session."""
        session = self._get_session(session_id)
        
        for line in session.active_lines:
            await self.hangup_line(session_id, line.id)
        
        session.ended_at = datetime.utcnow()

    async def send_dtmf(
        self,
        session_id: UUID,
        digits: str,
        line_id: Optional[UUID] = None,
    ) -> bool:
        """
        Send DTMF tones.
        
        Args:
            session_id: Session ID
            digits: DTMF digits to send
            line_id: Specific line (or primary if None)
        """
        session = self._get_session(session_id)
        
        if line_id:
            line = session.get_line(line_id)
        else:
            line = session.get_primary_line()
        
        if not line or not line.freeswitch_uuid:
            raise CallManagerError("No active line for DTMF")
        
        success = await self._freeswitch.send_dtmf(
            line.freeswitch_uuid,
            digits,
        )
        
        if success:
            # Log DTMF in transcript
            line.add_transcript(
                speaker=Speaker.SYSTEM,
                text=f"[DTMF: {digits}]",
            )
        
        return success

    async def takeover(self, session_id: UUID) -> None:
        """
        Human takes over the call.
        
        Transitions from AI conversation to human active mode.
        """
        session = self._get_session(session_id)
        line = session.get_primary_line()
        
        if not line:
            raise CallManagerError("No active line for takeover")
        
        session.human_takeover_at = datetime.utcnow()
        
        await self._transition_line_state(line, CallState.BRIDGING)
        
        # TODO: Bridge to WebRTC in Phase 5
        # For now, just transition state
        
        await self._transition_line_state(line, CallState.HUMAN_ACTIVE)
        await self._transition_session_state(session, CallState.HUMAN_ACTIVE)
        
        line.add_transcript(
            speaker=Speaker.SYSTEM,
            text="[Human takeover - transcription continues]",
        )

    def get_session(self, session_id: UUID) -> Optional[CallSession]:
        """Get a session by ID (public method)."""
        return self._sessions.get(session_id)

    def get_active_sessions(self, user_id: Optional[str] = None) -> List[CallSession]:
        """Get all active sessions, optionally filtered by user."""
        sessions = [s for s in self._sessions.values() if s.is_active]
        if user_id:
            sessions = [s for s in sessions if s.user_id == user_id]
        return sessions

    def _get_session(self, session_id: UUID) -> CallSession:
        """Get a session by ID (raises if not found)."""
        session = self._sessions.get(session_id)
        if not session:
            raise CallManagerError(f"Session {session_id} not found")
        return session

    async def _transition_line_state(
        self,
        line: CallLine,
        new_state: CallState,
    ) -> None:
        """Transition a line to a new state."""
        old_state = line.state
        
        if not is_valid_transition(old_state, new_state):
            logger.warning(
                "Invalid state transition",
                line_id=str(line.id),
                from_state=old_state.value,
                to_state=new_state.value,
            )
            return
        
        line.state = new_state
        
        # Update timing
        if new_state == CallState.CONNECTED:
            line.connected_at = datetime.utcnow()
        elif new_state in [CallState.ENDED, CallState.FAILED]:
            line.ended_at = datetime.utcnow()
        
        logger.info(
            "Line state changed",
            line_id=str(line.id),
            from_state=old_state.value,
            to_state=new_state.value,
        )
        
        # Notify callback
        if self._on_state_change:
            await self._on_state_change(
                line_id=line.id,
                old_state=old_state,
                new_state=new_state,
            )

    async def _transition_session_state(
        self,
        session: CallSession,
        new_state: CallState,
    ) -> None:
        """Transition a session to a new state."""
        old_state = session.state
        session.state = new_state
        
        if new_state == CallState.ENDED:
            session.ended_at = datetime.utcnow()
        
        logger.info(
            "Session state changed",
            session_id=str(session.id),
            from_state=old_state.value,
            to_state=new_state.value,
        )

    async def _monitor_line(
        self,
        session: CallSession,
        line: CallLine,
    ) -> None:
        """
        Background task to monitor a call line.
        
        Handles:
        - Audio classification
        - Hold detection
        - Human detection
        - Backup call triggering
        """
        settings = get_settings()
        hold_start_time: Optional[datetime] = None
        
        try:
            while line.is_active:
                await asyncio.sleep(1.0)  # Check every second
                
                # Skip if not connected yet
                if line.state in [CallState.IDLE, CallState.DIALING, CallState.RINGING]:
                    continue
                
                # Classify audio
                detection = await self._audio_processor.classify_audio(line.id)
                line.detection_type = detection
                
                # Handle detection results
                if detection == AudioDetectionType.HOLD_MUSIC:
                    if hold_start_time is None:
                        hold_start_time = datetime.utcnow()
                        await self._transition_line_state(line, CallState.ON_HOLD)
                    
                    # Calculate hold duration
                    hold_duration = (datetime.utcnow() - hold_start_time).total_seconds()
                    line.hold_duration_seconds = int(hold_duration)
                    
                    # Check if we should start backup call
                    if (
                        hold_duration >= settings.backup_call_delay_minutes * 60
                        and len(session.active_lines) < session.max_parallel_lines
                    ):
                        logger.info(
                            "Starting backup call after hold timeout",
                            session_id=str(session.id),
                            hold_duration=hold_duration,
                        )
                        asyncio.create_task(self.add_backup_line(session.id))
                
                elif detection == AudioDetectionType.HUMAN_SPEECH:
                    hold_start_time = None
                    await self._transition_line_state(line, CallState.HUMAN_DETECTED)
                    
                    # Notify about human detection
                    if self._on_human_detected:
                        await self._on_human_detected(
                            session_id=session.id,
                            line_id=line.id,
                            confidence=line.detection_confidence,
                        )
                    
                    # Make this the primary line if not already
                    if session.primary_line_id != line.id:
                        session.primary_line_id = line.id
                        
                        # Hangup other lines
                        for other_line in session.active_lines:
                            if other_line.id != line.id:
                                asyncio.create_task(
                                    self.hangup_line(session.id, other_line.id)
                                )
                    
                    # Transition to AI conversation or wait for human takeover
                    if session.ai_mode_enabled:
                        await self._transition_line_state(
                            line, CallState.AI_CONVERSATION
                        )
                
                elif detection == AudioDetectionType.IVR_PROMPT:
                    await self._transition_line_state(line, CallState.IN_IVR)
                    # IVR navigation will be handled in Phase 3
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(
                "Error in line monitor",
                line_id=str(line.id),
                error=str(e),
            )

    async def _on_channel_answer(self, event_data: str) -> None:
        """Handle FreeSWITCH CHANNEL_ANSWER event."""
        # Parse UUID from event
        fs_uuid = None
        for line in event_data.split("\n"):
            if "Unique-ID:" in line:
                fs_uuid = line.split(":", 1)[1].strip()
                break
        
        if fs_uuid and fs_uuid in self._fs_uuid_to_line:
            line_id = self._fs_uuid_to_line[fs_uuid]
            session_id = self._line_to_session.get(line_id)
            
            if session_id:
                session = self._sessions.get(session_id)
                line = session.get_line(line_id) if session else None
                
                if line:
                    await self._transition_line_state(line, CallState.CONNECTED)
                    await self._transition_line_state(line, CallState.ANALYZING)

    async def _on_channel_hangup(self, event_data: str) -> None:
        """Handle FreeSWITCH CHANNEL_HANGUP event."""
        fs_uuid = None
        for line in event_data.split("\n"):
            if "Unique-ID:" in line:
                fs_uuid = line.split(":", 1)[1].strip()
                break
        
        if fs_uuid and fs_uuid in self._fs_uuid_to_line:
            line_id = self._fs_uuid_to_line[fs_uuid]
            session_id = self._line_to_session.get(line_id)
            
            if session_id:
                session = self._sessions.get(session_id)
                line = session.get_line(line_id) if session else None
                
                if line and line.is_active:
                    await self._transition_line_state(line, CallState.ENDED)

    async def _on_dtmf(self, event_data: str) -> None:
        """Handle FreeSWITCH DTMF event (incoming DTMF)."""
        # Parse DTMF digit from event
        pass  # Not needed for outbound calls


# Singleton instance
_call_manager: Optional[CallManager] = None


def get_call_manager() -> CallManager:
    """Get the global call manager instance."""
    global _call_manager
    if _call_manager is None:
        _call_manager = CallManager()
    return _call_manager
