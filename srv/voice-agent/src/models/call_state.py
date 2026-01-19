"""
Call State Machine Models.

Defines the state machine for call lifecycle management including:
- Call states (Idle, Dialing, OnHold, HumanDetected, AIConversation, etc.)
- Call session management (parallel calls, backups)
- Transcript entries
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CallState(str, Enum):
    """
    Call state machine states.
    
    State Transitions:
    - Idle -> Dialing (start_call)
    - Dialing -> Ringing -> Connected -> Analyzing
    - Analyzing -> OnHold | InIVR | HumanDetected
    - OnHold -> Analyzing | BackupDialing
    - InIVR -> Analyzing (menu_complete)
    - HumanDetected -> AIConversation | Bridging
    - AIConversation -> Bridging | Ended
    - Bridging -> HumanActive
    - HumanActive -> Ended
    - Any -> Failed
    - Ended -> Saving -> Complete
    """

    IDLE = "idle"
    DIALING = "dialing"
    RINGING = "ringing"
    CONNECTED = "connected"
    ANALYZING = "analyzing"
    ON_HOLD = "on_hold"
    IN_IVR = "in_ivr"
    HUMAN_DETECTED = "human_detected"
    AI_CONVERSATION = "ai_conversation"
    BRIDGING = "bridging"
    HUMAN_ACTIVE = "human_active"
    BACKUP_DIALING = "backup_dialing"
    ENDED = "ended"
    SAVING = "saving"
    COMPLETE = "complete"
    FAILED = "failed"


class AudioDetectionType(str, Enum):
    """Types of audio detection results."""

    SILENCE = "silence"
    HOLD_MUSIC = "hold_music"
    IVR_PROMPT = "ivr_prompt"
    HUMAN_SPEECH = "human_speech"
    UNKNOWN = "unknown"


class Speaker(str, Enum):
    """Speaker identification for transcripts."""

    REMOTE = "remote"  # The other party (IRS agent, IVR, etc.)
    AI = "ai"  # Our AI agent
    USER = "user"  # The human user (after takeover)
    SYSTEM = "system"  # System messages


class TranscriptEntry(BaseModel):
    """A single entry in the call transcript."""

    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    speaker: Speaker
    text: str
    confidence: float = 1.0
    duration_ms: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CallLine(BaseModel):
    """
    Represents a single phone line (one SIP call).
    
    A CallSession can have multiple CallLines for parallel/backup calls.
    """

    id: UUID = Field(default_factory=uuid4)
    state: CallState = CallState.IDLE
    phone_number: str
    
    # Call timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    connected_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    
    # FreeSWITCH identifiers
    freeswitch_uuid: Optional[str] = None
    channel_name: Optional[str] = None
    
    # Detection state
    detection_type: AudioDetectionType = AudioDetectionType.UNKNOWN
    detection_confidence: float = 0.0
    hold_duration_seconds: int = 0
    
    # IVR state
    current_ivr_menu: Optional[str] = None
    ivr_path: List[str] = Field(default_factory=list)
    
    # Error tracking
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    
    # Transcript for this line
    transcript: List[TranscriptEntry] = Field(default_factory=list)

    def add_transcript(
        self,
        speaker: Speaker,
        text: str,
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TranscriptEntry:
        """Add a transcript entry to this line."""
        entry = TranscriptEntry(
            speaker=speaker,
            text=text,
            confidence=confidence,
            metadata=metadata or {},
        )
        self.transcript.append(entry)
        return entry

    @property
    def duration_seconds(self) -> int:
        """Get call duration in seconds."""
        if self.connected_at:
            end = self.ended_at or datetime.utcnow()
            return int((end - self.connected_at).total_seconds())
        return 0

    @property
    def is_active(self) -> bool:
        """Check if this line is still active."""
        return self.state not in [
            CallState.IDLE,
            CallState.ENDED,
            CallState.COMPLETE,
            CallState.FAILED,
        ]


class CallSession(BaseModel):
    """
    Represents a complete call session.
    
    A session can include multiple parallel lines (for backup/redundancy),
    and tracks the overall state of the user's call attempt.
    """

    id: UUID = Field(default_factory=uuid4)
    user_id: str  # Owner of this call session
    
    # Target information
    target_phone_number: str
    target_name: Optional[str] = None  # e.g., "IRS"
    
    # Session configuration
    max_parallel_lines: int = 2
    backup_delay_minutes: int = 5
    ai_mode_enabled: bool = True
    auto_navigate_ivr: bool = True
    
    # Call lines (parallel/backup calls)
    lines: List[CallLine] = Field(default_factory=list)
    primary_line_id: Optional[UUID] = None  # Currently active/bridged line
    
    # Session timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    
    # Session state
    state: CallState = CallState.IDLE
    
    # AI conversation context
    ai_system_prompt: Optional[str] = None
    ai_conversation_goal: Optional[str] = None
    
    # Human takeover
    human_takeover_at: Optional[datetime] = None
    ai_coaching_enabled: bool = True
    
    # Transcript storage
    save_transcript: bool = False
    transcript_saved_at: Optional[datetime] = None
    transcript_document_id: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def add_line(self, phone_number: Optional[str] = None) -> CallLine:
        """Add a new call line to this session."""
        line = CallLine(phone_number=phone_number or self.target_phone_number)
        self.lines.append(line)
        return line

    def get_line(self, line_id: UUID) -> Optional[CallLine]:
        """Get a specific line by ID."""
        for line in self.lines:
            if line.id == line_id:
                return line
        return None

    def get_primary_line(self) -> Optional[CallLine]:
        """Get the primary (active/bridged) line."""
        if self.primary_line_id:
            return self.get_line(self.primary_line_id)
        # Return first active line if no primary set
        for line in self.lines:
            if line.is_active:
                return line
        return None

    @property
    def active_lines(self) -> List[CallLine]:
        """Get all active lines."""
        return [line for line in self.lines if line.is_active]

    @property
    def full_transcript(self) -> List[TranscriptEntry]:
        """Get combined transcript from all lines, sorted by time."""
        all_entries = []
        for line in self.lines:
            all_entries.extend(line.transcript)
        return sorted(all_entries, key=lambda e: e.timestamp)

    @property
    def is_active(self) -> bool:
        """Check if session has any active lines."""
        return len(self.active_lines) > 0

    @property
    def total_duration_seconds(self) -> int:
        """Get total session duration."""
        if self.ended_at:
            return int((self.ended_at - self.created_at).total_seconds())
        return int((datetime.utcnow() - self.created_at).total_seconds())


# State transition validation
VALID_TRANSITIONS: Dict[CallState, List[CallState]] = {
    CallState.IDLE: [CallState.DIALING],
    CallState.DIALING: [CallState.RINGING, CallState.FAILED],
    CallState.RINGING: [CallState.CONNECTED, CallState.FAILED],
    CallState.CONNECTED: [CallState.ANALYZING, CallState.FAILED],
    CallState.ANALYZING: [
        CallState.ON_HOLD,
        CallState.IN_IVR,
        CallState.HUMAN_DETECTED,
        CallState.FAILED,
    ],
    CallState.ON_HOLD: [
        CallState.ANALYZING,
        CallState.BACKUP_DIALING,
        CallState.FAILED,
    ],
    CallState.IN_IVR: [CallState.ANALYZING, CallState.IN_IVR, CallState.FAILED],
    CallState.HUMAN_DETECTED: [
        CallState.AI_CONVERSATION,
        CallState.BRIDGING,
        CallState.FAILED,
    ],
    CallState.AI_CONVERSATION: [
        CallState.AI_CONVERSATION,
        CallState.BRIDGING,
        CallState.ENDED,
        CallState.FAILED,
    ],
    CallState.BRIDGING: [CallState.HUMAN_ACTIVE, CallState.FAILED],
    CallState.HUMAN_ACTIVE: [CallState.HUMAN_ACTIVE, CallState.ENDED],
    CallState.BACKUP_DIALING: [CallState.ANALYZING, CallState.FAILED],
    CallState.ENDED: [CallState.SAVING, CallState.COMPLETE],
    CallState.SAVING: [CallState.COMPLETE],
    CallState.COMPLETE: [],
    CallState.FAILED: [],
}


def is_valid_transition(from_state: CallState, to_state: CallState) -> bool:
    """Check if a state transition is valid."""
    return to_state in VALID_TRANSITIONS.get(from_state, [])
