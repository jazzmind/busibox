"""Webhook routes for external service notifications (stub - to be implemented)."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/minio")
async def minio_webhook():
    """Handle MinIO file upload notifications (stub)."""
    return {"message": "MinIO webhook endpoint - to be implemented"}

