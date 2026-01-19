"""
WebSocket API for Real-time Updates.

Provides real-time streaming of:
- Call state changes
- Live transcription
- Audio levels
- Human detection events
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, Set
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import structlog

from models.call_state import CallState, Speaker
from services.call_manager import get_call_manager

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manages WebSocket connections for call sessions."""

    def __init__(self):
        # session_id -> set of websocket connections
        self._connections: Dict[UUID, Set[WebSocket]] = {}
        self._user_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(
        self,
        websocket: WebSocket,
        session_id: UUID,
        user_id: str,
    ) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        
        if session_id not in self._connections:
            self._connections[session_id] = set()
        self._connections[session_id].add(websocket)
        
        if user_id not in self._user_connections:
            self._user_connections[user_id] = set()
        self._user_connections[user_id].add(websocket)
        
        logger.info(
            "WebSocket connected",
            session_id=str(session_id),
            user_id=user_id,
        )

    def disconnect(
        self,
        websocket: WebSocket,
        session_id: UUID,
        user_id: str,
    ) -> None:
        """Remove a WebSocket connection."""
        if session_id in self._connections:
            self._connections[session_id].discard(websocket)
            if not self._connections[session_id]:
                del self._connections[session_id]
        
        if user_id in self._user_connections:
            self._user_connections[user_id].discard(websocket)
            if not self._user_connections[user_id]:
                del self._user_connections[user_id]
        
        logger.info(
            "WebSocket disconnected",
            session_id=str(session_id),
        )

    async def send_to_session(
        self,
        session_id: UUID,
        message: dict,
    ) -> None:
        """Send a message to all connections for a session."""
        if session_id not in self._connections:
            return
        
        disconnected = set()
        for websocket in self._connections[session_id]:
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.add(websocket)
        
        # Clean up disconnected sockets
        self._connections[session_id] -= disconnected

    async def broadcast_to_user(
        self,
        user_id: str,
        message: dict,
    ) -> None:
        """Broadcast a message to all connections for a user."""
        if user_id not in self._user_connections:
            return
        
        disconnected = set()
        for websocket in self._user_connections[user_id]:
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.add(websocket)
        
        self._user_connections[user_id] -= disconnected


# Global connection manager
manager = ConnectionManager()


def setup_call_manager_callbacks():
    """Configure CallManager to send events via WebSocket."""
    call_manager = get_call_manager()
    
    async def on_state_change(line_id: UUID, old_state: CallState, new_state: CallState):
        # Find session for this line
        session_id = call_manager._line_to_session.get(line_id)
        if session_id:
            await manager.send_to_session(session_id, {
                "type": "state_change",
                "line_id": str(line_id),
                "old_state": old_state.value,
                "new_state": new_state.value,
                "timestamp": datetime.utcnow().isoformat(),
            })
    
    async def on_transcript(line_id: UUID, speaker: Speaker, text: str, confidence: float):
        session_id = call_manager._line_to_session.get(line_id)
        if session_id:
            await manager.send_to_session(session_id, {
                "type": "transcript",
                "line_id": str(line_id),
                "speaker": speaker.value,
                "text": text,
                "confidence": confidence,
                "timestamp": datetime.utcnow().isoformat(),
            })
    
    async def on_human_detected(session_id: UUID, line_id: UUID, confidence: float):
        await manager.send_to_session(session_id, {
            "type": "human_detected",
            "line_id": str(line_id),
            "confidence": confidence,
            "timestamp": datetime.utcnow().isoformat(),
        })
    
    call_manager.set_callbacks(
        on_state_change=on_state_change,
        on_transcript=on_transcript,
        on_human_detected=on_human_detected,
    )


@router.websocket("/api/v1/calls/{session_id}/stream")
async def call_stream(
    websocket: WebSocket,
    session_id: UUID,
    user_id: str = Query("demo-user"),  # TODO: Get from JWT
):
    """
    WebSocket endpoint for real-time call updates.
    
    Events sent to client:
    - state_change: Call state transitions
    - transcript: New transcription segments
    - human_detected: Human agent detected
    - audio_level: Audio level meters
    - coach_suggestion: AI coaching suggestions (Phase 5)
    
    Commands from client:
    - subscribe: Subscribe to session (automatic on connect)
    - takeover: Human takes over call
    - dtmf: Send DTMF tones
    - coach_request: Request AI coaching
    """
    call_manager = get_call_manager()
    session = call_manager.get_session(session_id)
    
    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return
    
    if session.user_id != user_id:
        await websocket.close(code=4003, reason="Not authorized")
        return
    
    await manager.connect(websocket, session_id, user_id)
    
    try:
        # Send initial state
        await websocket.send_json({
            "type": "connected",
            "session_id": str(session_id),
            "state": session.state.value,
        })
        
        # Start audio level monitor
        level_task = asyncio.create_task(
            _send_audio_levels(websocket, session_id)
        )
        
        # Handle incoming messages
        while True:
            try:
                data = await websocket.receive_json()
                await _handle_client_message(websocket, session_id, data)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON",
                })
    
    except WebSocketDisconnect:
        pass
    finally:
        level_task.cancel()
        manager.disconnect(websocket, session_id, user_id)


async def _send_audio_levels(websocket: WebSocket, session_id: UUID):
    """Send periodic audio level updates."""
    call_manager = get_call_manager()
    
    while True:
        try:
            await asyncio.sleep(0.1)  # 10 updates per second
            
            session = call_manager.get_session(session_id)
            if not session or not session.is_active:
                break
            
            # Get audio levels from processor
            # TODO: Implement actual audio level metering
            await websocket.send_json({
                "type": "audio_level",
                "remote": 0.5,  # Placeholder
                "local": 0.0,
            })
            
        except Exception:
            break


async def _handle_client_message(
    websocket: WebSocket,
    session_id: UUID,
    data: dict,
):
    """Handle incoming WebSocket messages from client."""
    call_manager = get_call_manager()
    msg_type = data.get("type")
    
    if msg_type == "takeover":
        try:
            await call_manager.takeover(session_id)
            await websocket.send_json({
                "type": "takeover_success",
            })
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
            })
    
    elif msg_type == "dtmf":
        digits = data.get("digits", "")
        if digits:
            try:
                await call_manager.send_dtmf(session_id, digits)
                await websocket.send_json({
                    "type": "dtmf_sent",
                    "digits": digits,
                })
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e),
                })
    
    elif msg_type == "coach_request":
        # Phase 5: AI coaching
        context = data.get("context", "")
        await websocket.send_json({
            "type": "coach_suggestion",
            "text": "AI coaching will be available in Phase 5",
        })
    
    elif msg_type == "ping":
        await websocket.send_json({"type": "pong"})
