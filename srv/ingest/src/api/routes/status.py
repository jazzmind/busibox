"""
Server-Sent Events (SSE) status endpoint.

Streams real-time processing status updates for a file.
Uses PostgreSQL LISTEN/NOTIFY for efficient updates.
"""

import json
from typing import AsyncIterator

import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from api.middleware.jwt_auth import ScopeChecker
from api.services.status import StatusService
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()

# Scope dependencies
require_ingest_read = ScopeChecker("ingest.read")


async def generate_status_stream(
    file_id: str,
    user_id: str,
) -> AsyncIterator[str]:
    """
    Generate SSE event stream for status updates.
    
    Yields:
        SSE-formatted event strings
    """
    config = Config().to_dict()
    status_service = StatusService(config)
    
    try:
        async for update in status_service.stream_status_updates(file_id, user_id):
            # Format as SSE event
            event_data = json.dumps(update)
            yield f"data: {event_data}\n\n"
            
            # Close stream if completed or failed
            if update.get("stage") in ["completed", "failed"] or update.get("error"):
                yield "event: close\ndata: {\"message\": \"Processing complete\"}\n\n"
                break
    
    except Exception as e:
        logger.error(
            "Status stream error",
            file_id=file_id,
            user_id=user_id,
            error=str(e),
            exc_info=True,
        )
        error_data = json.dumps({
            "error": "Status stream failed",
            "details": str(e),
        })
        yield f"data: {error_data}\n\n"


@router.get("/{fileId}", dependencies=[Depends(require_ingest_read)])
async def get_status_stream(fileId: str, request: Request):
    """
    Stream processing status updates via Server-Sent Events.
    
    Returns:
        SSE stream with status updates
        
    Events:
        - status: Status update with stage, progress, metrics
        - close: Stream closed (processing complete)
    """
    user_id = request.state.user_id
    
    logger.info(
        "Status stream requested",
        file_id=fileId,
        user_id=user_id,
    )
    
    return EventSourceResponse(
        generate_status_stream(fileId, user_id)
    )

