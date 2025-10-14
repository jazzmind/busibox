"""Semantic search routes (stub - to be implemented)."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/")
async def search():
    """Perform semantic search (stub)."""
    return {"message": "Search endpoint - to be implemented"}

