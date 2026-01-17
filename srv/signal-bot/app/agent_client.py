"""
Agent API Client

Handles communication with Busibox Agent API for AI chat functionality.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


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
    
    Provides methods for:
    - Chat messaging (streaming and non-streaming)
    - Conversation management
    - Authentication
    """

    def __init__(
        self,
        base_url: str,
        auth_token_url: str,
        client_id: str,
        client_secret: str,
        service_user_id: str,
    ):
        """
        Initialize Agent API client.
        
        Args:
            base_url: Base URL of Agent API
            auth_token_url: OAuth token endpoint
            client_id: OAuth client ID
            client_secret: OAuth client secret
            service_user_id: Service user ID for API calls
        """
        self.base_url = base_url.rstrip("/")
        self.auth_token_url = auth_token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.service_user_id = service_user_id
        
        self._client: Optional[httpx.AsyncClient] = None
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        
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

    async def _get_token(self) -> str:
        """
        Get a valid OAuth token, refreshing if needed.
        
        Returns:
            Bearer token string
        """
        now = datetime.now(timezone.utc)
        
        # Return cached token if still valid
        if self._token and self._token_expires and now < self._token_expires:
            return self._token
        
        # Request new token
        try:
            response = await self.client.post(
                self.auth_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "agent.execute chat.write chat.read",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            
            data = response.json()
            self._token = data["access_token"]
            
            # Parse expiry (default to 1 hour if not provided)
            expires_in = data.get("expires_in", 3600)
            self._token_expires = now.replace(
                tzinfo=timezone.utc
            ) + __import__("datetime").timedelta(seconds=expires_in - 60)
            
            logger.info("Obtained new Agent API token")
            return self._token
            
        except Exception as e:
            logger.error(f"Failed to get auth token: {e}")
            raise

    async def _get_headers(self) -> Dict[str, str]:
        """Get headers with authentication."""
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-User-ID": self.service_user_id,
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
        headers = await self._get_headers()
        
        payload = {
            "message": message,
            "model": model,
            "enable_web_search": enable_web_search,
            "enable_doc_search": enable_doc_search,
        }
        
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
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Send a chat message and stream the response.
        
        Args:
            message: User message
            sender: Sender identifier
            enable_web_search: Enable web search
            enable_doc_search: Enable document search
            model: Model selection
            
        Yields:
            Event dictionaries from the stream
        """
        url = f"{self.base_url}/chat/message/stream/agentic"
        headers = await self._get_headers()
        
        payload = {
            "message": message,
            "model": model,
            "enable_web_search": enable_web_search,
            "enable_doc_search": enable_doc_search,
        }
        
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
