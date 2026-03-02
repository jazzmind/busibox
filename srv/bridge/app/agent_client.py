"""
Agent API Client

Handles communication with Busibox Agent API for AI chat functionality.
Uses Zero Trust authentication via delegation tokens and token exchange.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class StaleTokenError(Exception):
    """Raised when token exchange fails because the delegation token's
    signing key no longer matches the authz active key."""
    pass


@dataclass
class ChatResponse:
    """Represents a chat response from the Agent API."""
    message_id: str
    conversation_id: str
    content: str
    model: Optional[str] = None
    thoughts: List[Dict[str, Any]] = field(default_factory=list)


class AgentClient:
    """
    Client for Busibox Agent API.
    
    Uses Zero Trust authentication:
    - Accepts a pre-issued delegation token for the service account
    - Exchanges the delegation token for agent-api-scoped tokens
    - No client_id/client_secret required
    
    Provides methods for:
    - Chat messaging (streaming and non-streaming)
    - Conversation management
    - Authentication via token exchange
    """

    def __init__(
        self,
        base_url: str,
        auth_token_url: str,
        delegation_token: str,
        default_agent_id: Optional[str] = None,
    ):
        """
        Initialize Agent API client with Zero Trust authentication.
        
        Args:
            base_url: Base URL of Agent API
            auth_token_url: OAuth token endpoint for token exchange
            delegation_token: Pre-issued delegation token for service account
        """
        self.base_url = base_url.rstrip("/")
        self.auth_token_url = auth_token_url
        self.delegation_token = delegation_token
        self.default_agent_id = default_agent_id
        
        self._client: Optional[httpx.AsyncClient] = None
        self._token_cache: Dict[str, tuple[str, datetime]] = {}
        
        # Conversation mapping: sender -> conversation_id
        self._conversations: Dict[str, str] = {}

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=120.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use async with context.")
        return self._client

    async def _get_token(self, subject_token: Optional[str] = None) -> str:
        """
        Get a valid agent-api token via token exchange.
        
        Uses the delegation token to exchange for an agent-api-scoped token.
        This follows the Zero Trust model where:
        - The delegation token represents the service account identity
        - Token exchange produces a short-lived, audience-scoped token
        
        Returns:
            Bearer token string for agent-api
        """
        now = datetime.now(timezone.utc)
        exchange_subject_token = subject_token or self.delegation_token
        cached = self._token_cache.get(exchange_subject_token)
        if cached and now < cached[1]:
            return cached[0]
        
        # Request new token via token exchange
        try:
            response = await self.client.post(
                self.auth_token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": exchange_subject_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "audience": "agent-api",
                    "scope": "agent.execute chat.write chat.read",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            
            data = response.json()
            access_token = data["access_token"]
            
            # Parse expiry (default to 1 hour if not provided)
            expires_in = data.get("expires_in", 3600)
            expires_at = now + timedelta(seconds=expires_in - 60)
            self._token_cache[exchange_subject_token] = (access_token, expires_at)
            
            logger.info("Obtained agent-api token via token exchange")
            return access_token
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Token exchange failed: {e.response.status_code} - {e.response.text}")
            if e.response.status_code == 401:
                try:
                    detail = e.response.json().get("detail", "")
                except Exception:
                    detail = ""
                if detail == "invalid_subject_token_key":
                    raise StaleTokenError(
                        "Delegation token signed with rotated key"
                    ) from e
            raise
        except Exception as e:
            logger.error(f"Failed to get auth token: {e}")
            raise

    async def _get_headers(self, subject_token: Optional[str] = None) -> Dict[str, str]:
        """Get headers with authentication."""
        token = await self._get_token(subject_token)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # Note: X-User-ID is no longer needed - the token carries the user identity
        }

    def get_conversation_id(self, sender: str) -> Optional[str]:
        """Get conversation ID for a sender."""
        return self._conversations.get(sender)

    def set_conversation_id(self, sender: str, conversation_id: str):
        """Set conversation ID for a sender."""
        self._conversations[sender] = conversation_id

    async def chat_message(
        self,
        message: str,
        sender: str,
        enable_web_search: bool = True,
        enable_doc_search: bool = False,
        model: str = "auto",
        agent_id: Optional[str] = None,
        delegation_token_override: Optional[str] = None,
    ) -> ChatResponse:
        """
        Send a chat message and get a response (non-streaming).
        
        Args:
            message: User message
            sender: Sender identifier (phone number)
            enable_web_search: Enable web search
            enable_doc_search: Enable document search
            model: Model selection
            
        Returns:
            ChatResponse with AI response
        """
        url = f"{self.base_url}/chat/message"
        headers = await self._get_headers(delegation_token_override)
        
        payload = {
            "message": message,
            "model": model,
            "enable_web_search": enable_web_search,
            "enable_doc_search": enable_doc_search,
        }
        effective_agent_id = agent_id or self.default_agent_id
        if effective_agent_id:
            payload["agent_id"] = effective_agent_id
        
        # Include conversation ID if we have one for this sender
        conversation_id = self.get_conversation_id(sender)
        if conversation_id:
            payload["conversation_id"] = conversation_id
        
        try:
            response = await self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            
            # Update conversation ID
            self.set_conversation_id(sender, data["conversation_id"])
            
            return ChatResponse(
                message_id=data["message_id"],
                conversation_id=data["conversation_id"],
                content=data["content"],
                model=data.get("model"),
            )
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Chat request failed: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Chat request error: {e}")
            raise

    async def chat_message_stream(
        self,
        message: str,
        sender: str,
        enable_web_search: bool = True,
        enable_doc_search: bool = False,
        model: str = "auto",
        agent_id: Optional[str] = None,
        delegation_token_override: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        channel: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Send a chat message and stream the response.
        
        Args:
            message: User message
            sender: Sender identifier
            enable_web_search: Enable web search
            enable_doc_search: Enable document search
            model: Model selection
            channel: Bridge channel name (e.g. "telegram") — passed as metadata
                     so the agent can tailor event filtering for bridge clients.
            
        Yields:
            Event dictionaries from the stream
        """
        url = f"{self.base_url}/chat/message/stream/agentic"
        headers = await self._get_headers(delegation_token_override)
        
        payload: Dict[str, Any] = {
            "message": message,
            "model": model,
            "enable_web_search": enable_web_search,
            "enable_doc_search": enable_doc_search,
        }
        if attachments:
            payload["attachments"] = attachments
        effective_agent_id = agent_id or self.default_agent_id
        if effective_agent_id:
            payload["agent_id"] = effective_agent_id

        if channel:
            payload["metadata"] = {"bridge_channels": [channel]}
        
        # Include conversation ID if we have one
        conversation_id = self.get_conversation_id(sender)
        if conversation_id:
            payload["conversation_id"] = conversation_id
        
        try:
            async with self.client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                event_type = ""
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    
                    # Parse SSE format
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            data["_event_type"] = event_type
                            if not isinstance(data.get("data"), dict):
                                data["data"] = {}
                            
                            # Update conversation ID from events
                            if "conversation_id" in data:
                                self.set_conversation_id(sender, data["conversation_id"])
                            
                            yield data
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse SSE data: {line}")
                            
        except httpx.HTTPStatusError as e:
            logger.error(f"Streaming chat failed: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Streaming chat error: {e}")
            raise

    async def health_check(self) -> bool:
        """
        Check Agent API health.
        
        Returns:
            True if API is healthy
        """
        try:
            response = await self.client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Agent API health check failed: {e}")
            return False
