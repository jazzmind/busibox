"""
Call Management API.

REST endpoints for managing call sessions.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from models.call_state import CallSession, CallLine, CallState, Speaker
from services.call_manager import get_call_manager, CallManagerError

router = APIRouter(prefix="/api/v1/calls", tags=["calls"])


# Request/Response Models

class StartCallRequest(BaseModel):
    """Request to start a new call session."""

    phone_number: str = Field(..., description="Target phone number (E.164 format)")
    target_name: Optional[str] = Field(None, description="Friendly name (e.g., 'IRS')")
    ai_mode: bool = Field(True, description="Enable AI conversation mode")
    auto_navigate_ivr: bool = Field(True, description="Auto-navigate IVR menus")
    max_parallel_lines: int = Field(2, ge=1, le=5, description="Max parallel calls")


class CallLineResponse(BaseModel):
    """Response model for a call line."""

    id: UUID
    state: str
    phone_number: str
    created_at: datetime
    connected_at: Optional[datetime] = None
    duration_seconds: int = 0
    hold_duration_seconds: int = 0
    detection_type: str = "unknown"
    error_message: Optional[str] = None

    @classmethod
    def from_line(cls, line: CallLine) -> "CallLineResponse":
        return cls(
            id=line.id,
            state=line.state.value,
            phone_number=line.phone_number,
            created_at=line.created_at,
            connected_at=line.connected_at,
            duration_seconds=line.duration_seconds,
            hold_duration_seconds=line.hold_duration_seconds,
            detection_type=line.detection_type.value,
            error_message=line.error_message,
        )


class CallSessionResponse(BaseModel):
    """Response model for a call session."""

    id: UUID
    user_id: str
    target_phone_number: str
    target_name: Optional[str] = None
    state: str
    created_at: datetime
    ended_at: Optional[datetime] = None
    total_duration_seconds: int = 0
    ai_mode_enabled: bool
    human_takeover_at: Optional[datetime] = None
    lines: List[CallLineResponse]
    primary_line_id: Optional[UUID] = None

    @classmethod
    def from_session(cls, session: CallSession) -> "CallSessionResponse":
        return cls(
            id=session.id,
            user_id=session.user_id,
            target_phone_number=session.target_phone_number,
            target_name=session.target_name,
            state=session.state.value,
            created_at=session.created_at,
            ended_at=session.ended_at,
            total_duration_seconds=session.total_duration_seconds,
            ai_mode_enabled=session.ai_mode_enabled,
            human_takeover_at=session.human_takeover_at,
            lines=[CallLineResponse.from_line(line) for line in session.lines],
            primary_line_id=session.primary_line_id,
        )


class SendDTMFRequest(BaseModel):
    """Request to send DTMF tones."""

    digits: str = Field(..., pattern=r"^[0-9*#]+$", description="DTMF digits")
    line_id: Optional[UUID] = Field(None, description="Specific line (or primary)")


class TranscriptEntryResponse(BaseModel):
    """Response model for a transcript entry."""

    id: UUID
    timestamp: datetime
    speaker: str
    text: str
    confidence: float


class TranscriptResponse(BaseModel):
    """Response model for call transcript."""

    session_id: UUID
    entries: List[TranscriptEntryResponse]
    total_entries: int


# Dependency for getting current user
# TODO: Integrate with AuthZ in Phase 5
async def get_current_user_id() -> str:
    """Get the current user ID from JWT."""
    # Placeholder - will be replaced with actual JWT auth
    return "demo-user"


# Endpoints

@router.post("", response_model=CallSessionResponse)
async def start_call(
    request: StartCallRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    Start a new call session.
    
    Creates a session and initiates the primary call.
    """
    call_manager = get_call_manager()
    
    try:
        # Create session
        session = await call_manager.create_session(
            user_id=user_id,
            phone_number=request.phone_number,
            target_name=request.target_name,
            ai_mode=request.ai_mode,
            auto_navigate_ivr=request.auto_navigate_ivr,
            max_parallel_lines=request.max_parallel_lines,
        )
        
        # Start the call
        await call_manager.start_call(session.id)
        
        return CallSessionResponse.from_session(session)
        
    except CallManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start call: {e}")


@router.get("", response_model=List[CallSessionResponse])
async def list_calls(
    user_id: str = Depends(get_current_user_id),
    active_only: bool = Query(True, description="Only show active calls"),
):
    """List call sessions for the current user."""
    call_manager = get_call_manager()
    
    if active_only:
        sessions = call_manager.get_active_sessions(user_id)
    else:
        sessions = [
            s for s in call_manager._sessions.values()
            if s.user_id == user_id
        ]
    
    return [CallSessionResponse.from_session(s) for s in sessions]


@router.get("/{session_id}", response_model=CallSessionResponse)
async def get_call(
    session_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """Get details for a specific call session."""
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    return CallSessionResponse.from_session(session)


@router.post("/{session_id}/dial", response_model=CallLineResponse)
async def add_line(
    session_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """Add a parallel/backup line to an existing session."""
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    try:
        line = await call_manager.add_backup_line(session_id)
        return CallLineResponse.from_line(line)
    except CallManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{session_id}/dtmf")
async def send_dtmf(
    session_id: UUID,
    request: SendDTMFRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Send DTMF tones to a call."""
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    try:
        success = await call_manager.send_dtmf(
            session_id,
            request.digits,
            request.line_id,
        )
        return {"success": success, "digits": request.digits}
    except CallManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{session_id}/takeover")
async def takeover_call(
    session_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """Human takes over the call from AI."""
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    try:
        await call_manager.takeover(session_id)
        return {"success": True, "message": "Takeover initiated"}
    except CallManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{session_id}/hangup")
async def hangup_line(
    session_id: UUID,
    line_id: Optional[UUID] = Query(None, description="Specific line to hangup"),
    user_id: str = Depends(get_current_user_id),
):
    """Hangup a specific line or all lines in a session."""
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    try:
        if line_id:
            await call_manager.hangup_line(session_id, line_id)
        else:
            await call_manager.hangup_session(session_id)
        return {"success": True}
    except CallManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{session_id}")
async def end_session(
    session_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """End a call session completely."""
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    try:
        await call_manager.hangup_session(session_id)
        return {"success": True, "message": "Session ended"}
    except CallManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{session_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(
    session_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """Get the transcript for a call session."""
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    entries = [
        TranscriptEntryResponse(
            id=e.id,
            timestamp=e.timestamp,
            speaker=e.speaker.value,
            text=e.text,
            confidence=e.confidence,
        )
        for e in session.full_transcript
    ]
    
    return TranscriptResponse(
        session_id=session_id,
        entries=entries,
        total_entries=len(entries),
    )
