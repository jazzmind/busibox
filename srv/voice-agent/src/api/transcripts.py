"""
Transcript Management API.

Endpoints for saving and retrieving call transcripts.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
import structlog

from models.call_state import CallSession
from models.transcript import Transcript
from services.call_manager import get_call_manager
from services.transcript_store import get_transcript_store

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/transcripts", tags=["transcripts"])


# Request/Response Models

class SaveTranscriptRequest(BaseModel):
    """Request to save a transcript to the document library."""

    library_id: Optional[str] = Field(None, description="Target library ID")
    generate_summary: bool = Field(True, description="Generate AI summary")
    tags: List[str] = Field(default_factory=list, description="Tags for the document")


class TranscriptSummary(BaseModel):
    """Summary of a saved transcript."""

    id: UUID
    session_id: UUID
    phone_number: str
    target_name: Optional[str] = None
    call_started_at: datetime
    call_duration_seconds: int
    segment_count: int
    saved_at: datetime
    document_id: Optional[str] = None


class TranscriptDetail(BaseModel):
    """Detailed transcript with full content."""

    id: UUID
    session_id: UUID
    phone_number: str
    target_name: Optional[str] = None
    call_started_at: datetime
    call_ended_at: Optional[datetime] = None
    call_duration_seconds: int
    full_text: str
    full_text_with_timestamps: str
    summary: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    action_items: List[str] = Field(default_factory=list)
    saved_at: datetime
    document_id: Optional[str] = None


# Dependency for getting current user
async def get_current_user_id() -> str:
    """Get the current user ID from JWT."""
    return "demo-user"


# Use the transcript store service
def _get_store():
    return get_transcript_store()


@router.post("/{session_id}/save", response_model=TranscriptSummary)
async def save_transcript(
    session_id: UUID,
    request: SaveTranscriptRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    Save a call transcript to the document library.
    
    This creates a document in the user's library that can be
    searched via RAG.
    """
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Build transcript from session
    transcript = Transcript(
        call_session_id=session.id,
        user_id=user_id,
        phone_number=session.target_phone_number,
        target_name=session.target_name,
        call_started_at=session.created_at,
        call_ended_at=session.ended_at,
        call_duration_seconds=session.total_duration_seconds,
    )
    
    # Add segments from all lines
    for entry in session.full_transcript:
        transcript.add_segment(
            speaker=entry.speaker,
            text=entry.text,
            start_time_ms=0,  # Will be calculated properly in Phase 6
            end_time_ms=0,
            confidence=entry.confidence,
        )
    
    store = _get_store()
    
    # Generate summary if requested
    if request.generate_summary:
        transcript = await store.generate_summary(transcript)
    
    # Save to local storage
    await store.save_transcript(transcript)
    
    # Export to document library
    document_id = await store.export_to_library(
        transcript,
        library_id=request.library_id,
        tags=request.tags,
    )
    
    # Update session
    session.save_transcript = True
    session.transcript_saved_at = datetime.utcnow()
    session.transcript_document_id = document_id or str(transcript.id)
    
    logger.info(
        "Transcript saved",
        transcript_id=str(transcript.id),
        session_id=str(session_id),
        document_id=document_id,
    )
    
    return TranscriptSummary(
        id=transcript.id,
        session_id=session.id,
        phone_number=transcript.phone_number,
        target_name=transcript.target_name,
        call_started_at=transcript.call_started_at,
        call_duration_seconds=transcript.call_duration_seconds,
        segment_count=len(transcript.segments),
        saved_at=transcript.created_at,
        document_id=transcript.document_id,
    )


@router.get("", response_model=List[TranscriptSummary])
async def list_transcripts(
    user_id: str = Depends(get_current_user_id),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List saved transcripts for the current user."""
    store = _get_store()
    transcripts = await store.list_transcripts(user_id, limit, offset)
    
    return [
        TranscriptSummary(
            id=t.id,
            session_id=t.call_session_id,
            phone_number=t.phone_number,
            target_name=t.target_name,
            call_started_at=t.call_started_at,
            call_duration_seconds=t.call_duration_seconds,
            segment_count=len(t.segments),
            saved_at=t.created_at,
            document_id=t.document_id,
        )
        for t in transcripts
    ]


@router.get("/{transcript_id}", response_model=TranscriptDetail)
async def get_transcript(
    transcript_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """Get a specific transcript with full content."""
    store = _get_store()
    transcript = await store.get_transcript(transcript_id)
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    
    if transcript.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    return TranscriptDetail(
        id=transcript.id,
        session_id=transcript.call_session_id,
        phone_number=transcript.phone_number,
        target_name=transcript.target_name,
        call_started_at=transcript.call_started_at,
        call_ended_at=transcript.call_ended_at,
        call_duration_seconds=transcript.call_duration_seconds,
        full_text=transcript.full_text,
        full_text_with_timestamps=transcript.full_text_with_timestamps,
        summary=transcript.summary,
        topics=transcript.topics,
        action_items=transcript.action_items,
        saved_at=transcript.created_at,
        document_id=transcript.document_id,
    )


@router.delete("/{transcript_id}")
async def delete_transcript(
    transcript_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """Delete a saved transcript."""
    store = _get_store()
    transcript = await store.get_transcript(transcript_id)
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    
    if transcript.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    await store.delete_transcript(transcript_id)
    
    logger.info(
        "Transcript deleted",
        transcript_id=str(transcript_id),
    )
    
    return {"success": True, "message": "Transcript deleted"}


@router.get("/{transcript_id}/markdown")
async def get_transcript_markdown(
    transcript_id: UUID,
    user_id: str = Depends(get_current_user_id),
):
    """Get transcript as Markdown document."""
    store = _get_store()
    transcript = await store.get_transcript(transcript_id)
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    
    if transcript.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    return {
        "content": transcript.to_markdown(),
        "content_type": "text/markdown",
    }


class SearchRequest(BaseModel):
    """Request to search transcripts."""

    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(10, ge=1, le=50)


class SearchResult(BaseModel):
    """Search result item."""

    document_id: str
    transcript_id: Optional[str] = None
    phone_number: Optional[str] = None
    target_name: Optional[str] = None
    snippet: str
    score: float


@router.post("/search", response_model=List[SearchResult])
async def search_transcripts(
    request: SearchRequest,
    user_id: str = Depends(get_current_user_id),
):
    """
    Search transcripts using semantic search.
    
    Searches through saved transcripts using RAG.
    """
    store = _get_store()
    results = await store.search_transcripts(
        user_id=user_id,
        query=request.query,
        limit=request.limit,
    )
    
    return [
        SearchResult(
            document_id=r.get("id", ""),
            transcript_id=r.get("metadata", {}).get("call_session_id"),
            phone_number=r.get("metadata", {}).get("phone_number"),
            target_name=r.get("metadata", {}).get("target_name"),
            snippet=r.get("content", "")[:500],
            score=r.get("score", 0.0),
        )
        for r in results
    ]
