"""
AI Coach API.

Endpoints for real-time AI coaching during calls.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
import structlog

from agents.coach_agent import CoachAgent, CoachContext, CoachSuggestion
from services.call_manager import get_call_manager

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/coach", tags=["coach"])


# Request/Response Models

class StartCoachingRequest(BaseModel):
    """Request to start AI coaching for a call."""

    session_id: UUID
    call_purpose: Optional[str] = None
    user_goals: List[str] = Field(default_factory=list)
    important_info: dict = Field(default_factory=dict)


class CoachRequestInput(BaseModel):
    """Request for a coaching suggestion."""

    question: Optional[str] = Field(None, description="Specific question to ask the coach")


class CoachSuggestionResponse(BaseModel):
    """Response with a coaching suggestion."""

    text: str
    category: str
    confidence: float
    timestamp: datetime


class CoachSessionResponse(BaseModel):
    """Response with coaching session info."""

    session_id: UUID
    is_active: bool
    suggestions_count: int


# In-memory coaching sessions (per call session)
_coaching_sessions: dict[UUID, CoachAgent] = {}


# Dependency for getting current user
async def get_current_user_id() -> str:
    """Get the current user ID from JWT."""
    return "demo-user"


@router.post("/{session_id}/start", response_model=CoachSessionResponse)
async def start_coaching(
    session_id: UUID,
    request: StartCoachingRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    Start AI coaching for a call session.
    
    The coach will monitor the conversation and provide suggestions.
    """
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Call session not found")
    
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Create coach with context
    context = CoachContext(
        call_purpose=request.call_purpose,
        user_goals=request.user_goals,
        important_info=request.important_info,
    )
    
    coach = CoachAgent(context=context)
    coach.start()
    
    _coaching_sessions[session_id] = coach
    
    logger.info(
        "Started coaching session",
        session_id=str(session_id),
    )
    
    return CoachSessionResponse(
        session_id=session_id,
        is_active=True,
        suggestions_count=0,
    )


@router.post("/{session_id}/stop", response_model=CoachSessionResponse)
async def stop_coaching(
    session_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """Stop AI coaching for a call session."""
    coach = _coaching_sessions.get(session_id)
    
    if not coach:
        raise HTTPException(status_code=404, detail="Coaching session not found")
    
    suggestions_count = len(coach.get_suggestions_history())
    coach.stop()
    
    del _coaching_sessions[session_id]
    
    return CoachSessionResponse(
        session_id=session_id,
        is_active=False,
        suggestions_count=suggestions_count,
    )


@router.post("/{session_id}/suggest", response_model=Optional[CoachSuggestionResponse])
async def get_suggestion(
    session_id: UUID,
    request: CoachRequestInput,
    user_id: str = Depends(get_current_user_id),
):
    """
    Get a coaching suggestion on demand.
    
    Optionally pass a specific question for targeted advice.
    """
    coach = _coaching_sessions.get(session_id)
    
    if not coach:
        raise HTTPException(status_code=404, detail="Coaching session not found")
    
    suggestion = await coach.get_suggestion(request.question)
    
    if not suggestion:
        return None
    
    return CoachSuggestionResponse(
        text=suggestion.text,
        category=suggestion.category,
        confidence=suggestion.confidence,
        timestamp=suggestion.timestamp,
    )


@router.get("/{session_id}/history", response_model=List[CoachSuggestionResponse])
async def get_suggestions_history(
    session_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """Get all coaching suggestions given in this session."""
    coach = _coaching_sessions.get(session_id)
    
    if not coach:
        raise HTTPException(status_code=404, detail="Coaching session not found")
    
    suggestions = coach.get_suggestions_history()
    
    return [
        CoachSuggestionResponse(
            text=s.text,
            category=s.category,
            confidence=s.confidence,
            timestamp=s.timestamp,
        )
        for s in suggestions
    ]


@router.put("/{session_id}/context")
async def update_coach_context(
    session_id: UUID,
    call_purpose: Optional[str] = None,
    user_goals: Optional[List[str]] = None,
    important_info: Optional[dict] = None,
    user_id: str = Depends(get_current_user_id),
):
    """Update the coaching context."""
    coach = _coaching_sessions.get(session_id)
    
    if not coach:
        raise HTTPException(status_code=404, detail="Coaching session not found")
    
    coach.update_context(
        call_purpose=call_purpose,
        user_goals=user_goals,
        important_info=important_info,
    )
    
    return {"success": True, "message": "Context updated"}


# Internal function for processing transcripts
async def process_transcript_for_coaching(
    session_id: UUID,
    speaker: str,
    text: str,
) -> Optional[CoachSuggestion]:
    """
    Process a transcript update for coaching.
    
    Called internally when new transcript is available.
    """
    coach = _coaching_sessions.get(session_id)
    
    if not coach:
        return None
    
    return await coach.process_transcript(speaker, text)
