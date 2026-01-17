"""
Token Exchange Service for service-to-service authentication.

When the search service needs to call other services (like ingest for embeddings),
it uses OAuth2 Token Exchange (RFC 8693) to get a token with:
- The correct audience for the target service
- The original user's identity (sub) and roles preserved
- Proper RLS enforcement in the downstream service

This module wraps busibox_common.auth.TokenExchangeClient for backward compatibility.
"""

from typing import Optional, Dict

from busibox_common.auth import TokenExchangeClient


class TokenExchangeService:
    """
    Service for exchanging tokens to call other internal services.
    
    This implements OAuth2 Token Exchange (RFC 8693) to get tokens with
    the correct audience while preserving user identity and roles.
    
    Wraps busibox_common.auth.TokenExchangeClient.
    """
    
    def __init__(self, config: Dict):
        """Initialize token exchange service."""
        self._client = TokenExchangeClient(
            token_url=config.get("authz_token_url", "http://10.96.200.210:8010/oauth/token"),
            client_id=config.get("api_service_client_id", "api-service"),
            client_secret=config.get("api_service_client_secret", ""),
            timeout=10.0,
        )
    
    async def get_token_for_service(
        self,
        user_id: str,
        target_audience: str,
        scope: str = "read write",
    ) -> Optional[str]:
        """
        Get a token for calling another service on behalf of a user.
        
        Args:
            user_id: The user ID to impersonate (from the incoming request's JWT)
            target_audience: The audience of the target service (e.g., "ingest-api")
            scope: Requested scopes (optional)
        
        Returns:
            Access token string, or None if exchange fails
        """
        return await self._client.get_token_for_service(
            user_id=user_id,
            target_audience=target_audience,
            scope=scope,
        )

