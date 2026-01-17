"""
Health check endpoints for the Docs API.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/live")
async def liveness():
    """Liveness probe - service is running."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness():
    """Readiness probe - service is ready to accept requests."""
    return {"status": "ok"}
