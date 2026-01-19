"""
Voice Agent Service - Main Application.

FastAPI application for the Busibox Voice AI Platform.
Provides VoIP calling, real-time transcription, IVR navigation,
and AI-powered voice conversations.
"""

import uvicorn
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

from api.calls import router as calls_router
from api.websocket import router as websocket_router, setup_call_manager_callbacks
from api.transcripts import router as transcripts_router
from api.coach import router as coach_router
from config.settings import get_settings
from services.call_manager import get_call_manager
from services.transcription import get_transcription_service

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    settings = get_settings()
    
    logger.info(
        "Starting Voice Agent Service",
        version=settings.service_version,
        port=settings.port,
    )
    
    # Initialize call manager (connects to FreeSWITCH)
    call_manager = get_call_manager()
    try:
        await call_manager.initialize()
    except Exception as e:
        logger.warning(
            "Call manager initialization failed - service will run in limited mode",
            error=str(e),
        )
    
    # Setup WebSocket callbacks
    setup_call_manager_callbacks()
    
    # Initialize transcription service
    transcription = get_transcription_service()
    try:
        await transcription.initialize()
    except Exception as e:
        logger.warning(
            "Transcription service initialization failed",
            error=str(e),
        )
    
    logger.info("Voice Agent Service started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Voice Agent Service")
    await call_manager.shutdown()
    logger.info("Voice Agent Service shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Voice Agent Service",
    description="Busibox Voice AI Platform - VoIP calling, transcription, and AI conversations",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check endpoints
@app.get("/health/live")
async def liveness():
    """Liveness probe for container orchestration."""
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness():
    """Readiness probe - checks if service is ready to accept requests."""
    call_manager = get_call_manager()
    transcription = get_transcription_service()
    
    return {
        "status": "ok",
        "freeswitch_connected": call_manager._freeswitch.is_connected,
        "transcription_ready": transcription._initialized,
    }


@app.get("/")
async def root():
    """Root endpoint with service info."""
    settings = get_settings()
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "status": "running",
    }


# Include routers
app.include_router(calls_router)
app.include_router(websocket_router)
app.include_router(transcripts_router)
app.include_router(coach_router)


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
