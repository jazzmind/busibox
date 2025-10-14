"""Authentication routes (stub - to be implemented)."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/login")
async def login():
    """User login - returns JWT token (stub)."""
    return {"message": "Login endpoint - to be implemented"}


@router.post("/logout")
async def logout():
    """User logout (stub)."""
    return {"message": "Logout endpoint - to be implemented"}

