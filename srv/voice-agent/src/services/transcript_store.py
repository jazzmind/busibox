"""
Transcript Storage Service.

Handles persistence of call transcripts and integration
with the Data API for document storage and RAG search.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
import structlog

from config.settings import get_settings
from models.transcript import Transcript, TranscriptSegment
from models.call_state import Speaker

logger = structlog.get_logger(__name__)


class TranscriptStore:
    """
    Manages transcript persistence and document library integration.
    
    Provides:
    - Save transcripts to PostgreSQL
    - Export transcripts to Data API
    - Generate summaries using LLM
    - Search transcripts via RAG
    """

    def __init__(self):
        settings = get_settings()
        
        self._data_api_url = settings.data_api_url
        self._litellm_url = settings.litellm_base_url
        self._litellm_key = settings.litellm_api_key
        self._model = settings.default_model
        
        # In-memory storage (will be replaced with PostgreSQL)
        self._transcripts: Dict[UUID, Transcript] = {}
        
        # Database connection will be added later
        self._db_pool = None

    async def save_transcript(
        self,
        transcript: Transcript,
    ) -> Transcript:
        """
        Save a transcript to the store.
        
        Args:
            transcript: Transcript to save
            
        Returns:
            Saved transcript with any generated fields
        """
        transcript.updated_at = datetime.utcnow()
        self._transcripts[transcript.id] = transcript
        
        logger.info(
            "Saved transcript",
            transcript_id=str(transcript.id),
            segments=len(transcript.segments),
        )
        
        return transcript

    async def get_transcript(
        self,
        transcript_id: UUID,
    ) -> Optional[Transcript]:
        """Get a transcript by ID."""
        return self._transcripts.get(transcript_id)

    async def list_transcripts(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Transcript]:
        """List transcripts for a user."""
        transcripts = [
            t for t in self._transcripts.values()
            if t.user_id == user_id
        ]
        
        # Sort by date (newest first)
        transcripts.sort(key=lambda t: t.created_at, reverse=True)
        
        return transcripts[offset:offset + limit]

    async def delete_transcript(
        self,
        transcript_id: UUID,
    ) -> bool:
        """Delete a transcript."""
        if transcript_id in self._transcripts:
            del self._transcripts[transcript_id]
            return True
        return False

    async def generate_summary(
        self,
        transcript: Transcript,
    ) -> Transcript:
        """
        Generate an AI summary of the transcript.
        
        Extracts:
        - Summary text
        - Topics discussed
        - Action items
        """
        full_text = transcript.full_text
        
        if not full_text:
            return transcript
        
        prompt = f"""Analyze this phone call transcript and provide:
1. A brief summary (2-3 sentences)
2. Main topics discussed (as a list)
3. Any action items or follow-ups mentioned (as a list)

Transcript:
{full_text}

Respond in this JSON format:
{{
    "summary": "Brief summary here",
    "topics": ["topic1", "topic2"],
    "action_items": ["action1", "action2"]
}}"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._litellm_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._litellm_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": "You analyze phone call transcripts and extract summaries."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.3,
                        "response_format": {"type": "json_object"},
                    },
                )
                
                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    
                    import json
                    parsed = json.loads(content)
                    
                    transcript.summary = parsed.get("summary", "")
                    transcript.topics = parsed.get("topics", [])
                    transcript.action_items = parsed.get("action_items", [])
                    transcript.updated_at = datetime.utcnow()
                    
                    logger.info(
                        "Generated transcript summary",
                        transcript_id=str(transcript.id),
                    )
                    
        except Exception as e:
            logger.error("Failed to generate summary", error=str(e))
        
        return transcript

    async def export_to_library(
        self,
        transcript: Transcript,
        library_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        access_token: Optional[str] = None,
    ) -> Optional[str]:
        """
        Export transcript to the document library via Data API.
        
        Args:
            transcript: Transcript to export
            library_id: Target library ID (uses default if not specified)
            tags: Optional tags for the document
            access_token: OAuth token for Data API
            
        Returns:
            Document ID if successful
        """
        # Generate markdown content
        content = transcript.to_markdown()
        
        # Prepare document metadata
        filename = f"call_transcript_{transcript.call_started_at.strftime('%Y%m%d_%H%M%S')}.md"
        
        metadata = {
            "source": "voice-agent",
            "call_session_id": str(transcript.call_session_id),
            "phone_number": transcript.phone_number,
            "call_direction": transcript.call_direction,
            "duration_seconds": transcript.call_duration_seconds,
        }
        
        if transcript.target_name:
            metadata["target_name"] = transcript.target_name
        
        if transcript.summary:
            metadata["summary"] = transcript.summary
        
        if tags:
            metadata["tags"] = tags
        
        try:
            headers = {"Content-Type": "application/json"}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Create document in Data API
                response = await client.post(
                    f"{self._data_api_url}/api/v1/documents",
                    headers=headers,
                    json={
                        "filename": filename,
                        "content": content,
                        "content_type": "text/markdown",
                        "library_id": library_id,
                        "metadata": metadata,
                    },
                )
                
                if response.status_code in [200, 201]:
                    data = response.json()
                    document_id = data.get("id") or data.get("document_id")
                    
                    # Update transcript with document reference
                    transcript.saved_to_library = True
                    transcript.document_id = document_id
                    transcript.library_id = library_id
                    transcript.updated_at = datetime.utcnow()
                    
                    logger.info(
                        "Exported transcript to library",
                        transcript_id=str(transcript.id),
                        document_id=document_id,
                    )
                    
                    return document_id
                else:
                    logger.error(
                        "Failed to export transcript",
                        status=response.status_code,
                        response=response.text,
                    )
                    
        except Exception as e:
            logger.error("Failed to export transcript to library", error=str(e))
        
        return None

    async def search_transcripts(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
        access_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search transcripts using RAG.
        
        Uses the Search API to find relevant transcripts.
        
        Args:
            user_id: User ID for filtering
            query: Search query
            limit: Maximum results
            access_token: OAuth token for Search API
            
        Returns:
            List of search results
        """
        settings = get_settings()
        search_api_url = settings.data_api_url.replace("data", "search")
        
        try:
            headers = {"Content-Type": "application/json"}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{search_api_url}/api/v1/search",
                    headers=headers,
                    json={
                        "query": query,
                        "filters": {
                            "source": "voice-agent",
                        },
                        "limit": limit,
                    },
                )
                
                if response.status_code == 200:
                    return response.json().get("results", [])
                    
        except Exception as e:
            logger.error("Failed to search transcripts", error=str(e))
        
        return []


# Singleton instance
_transcript_store: Optional[TranscriptStore] = None


def get_transcript_store() -> TranscriptStore:
    """Get the global transcript store instance."""
    global _transcript_store
    if _transcript_store is None:
        _transcript_store = TranscriptStore()
    return _transcript_store
