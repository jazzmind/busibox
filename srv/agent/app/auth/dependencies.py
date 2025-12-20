from fastapi import Depends, Header, HTTPException, status

from app.auth.tokens import validate_bearer
from app.schemas.auth import Principal


async def get_principal(authorization: str | None = Header(None)) -> Principal:
    """
    Get authenticated principal from Bearer token.
    
    Validates JWT token and returns principal with user claims.
    The token itself is stored in principal.token for downstream service calls.
    
    Raises 401 if:
    - No authorization header provided
    - Authorization header doesn't start with "Bearer "
    - Token validation fails
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1]
    try:
        principal = await validate_bearer(token)
        # Store the original token in the principal for downstream use
        principal.token = token
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return principal
