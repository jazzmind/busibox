"""
Token Exchange Service for service-to-service authentication.

When the search service needs to call other services (like ingest for embeddings),
it uses OAuth2 Token Exchange (RFC 8693) to get a token with:
- The correct audience for the target service
- The original user's identity (sub) and roles preserved
- Proper RLS enforcement in the downstream service
"""

import httpx
import structlog
from typing import Optional, Dict
from cachetools import TTLCache
import time

logger = structlog.get_logger()

# Cache tokens to avoid repeated token exchanges (keyed by user_id + audience)
# TTL is slightly less than token lifetime to ensure we don't use expired tokens
_token_cache: TTLCache = TTLCache(maxsize=1000, ttl=840)  # 14 minutes (tokens are 15 min)


class TokenExchangeService:
    """
    Service for exchanging tokens to call other internal services.
    
    This implements OAuth2 Token Exchange (RFC 8693) to get tokens with
    the correct audience while preserving user identity and roles.
    """
    
    def __init__(self, config: Dict):
        """Initialize token exchange service."""
        self.authz_token_url = config.get("authz_token_url", "http://10.96.200.210:8010/oauth/token")
        self.client_id = config.get("api_service_client_id", "api-service")
        self.client_secret = config.get("api_service_client_secret", "")
        self.timeout = 10.0
    
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
        # Check cache first
        cache_key = f"{user_id}:{target_audience}"
        cached_token = _token_cache.get(cache_key)
        if cached_token:
            logger.debug(
                "Using cached token for service call",
                user_id=user_id,
                target_audience=target_audience,
            )
            return cached_token
        
        if not self.client_secret:
            logger.error(
                "API service client secret not configured",
                client_id=self.client_id,
            )
            return None
        
        try:
            logger.info(
                "Exchanging token for service call",
                user_id=user_id,
                target_audience=target_audience,
            )
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.authz_token_url,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "requested_subject": user_id,
                        "audience": target_audience,
                        "scope": scope,
                    },
                )
                
                if response.status_code != 200:
                    logger.error(
                        "Token exchange failed",
                        status_code=response.status_code,
                        response=response.text[:200],
                        user_id=user_id,
                        target_audience=target_audience,
                    )
                    return None
                
                data = response.json()
                access_token = data.get("access_token")
                
                if access_token:
                    # Cache the token
                    _token_cache[cache_key] = access_token
                    logger.debug(
                        "Token exchange successful",
                        user_id=user_id,
                        target_audience=target_audience,
                        expires_in=data.get("expires_in"),
                    )
                
                return access_token
        
        except Exception as e:
            logger.error(
                "Token exchange error",
                error=str(e),
                user_id=user_id,
                target_audience=target_audience,
                exc_info=True,
            )
            return None

