"""
Zero Trust Token Exchange for Agent Service.

Re-exports the shared Zero Trust token exchange from busibox_common.
This module exists for backward compatibility and local configuration.
"""

from typing import Optional

from app.config.settings import get_settings
from busibox_common.auth import exchange_token_zero_trust as _exchange_token_zero_trust


async def exchange_token_zero_trust(
    subject_token: str,
    target_audience: str,
    user_id: str,
    scopes: Optional[str] = ""
) -> Optional[str]:
    """
    Exchange a user's token for a downstream service token (Zero Trust).
    
    This uses RFC 8693 token exchange with the user's JWT as subject_token.
    No client credentials are used - the user's token cryptographically proves identity.
    
    Args:
        subject_token: The user's current JWT token
        target_audience: Target service audience (e.g., "ingest-api")
        user_id: User ID for logging purposes
        scopes: Requested scopes (optional, scopes come from RBAC)
        
    Returns:
        Access token string, or None if exchange fails
    """
    settings = get_settings()
    
    return await _exchange_token_zero_trust(
        subject_token=subject_token,
        target_audience=target_audience,
        user_id=user_id,
        scopes=scopes or "",
        authz_url=str(settings.auth_token_url),
    )
