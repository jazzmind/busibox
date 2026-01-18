"""
Signal CLI REST API Client

Handles communication with signal-cli-rest-api for sending/receiving messages.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, List, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SignalMessage:
    """Represents a Signal message."""
    timestamp: int
    source: str  # Phone number of sender
    message: str
    group_id: Optional[str] = None
    attachments: Optional[List[dict]] = None

    @property
    def sender(self) -> str:
        return self.source

    @property
    def is_group_message(self) -> bool:
        return self.group_id is not None

    @property
    def received_at(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp / 1000)


class SignalClient:
    """
    Client for signal-cli-rest-api.
    
    Provides methods for:
    - Sending messages
    - Receiving messages (polling)
    - Managing conversations
    """

    def __init__(self, base_url: str, phone_number: str):
        """
        Initialize Signal client.
        
        Args:
            base_url: Base URL of signal-cli-rest-api
            phone_number: Registered phone number for the bot
        """
        self.base_url = base_url.rstrip("/")
        self.phone_number = phone_number
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use async with context.")
        return self._client

    async def send_message(
        self,
        recipient: str,
        message: str,
        attachments: Optional[List[str]] = None,
    ) -> bool:
        """
        Send a message to a recipient.
        
        Args:
            recipient: Phone number of recipient (E.164 format)
            message: Text message to send
            attachments: Optional list of attachment URLs
            
        Returns:
            True if message was sent successfully
        """
        url = f"{self.base_url}/v2/send"
        
        payload = {
            "number": self.phone_number,
            "recipients": [recipient],
            "message": message,
        }
        
        if attachments:
            payload["base64_attachments"] = attachments

        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            
            logger.info(f"Message sent to {recipient[:6]}...")
            return True
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to send message: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    async def send_typing_indicator(self, recipient: str, stop: bool = False) -> bool:
        """
        Send typing indicator to recipient.
        
        Args:
            recipient: Phone number of recipient
            stop: If True, stops the typing indicator
            
        Returns:
            True if indicator was sent
        """
        url = f"{self.base_url}/v1/typing-indicator/{self.phone_number}"
        
        payload = {
            "recipient": recipient,
        }
        
        method = "DELETE" if stop else "PUT"
        
        try:
            response = await self.client.request(method, url, json=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.debug(f"Typing indicator failed: {e}")
            return False

    async def receive_messages(self) -> List[SignalMessage]:
        """
        Receive pending messages.
        
        Returns:
            List of received messages
        """
        url = f"{self.base_url}/v1/receive/{self.phone_number}"
        
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            
            messages = []
            data = response.json()
            
            for envelope in data:
                if "dataMessage" in envelope.get("envelope", {}):
                    data_msg = envelope["envelope"]["dataMessage"]
                    source = envelope["envelope"].get("source", "")
                    timestamp = data_msg.get("timestamp", 0)
                    message = data_msg.get("message", "")
                    group_id = data_msg.get("groupInfo", {}).get("groupId")
                    
                    if message:  # Only include messages with text
                        messages.append(SignalMessage(
                            timestamp=timestamp,
                            source=source,
                            message=message,
                            group_id=group_id,
                        ))
            
            return messages
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to receive messages: {e.response.text}")
            return []
        except Exception as e:
            logger.error(f"Error receiving messages: {e}")
            return []

    async def poll_messages(
        self,
        interval: float = 1.0,
    ) -> AsyncGenerator[SignalMessage, None]:
        """
        Continuously poll for new messages.
        
        Args:
            interval: Seconds between polls
            
        Yields:
            SignalMessage objects as they are received
        """
        logger.info(f"Starting message polling (interval={interval}s)")
        
        while True:
            try:
                messages = await self.receive_messages()
                
                for message in messages:
                    logger.info(
                        f"Received message from {message.sender[:6]}...: "
                        f"{message.message[:50]}..."
                    )
                    yield message
                    
            except Exception as e:
                logger.error(f"Polling error: {e}")
            
            await asyncio.sleep(interval)

    async def get_about(self) -> dict:
        """
        Get information about the registered account.
        
        Returns:
            Account information
        """
        url = f"{self.base_url}/v1/about"
        
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            return {}

    async def is_registered(self) -> bool:
        """
        Check if the phone number is registered.
        
        Returns:
            True if registered
        """
        about = await self.get_about()
        # Check if we have registered accounts
        return len(about.get("accounts", [])) > 0
