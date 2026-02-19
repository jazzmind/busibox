"""
Telegram Bot API client.
"""

import logging
from dataclasses import dataclass
from typing import AsyncGenerator, List, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TelegramMessage:
    """Represents a Telegram inbound message."""

    update_id: int
    chat_id: str
    sender_id: str
    text: str
    audio_url: Optional[str] = None
    is_group_message: bool = False


class TelegramClient:
    """Simple async Telegram Bot API client using long-polling."""

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self._client: Optional[httpx.AsyncClient] = None
        self._offset: Optional[int] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use async with context.")
        return self._client

    async def poll_messages(
        self,
        interval: float = 1.0,
        timeout: int = 25,
    ) -> AsyncGenerator[TelegramMessage, None]:
        """Yield parsed Telegram messages via getUpdates long-polling."""
        while True:
            params = {
                "timeout": timeout,
                "allowed_updates": ["message"],
            }
            if self._offset is not None:
                params["offset"] = self._offset

            try:
                response = await self.client.get(f"{self.base_url}/getUpdates", params=params)
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok", False):
                    await self._sleep(interval)
                    continue

                for update in payload.get("result", []):
                    update_id = int(update.get("update_id", 0))
                    self._offset = update_id + 1

                    msg = update.get("message") or {}
                    text = (msg.get("text") or "").strip()
                    audio_url: Optional[str] = None
                    voice = msg.get("voice") or msg.get("audio")
                    if voice and voice.get("file_id"):
                        audio_url = await self._resolve_file_url(str(voice["file_id"]))
                    if not text and not audio_url:
                        continue

                    chat = msg.get("chat") or {}
                    user = msg.get("from") or {}
                    chat_id = str(chat.get("id", ""))
                    sender_id = str(user.get("id", chat_id))
                    chat_type = str(chat.get("type", "")).lower()

                    yield TelegramMessage(
                        update_id=update_id,
                        chat_id=chat_id,
                        sender_id=sender_id,
                        text=text,
                        audio_url=audio_url,
                        is_group_message=chat_type in {"group", "supergroup"},
                    )
            except Exception as e:
                logger.error("Telegram poll failed: %s", e)
                await self._sleep(interval)

    async def send_message(self, chat_id: str, text: str, parse_mode: Optional[str] = None) -> None:
        """Send a message to a Telegram chat."""
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        response = await self.client.post(
            f"{self.base_url}/sendMessage",
            json=payload,
        )
        response.raise_for_status()

    async def send_typing_indicator(self, chat_id: str) -> None:
        """Show typing indicator in Telegram."""
        try:
            response = await self.client.post(
                f"{self.base_url}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )
            response.raise_for_status()
        except Exception:
            # Best-effort only.
            pass

    async def _resolve_file_url(self, file_id: str) -> Optional[str]:
        try:
            response = await self.client.get(
                f"{self.base_url}/getFile",
                params={"file_id": file_id},
            )
            response.raise_for_status()
            payload = response.json()
            file_path = ((payload.get("result") or {}).get("file_path") or "").strip()
            if not file_path:
                return None
            return f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        except Exception:
            return None

    async def _sleep(self, seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)
