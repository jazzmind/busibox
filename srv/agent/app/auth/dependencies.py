from fastapi import Depends, Header, HTTPException, status

from app.auth.tokens import validate_bearer
from app.schemas.auth import Principal


async def get_principal(authorization: str = Header(...)) -> Principal:
    """
    Get authenticated principal from Bearer token.
    
    Validates JWT token and returns principal with user claims.
    The token itself is stored in principal.token for downstream service calls.
    """
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid auth header")
    token = authorization.split(" ", 1)[1]
    try:
        principal = await validate_bearer(token)
        # Store the original token in the principal for downstream use
        principal.token = token
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return principal
