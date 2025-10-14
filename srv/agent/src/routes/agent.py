"""AI agent routes (stub - to be implemented)."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/invoke")
async def invoke_agent():
    """Invoke AI agent with RAG (stub)."""
    return {"message": "Agent invoke endpoint - to be implemented"}

