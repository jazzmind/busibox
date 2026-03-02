"""
Bridge Main Application.

Runs FastAPI + optional channel workers:
- Signal polling bot
- Telegram polling bot
- Discord polling bot
- WhatsApp webhook ingress (served through FastAPI endpoint)
"""

import asyncio
import httpx
import logging
import re
import secrets
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .agent_client import AgentClient, StaleTokenError
from .channel_identity import ChannelIdentityResolver
from .config import Settings, get_settings
from .discord_client import DiscordClient
from .email_client import EmailClient
from .email_inbound_client import EmailInboundClient, InboundEmailMessage
from .signal_client import SignalClient, SignalMessage
from .telegram_client import TelegramClient, TelegramMessage
from .telegram_formatter import markdown_to_telegram_html
from .whatsapp_client import WhatsAppClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple sender-scoped rate limiter."""

    def __init__(self, max_messages: int, window_seconds: int):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self._messages: Dict[str, List[datetime]] = defaultdict(list)

    def is_allowed(self, sender: str) -> bool:
        now = datetime.now()
        cutoff = now - timedelta(seconds=self.window_seconds)
        self._messages[sender] = [ts for ts in self._messages[sender] if ts > cutoff]
        if len(self._messages[sender]) >= self.max_messages:
            return False
        self._messages[sender].append(now)
        return True


class MessageProcessor:
    """Shared channel-agnostic message processing logic."""

    def __init__(self, settings: Settings, identity: ChannelIdentityResolver):
        self.settings = settings
        self.identity = identity
        self.rate_limiter = RateLimiter(
            max_messages=settings.rate_limit_messages,
            window_seconds=settings.rate_limit_window,
        )
        self._binding_cache: Dict[str, Dict[str, str]] = {}
        self.authz_base_url = str(settings.authz_base_url).rstrip("/")
        self._sender_queues: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._sender_workers: Dict[str, asyncio.Task[None]] = {}
        self._sender_cancels: Dict[str, asyncio.Event] = {}

    def _should_interrupt(self, text: str) -> bool:
        lowered = text.lower()
        interrupt_keywords = (
            "cancel",
            "stop",
            "never mind",
            "nevermind",
            "ignore that",
            "actually",
            "wait",
        )
        return any(keyword in lowered for keyword in interrupt_keywords)

    async def _resolve_sender_binding(self, channel: str, external_sender: str) -> Dict[str, str] | None:
        cache_key = f"{channel}:{external_sender}".strip().lower()
        cached = self._binding_cache.get(cache_key)
        if cached:
            return cached

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.authz_base_url}/internal/channel-bindings/lookup",
                    params={
                        "channel_type": channel,
                        "external_id": external_sender,
                    },
                )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            binding = (resp.json() or {}).get("binding")
            if isinstance(binding, dict):
                normalized = {
                    "user_id": str(binding.get("user_id") or ""),
                    "delegation_token": str(binding.get("delegation_token") or ""),
                }
                if normalized["user_id"] and normalized["delegation_token"]:
                    self._binding_cache[cache_key] = normalized
                    return normalized
        except Exception as exc:
            logger.warning("Failed to resolve channel binding: %s", exc)
        return None

    async def _refresh_binding_token(self, channel: str, external_sender: str) -> Dict[str, str] | None:
        """Ask authz to re-sign a stale delegation token for this binding."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.authz_base_url}/internal/channel-bindings/refresh-token",
                    json={
                        "channel_type": channel,
                        "external_id": external_sender,
                    },
                )
            if resp.status_code != 200:
                logger.warning(
                    "Binding token refresh failed: %s %s",
                    resp.status_code, resp.text,
                )
                return None
            binding = (resp.json() or {}).get("binding")
            if isinstance(binding, dict):
                normalized = {
                    "user_id": str(binding.get("user_id") or ""),
                    "delegation_token": str(binding.get("delegation_token") or ""),
                }
                if normalized["user_id"] and normalized["delegation_token"]:
                    cache_key = f"{channel}:{external_sender}".strip().lower()
                    self._binding_cache[cache_key] = normalized
                    return normalized
        except Exception as exc:
            logger.warning("Failed to refresh binding token: %s", exc)
        return None

    async def _verify_link_code(
        self,
        *,
        channel: str,
        external_sender: str,
        code: str,
    ) -> Dict[str, str] | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.put(
                    f"{self.authz_base_url}/internal/channel-bindings/verify",
                    json={
                        "channel_type": channel,
                        "external_id": external_sender,
                        "link_code": code,
                    },
                )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            binding = (resp.json() or {}).get("binding")
            if isinstance(binding, dict):
                normalized = {
                    "user_id": str(binding.get("user_id") or ""),
                    "delegation_token": str(binding.get("delegation_token") or ""),
                }
                if normalized["user_id"] and normalized["delegation_token"]:
                    cache_key = f"{channel}:{external_sender}".strip().lower()
                    self._binding_cache[cache_key] = normalized
                    return normalized
        except Exception as exc:
            logger.warning("Failed to verify link code: %s", exc)
        return None

    async def process(
        self,
        *,
        channel: str,
        external_sender: str,
        text: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        send_message: Callable[[str], Awaitable[Any]],
        send_typing_start: Callable[[], Awaitable[None]] | None,
        send_typing_stop: Callable[[], Awaitable[None]] | None,
        agent_client: AgentClient,
        edit_message: Callable[[int, str], Awaitable[None]] | None = None,
        delete_message: Callable[[int], Awaitable[None]] | None = None,
        format_content: Callable[[str], str] | None = None,
    ) -> None:
        text = text.strip()
        if not text:
            return

        if text.lower().startswith("/link "):
            code = text.split(maxsplit=1)[1].strip()
            if not code:
                await send_message("Please provide a link code, e.g. /link ABC123")
                return
            verified = await self._verify_link_code(
                channel=channel,
                external_sender=external_sender,
                code=code,
            )
            if verified:
                await send_message("Your channel is now linked to your Busibox account.")
            else:
                await send_message("Link code is invalid or expired. Please generate a new link code in Account settings.")
            return

        sender_key = self.identity.resolve(channel, external_sender)
        delegation_token_override = None
        binding = await self._resolve_sender_binding(channel, external_sender)
        if binding:
            sender_key = binding["user_id"]
            delegation_token_override = binding["delegation_token"]
        elif not self.settings.delegation_token:
            # When no global service delegation token exists, channels can still
            # run to support self-service /link flows. Non-linked chat requests
            # must be blocked until the user links their channel.
            await send_message(
                "This channel is not linked yet. Generate a link code in your Account settings and send /link <code>."
            )
            return

        if not self.rate_limiter.is_allowed(sender_key):
            await send_message("⏳ You're sending messages too quickly. Please wait a moment.")
            return

        if text.lower() == "/help":
            await send_message(
                "AI Assistant Bot\n\nCommands:\n- /help\n- /new\n\nAsk anything and I will respond with Busibox agents."
            )
            return

        if text.lower() == "/new":
            agent_client._conversations.pop(sender_key, None)
            await send_message("Started a new conversation. How can I help?")
            return

        request: Dict[str, Any] = {
            "channel": channel,
            "external_sender": external_sender,
            "sender_key": sender_key,
            "text": text,
            "attachments": attachments or [],
            "send_message": send_message,
            "send_typing_start": send_typing_start,
            "send_typing_stop": send_typing_stop,
            "agent_client": agent_client,
            "delegation_token_override": delegation_token_override,
            "edit_message": edit_message,
            "delete_message": delete_message,
            "format_content": format_content,
        }

        # Email flow expects synchronous completion in-process to capture chunks.
        if channel == "email":
            await self._run_request(request)
            return

        queue = self._sender_queues[sender_key]
        worker = self._sender_workers.get(sender_key)
        active_cancel = self._sender_cancels.get(sender_key)

        if worker and not worker.done():
            if active_cancel and self._should_interrupt(text):
                active_cancel.set()
                queue.clear()
                queue.append(request)
                await send_message("Got it - switching to your latest request.")
            else:
                queue.append(request)
                await send_message("Got it - I'll respond once I finish the current request.")
            return

        queue.append(request)
        self._sender_workers[sender_key] = asyncio.create_task(
            self._sender_worker(sender_key)
        )

    async def _sender_worker(self, sender_key: str) -> None:
        """Process queued requests for a sender in order."""
        try:
            while True:
                queue = self._sender_queues.get(sender_key)
                if not queue:
                    return
                request = queue.pop(0)
                cancel_event = asyncio.Event()
                self._sender_cancels[sender_key] = cancel_event
                request["cancel_event"] = cancel_event
                await self._run_request(request)
                self._sender_cancels.pop(sender_key, None)
        finally:
            self._sender_workers.pop(sender_key, None)
            self._sender_cancels.pop(sender_key, None)
            if not self._sender_queues.get(sender_key):
                self._sender_queues.pop(sender_key, None)

    async def _run_request(self, request: Dict[str, Any]) -> None:
        channel = request["channel"]
        send_message = request["send_message"]
        send_typing_start = request["send_typing_start"]
        send_typing_stop = request["send_typing_stop"]
        cancel_event = request.get("cancel_event")

        if send_typing_start:
            await send_typing_start()
        try:
            await self._process_streaming(
                text=request["text"],
                attachments=request.get("attachments") or [],
                sender=request["sender_key"],
                agent_client=request["agent_client"],
                delegation_token_override=request["delegation_token_override"],
                send_message=send_message,
                cancel_event=cancel_event,
                channel=channel,
                edit_message=request.get("edit_message"),
                delete_message=request.get("delete_message"),
                format_content=request.get("format_content"),
            )
        except StaleTokenError:
            external_sender = request.get("external_sender", "")
            if not request["delegation_token_override"] or not external_sender:
                logger.error(
                    "Stale global delegation token — regenerate via "
                    "'make install SERVICE=bridge' after updating vault"
                )
                await send_message("Sorry, the service token has expired. An admin needs to regenerate it.")
                return
            logger.warning(
                "Stale delegation token for %s:%s — attempting refresh",
                channel, external_sender,
            )
            refreshed = await self._refresh_binding_token(channel, external_sender)
            if not refreshed:
                await send_message(
                    "Your linked channel token has expired. "
                    "Please re-link your channel in Account settings."
                )
                return
            old_token = request["delegation_token_override"]
            request["delegation_token_override"] = refreshed["delegation_token"]
            request["agent_client"]._token_cache.pop(old_token, None)
            try:
                await self._process_streaming(
                    text=request["text"],
                    attachments=request.get("attachments") or [],
                    sender=request["sender_key"],
                    agent_client=request["agent_client"],
                    delegation_token_override=request["delegation_token_override"],
                    send_message=send_message,
                    cancel_event=cancel_event,
                    channel=channel,
                    edit_message=request.get("edit_message"),
                    delete_message=request.get("delete_message"),
                    format_content=request.get("format_content"),
                )
            except Exception as e:
                logger.error("Error processing %s message after token refresh: %s", channel, e, exc_info=True)
                await send_message("Sorry, I encountered an error processing your message.")
        except Exception as e:
            logger.error("Error processing %s message: %s", channel, e, exc_info=True)
            await send_message("Sorry, I encountered an error processing your message.")
        finally:
            if send_typing_stop:
                await send_typing_stop()

    async def _process_streaming(
        self,
        *,
        text: str,
        attachments: List[Dict[str, Any]],
        sender: str,
        agent_client: AgentClient,
        delegation_token_override: str | None,
        send_message: Callable[[str], Awaitable[Any]],
        cancel_event: asyncio.Event | None = None,
        channel: str = "",
        edit_message: Callable[[int, str], Awaitable[None]] | None = None,
        delete_message: Callable[[int], Awaitable[None]] | None = None,
        format_content: Callable[[str], str] | None = None,
    ) -> str:
        """
        Stream agent events and deliver user-visible content chunks incrementally.

        When edit_message / delete_message callbacks are provided (e.g. Telegram),
        telemetry events (thought, tool_start, tool_result) are consolidated into
        a single editable status message that is deleted once real content arrives.

        send_message may return an int (message_id) on channels that support it.
        """
        partial_buffer = ""
        collected_messages: List[str] = []
        loop = asyncio.get_running_loop()
        last_emit_at = loop.time()
        debounce_seconds = 0.5

        # Editable status message tracking (Telegram only)
        status_message_id: int | None = None
        supports_status_msg = edit_message is not None and delete_message is not None

        def _apply_format(raw: str) -> str:
            if format_content:
                try:
                    return format_content(raw)
                except Exception:
                    logger.debug("Content formatting failed, sending plain text")
            return raw

        async def _clear_status_message() -> None:
            nonlocal status_message_id
            if status_message_id is not None and delete_message is not None:
                await delete_message(status_message_id)
                status_message_id = None

        async def flush_partial_buffer() -> None:
            nonlocal partial_buffer, last_emit_at
            if not partial_buffer.strip():
                partial_buffer = ""
                return
            await _clear_status_message()
            formatted = _apply_format(partial_buffer)
            for chunk in self._split_response(formatted):
                await send_message(chunk)
            collected_messages.append(partial_buffer)
            partial_buffer = ""
            last_emit_at = loop.time()

        async for event in agent_client.chat_message_stream(
            message=text,
            sender=sender,
            enable_web_search=True,
            enable_doc_search=True,
            model=self.settings.default_model,
            agent_id=self.settings.default_agent_id or None,
            delegation_token_override=delegation_token_override,
            attachments=attachments,
            channel=channel,
        ):
            if cancel_event and cancel_event.is_set():
                break

            event_type = str(event.get("_event_type") or "")
            if event_type == "error":
                detail = str(event.get("message") or event.get("error") or "Unknown error")
                raise RuntimeError(detail)

            if event_type in ("thought", "tool_start", "tool_result"):
                telemetry_msg = str(event.get("message") or "").strip()
                if not telemetry_msg:
                    continue
                if supports_status_msg:
                    status_text = f"💭 {telemetry_msg}"
                    if status_message_id is not None:
                        await edit_message(status_message_id, status_text)
                    else:
                        status_message_id = await send_message(status_text)
                else:
                    await send_message(telemetry_msg)
                continue

            if event_type not in ("content", "complete", "message_complete"):
                continue

            if event_type in ("complete", "message_complete"):
                await flush_partial_buffer()
                continue

            message = str(event.get("message") or "")
            event_data = event.get("data") if isinstance(event.get("data"), dict) else {}
            is_partial = bool(event_data.get("partial")) if isinstance(event_data, dict) else False
            is_complete_marker = bool(event_data.get("complete")) if isinstance(event_data, dict) else False
            if is_complete_marker:
                await flush_partial_buffer()
                continue
            if not message:
                continue

            if is_partial:
                partial_buffer += message
                now = loop.time()
                should_flush = (
                    now - last_emit_at >= debounce_seconds
                    or len(partial_buffer) >= 140
                    or partial_buffer.endswith((".", "!", "?", "\n"))
                )
                if should_flush:
                    await flush_partial_buffer()
                continue

            await flush_partial_buffer()
            await _clear_status_message()
            formatted = _apply_format(message)
            for chunk in self._split_response(formatted):
                await send_message(chunk)
            collected_messages.append(message)

        await flush_partial_buffer()
        await _clear_status_message()
        return "\n".join(collected_messages).strip() or "No response generated."

    async def process_audio(
        self,
        *,
        channel: str,
        external_sender: str,
        audio_url: str,
        send_message: Callable[[str], Awaitable[Any]],
        send_typing_start: Callable[[], Awaitable[None]] | None,
        send_typing_stop: Callable[[], Awaitable[None]] | None,
        agent_client: AgentClient,
        edit_message: Callable[[int, str], Awaitable[None]] | None = None,
        delete_message: Callable[[int], Awaitable[None]] | None = None,
        format_content: Callable[[str], str] | None = None,
    ) -> None:
        """
        Process an inbound voice/audio message by asking the chat agent to
        transcribe and respond.
        """
        prompt = (
            "Transcribe the following audio file and respond to the user. "
            "If it contains a question or request, answer it after transcription.\n\n"
            f"Audio URL: {audio_url}"
        )
        await self.process(
            channel=channel,
            external_sender=external_sender,
            text=prompt,
            attachments=None,
            send_message=send_message,
            send_typing_start=send_typing_start,
            send_typing_stop=send_typing_stop,
            agent_client=agent_client,
            edit_message=edit_message,
            delete_message=delete_message,
            format_content=format_content,
        )

    async def _exchange_access_token(
        self,
        *,
        subject_token: str,
        audience: str,
        scope: str,
    ) -> str:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                str(self.settings.auth_token_url),
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": subject_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "audience": audience,
                    "scope": scope,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        resp.raise_for_status()
        data = resp.json() or {}
        token = str(data.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Token exchange succeeded but access_token was missing")
        return token

    async def _upload_attachment_for_chat(
        self,
        *,
        source_url: str,
        filename: str,
        mime_type: str,
        subject_token: str,
        channel: str,
        external_sender: str,
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            source_resp = await client.get(source_url)
            source_resp.raise_for_status()
            file_bytes = source_resp.content

        data_api_token = await self._exchange_access_token(
            subject_token=subject_token,
            audience="data-api",
            scope="data.write data.read",
        )

        metadata = {
            "source": f"{channel}-bridge",
            "external_sender": external_sender,
        }
        data = {
            "visibility": "personal",
            "metadata": json.dumps(metadata),
        }
        files = {"file": (filename, file_bytes, mime_type or "application/octet-stream")}
        upload_url = f"{str(self.settings.data_api_url).rstrip('/')}/upload"
        async with httpx.AsyncClient(timeout=180.0) as client:
            upload_resp = await client.post(
                upload_url,
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {data_api_token}"},
            )
        upload_resp.raise_for_status()
        payload = upload_resp.json() or {}
        file_id = str(payload.get("file_id") or payload.get("id") or "").strip()
        if not file_id:
            raise RuntimeError("Upload succeeded but file_id was missing")
        file_size = int(payload.get("size") or len(file_bytes) or 0)
        file_url = f"{str(self.settings.data_api_url).rstrip('/')}/files/{file_id}/download"
        return {
            "name": filename,
            "type": mime_type or "application/octet-stream",
            "url": file_url,
            "size": file_size,
        }

    async def process_document(
        self,
        *,
        channel: str,
        external_sender: str,
        text: str,
        attachment_url: str,
        attachment_filename: str,
        attachment_mime_type: str,
        send_message: Callable[[str], Awaitable[Any]],
        send_typing_start: Callable[[], Awaitable[None]] | None,
        send_typing_stop: Callable[[], Awaitable[None]] | None,
        agent_client: AgentClient,
        edit_message: Callable[[int, str], Awaitable[None]] | None = None,
        delete_message: Callable[[int], Awaitable[None]] | None = None,
        format_content: Callable[[str], str] | None = None,
    ) -> None:
        binding = await self._resolve_sender_binding(channel, external_sender)
        subject_token = ""
        if binding and binding.get("delegation_token"):
            subject_token = str(binding["delegation_token"]).strip()
        else:
            subject_token = str(self.settings.delegation_token or "").strip()
        if not subject_token:
            await send_message(
                "This channel is not linked yet. Generate a link code in your Account settings and send /link <code>."
            )
            return

        try:
            attachment = await self._upload_attachment_for_chat(
                source_url=attachment_url,
                filename=attachment_filename or "telegram-attachment",
                mime_type=attachment_mime_type or "application/octet-stream",
                subject_token=subject_token,
                channel=channel,
                external_sender=external_sender,
            )
        except Exception as exc:
            logger.error("Failed to ingest %s attachment: %s", channel, exc, exc_info=True)
            await send_message("I couldn't ingest that attachment. Please try again.")
            return

        prompt = text.strip() if text and text.strip() else "I attached a file. Please analyze it and help me."
        await self.process(
            channel=channel,
            external_sender=external_sender,
            text=prompt,
            attachments=[attachment],
            send_message=send_message,
            send_typing_start=send_typing_start,
            send_typing_stop=send_typing_stop,
            agent_client=agent_client,
            edit_message=edit_message,
            delete_message=delete_message,
            format_content=format_content,
        )

    def _split_response(self, response: str) -> List[str]:
        max_length = self.settings.max_message_length
        if len(response) <= max_length:
            return [response]

        chunks: List[str] = []
        current = ""
        for line in response.split("\n"):
            if len(current) + len(line) + 1 > max_length:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = f"{current}\n{line}" if current else line
        if current:
            chunks.append(current)
        return chunks


class SignalBot:
    """Signal polling bot."""

    def __init__(self, settings: Settings, processor: MessageProcessor):
        self.settings = settings
        self.processor = processor
        self._running = False

    async def process_message(
        self,
        message: SignalMessage,
        signal_client: SignalClient,
        agent_client: AgentClient,
    ) -> None:
        sender = message.sender
        allowed = self.settings.get_allowed_phone_numbers()
        if allowed and sender not in allowed:
            logger.warning("Rejected Signal sender: %s", sender[:6])
            return

        text = message.message or ""
        if not text.strip() and message.attachments:
            first = message.attachments[0] if message.attachments else {}
            attachment_url = (
                str(first.get("url", "")).strip()
                or str(first.get("remoteUrl", "")).strip()
                or str(first.get("uri", "")).strip()
            )
            if attachment_url:
                await self.processor.process_audio(
                    channel="signal",
                    external_sender=sender,
                    audio_url=attachment_url,
                    send_message=lambda body: signal_client.send_message(sender, body),
                    send_typing_start=lambda: signal_client.send_typing_indicator(sender),
                    send_typing_stop=lambda: signal_client.send_typing_indicator(sender, stop=True),
                    agent_client=agent_client,
                )
                return

        await self.processor.process(
            channel="signal",
            external_sender=sender,
            text=text,
            send_message=lambda body: signal_client.send_message(sender, body),
            send_typing_start=lambda: signal_client.send_typing_indicator(sender),
            send_typing_stop=lambda: signal_client.send_typing_indicator(sender, stop=True),
            agent_client=agent_client,
        )

    async def run(self):
        settings = self.settings
        self._running = True
        async with SignalClient(
            base_url=str(settings.signal_cli_url),
            phone_number=settings.signal_phone_number,
        ) as signal_client:
            async with AgentClient(
                base_url=str(settings.agent_api_url),
                auth_token_url=str(settings.auth_token_url),
                delegation_token=settings.delegation_token,
                default_agent_id=settings.default_agent_id or None,
            ) as agent_client:
                if not await signal_client.is_registered():
                    logger.error("Signal phone number is not registered")
                    return
                if not await agent_client.health_check():
                    logger.warning("Agent API health check failed, continuing")
                async for message in signal_client.poll_messages(interval=settings.poll_interval):
                    if not self._running:
                        break
                    if message.is_group_message:
                        continue
                    asyncio.create_task(self.process_message(message, signal_client, agent_client))

    def stop(self):
        self._running = False


class TelegramBot:
    """Telegram polling bot."""

    def __init__(self, settings: Settings, processor: MessageProcessor):
        self.settings = settings
        self.processor = processor
        self._running = False

    @staticmethod
    def _make_telegram_callbacks(
        telegram_client: TelegramClient, chat_id: str
    ) -> Dict[str, Any]:
        """Build the set of Telegram-specific callbacks for a chat_id."""

        async def _send(body: str) -> Optional[int]:
            return await telegram_client.send_message(chat_id, body, parse_mode="HTML")

        async def _send_plain(body: str) -> Optional[int]:
            """Fallback sender without parse_mode (used when HTML fails)."""
            return await telegram_client.send_message(chat_id, body)

        async def _edit(message_id: int, body: str) -> None:
            await telegram_client.edit_message(chat_id, message_id, body)

        async def _delete(message_id: int) -> None:
            await telegram_client.delete_message(chat_id, message_id)

        def _format(raw: str) -> str:
            return markdown_to_telegram_html(raw)

        async def _safe_send(body: str) -> Optional[int]:
            """Send with HTML formatting; fall back to plain text on error."""
            try:
                return await _send(body)
            except Exception:
                logger.debug("HTML send failed for Telegram, falling back to plain text")
                plain = re.sub(r"<[^>]+>", "", body)
                return await _send_plain(plain)

        return {
            "send_message": _safe_send,
            "send_typing_start": lambda: telegram_client.send_typing_indicator(chat_id),
            "send_typing_stop": None,
            "edit_message": _edit,
            "delete_message": _delete,
            "format_content": _format,
        }

    async def run(self):
        self._running = True
        allowed = set(self.settings.get_allowed_telegram_chat_ids())

        async with TelegramClient(self.settings.telegram_bot_token) as telegram_client:
            async with AgentClient(
                base_url=str(self.settings.agent_api_url),
                auth_token_url=str(self.settings.auth_token_url),
                delegation_token=self.settings.delegation_token,
                default_agent_id=self.settings.default_agent_id or None,
            ) as agent_client:
                async for msg in telegram_client.poll_messages(
                    interval=self.settings.telegram_poll_interval,
                    timeout=self.settings.telegram_poll_timeout,
                ):
                    if not self._running:
                        break
                    if allowed and msg.chat_id not in allowed:
                        continue

                    cbs = self._make_telegram_callbacks(telegram_client, msg.chat_id)

                    if msg.audio_url and not (msg.text or "").strip():
                        await self.processor.process_audio(
                            channel="telegram",
                            external_sender=msg.sender_id,
                            audio_url=msg.audio_url,
                            send_message=cbs["send_message"],
                            send_typing_start=cbs["send_typing_start"],
                            send_typing_stop=cbs["send_typing_stop"],
                            agent_client=agent_client,
                            edit_message=cbs["edit_message"],
                            delete_message=cbs["delete_message"],
                            format_content=cbs["format_content"],
                        )
                        continue

                    if msg.attachment_url:
                        await self.processor.process_document(
                            channel="telegram",
                            external_sender=msg.sender_id,
                            text=msg.text,
                            attachment_url=msg.attachment_url,
                            attachment_filename=msg.attachment_filename or "telegram-attachment",
                            attachment_mime_type=msg.attachment_mime_type or "application/octet-stream",
                            send_message=cbs["send_message"],
                            send_typing_start=cbs["send_typing_start"],
                            send_typing_stop=cbs["send_typing_stop"],
                            agent_client=agent_client,
                            edit_message=cbs["edit_message"],
                            delete_message=cbs["delete_message"],
                            format_content=cbs["format_content"],
                        )
                        continue

                    await self.processor.process(
                        channel="telegram",
                        external_sender=msg.sender_id,
                        text=msg.text,
                        send_message=cbs["send_message"],
                        send_typing_start=cbs["send_typing_start"],
                        send_typing_stop=cbs["send_typing_stop"],
                        agent_client=agent_client,
                        edit_message=cbs["edit_message"],
                        delete_message=cbs["delete_message"],
                        format_content=cbs["format_content"],
                    )

    def stop(self):
        self._running = False


class DiscordBot:
    """Discord REST polling bot for configured channels."""

    def __init__(self, settings: Settings, processor: MessageProcessor):
        self.settings = settings
        self.processor = processor
        self._running = False

    async def run(self):
        self._running = True
        channel_ids = self.settings.get_discord_channel_ids()
        if not channel_ids:
            logger.warning("Discord enabled but DISCORD_CHANNEL_IDS is empty; skipping")
            return

        async with DiscordClient(self.settings.discord_bot_token) as discord_client:
            async with AgentClient(
                base_url=str(self.settings.agent_api_url),
                auth_token_url=str(self.settings.auth_token_url),
                delegation_token=self.settings.delegation_token,
                default_agent_id=self.settings.default_agent_id or None,
            ) as agent_client:
                tasks = [
                    asyncio.create_task(
                        self._run_channel_loop(discord_client, agent_client, channel_id)
                    )
                    for channel_id in channel_ids
                ]
                await asyncio.gather(*tasks)

    async def _run_channel_loop(
        self,
        discord_client: DiscordClient,
        agent_client: AgentClient,
        channel_id: str,
    ) -> None:
        async for msg in discord_client.poll_messages(
            channel_id=channel_id,
            interval=self.settings.discord_poll_interval,
        ):
            if not self._running:
                break
            await self.processor.process(
                channel="discord",
                external_sender=msg.author_id,
                text=msg.content,
                send_message=lambda body, cid=msg.channel_id: discord_client.send_message(cid, body),
                send_typing_start=None,
                send_typing_stop=None,
                agent_client=agent_client,
            )

    def stop(self):
        self._running = False


class WhatsAppWebhookBot:
    """WhatsApp webhook ingress handler for Cloud API."""

    def __init__(self, settings: Settings, processor: MessageProcessor):
        self.settings = settings
        self.processor = processor

    async def handle_webhook(self, payload: dict) -> None:
        allowed = set(self.settings.get_allowed_whatsapp_phone_numbers())
        messages = WhatsAppClient.parse_webhook_messages(payload)
        if not messages:
            return

        async with AgentClient(
            base_url=str(self.settings.agent_api_url),
            auth_token_url=str(self.settings.auth_token_url),
            delegation_token=self.settings.delegation_token,
            default_agent_id=self.settings.default_agent_id or None,
        ) as agent_client:
            async with WhatsAppClient(
                access_token=self.settings.whatsapp_access_token,
                phone_number_id=self.settings.whatsapp_phone_number_id,
                api_version=self.settings.whatsapp_api_version,
            ) as wa_client:
                for msg in messages:
                    if allowed and msg.from_phone not in allowed:
                        continue
                    await self.processor.process(
                        channel="whatsapp",
                        external_sender=msg.from_phone,
                        text=msg.text,
                        send_message=lambda body, to=msg.from_phone: wa_client.send_message(to, body),
                        send_typing_start=None,
                        send_typing_stop=None,
                        agent_client=agent_client,
                    )


@dataclass
class _PendingEmailConfirmation:
    """Tracks a confirmation code sent to an inbound email sender."""
    code: str
    expires_at: datetime
    original_message: InboundEmailMessage
    binding: Dict[str, str]


class EmailInboundBot:
    """Inbound email polling with per-conversation confirmation.

    Flow:
    1. Email arrives; bridge looks up a channel binding for the sender.
    2. If no binding, reply asking user to link their email in Account settings.
    3. If an active confirmed session exists (< TTL), process normally.
    4. Otherwise send a 6-digit confirmation code and hold the original message.
    5. When the user replies with the code (within TTL), confirm and process.
    """

    def __init__(self, settings: Settings, processor: MessageProcessor):
        self.settings = settings
        self.processor = processor
        self._running = False
        self._pending: Dict[str, _PendingEmailConfirmation] = {}
        self._confirmed_sessions: Dict[str, datetime] = {}

    def _session_active(self, sender: str) -> bool:
        confirmed_at = self._confirmed_sessions.get(sender)
        if not confirmed_at:
            return False
        return datetime.now(timezone.utc) - confirmed_at < timedelta(
            seconds=self.settings.email_confirmation_ttl
        )

    def _touch_session(self, sender: str) -> None:
        self._confirmed_sessions[sender] = datetime.now(timezone.utc)

    async def _send_confirmation(
        self,
        email_client: EmailClient,
        sender: str,
        inbound_msg: InboundEmailMessage,
        binding: Dict[str, str],
    ) -> None:
        code = secrets.token_hex(3).upper()  # 6 hex chars
        self._pending[sender] = _PendingEmailConfirmation(
            code=code,
            expires_at=datetime.now(timezone.utc) + timedelta(
                seconds=self.settings.email_confirmation_ttl
            ),
            original_message=inbound_msg,
            binding=binding,
        )
        ttl_hours = self.settings.email_confirmation_ttl // 3600
        subject = f"Re: {inbound_msg.subject or 'Your message'}"
        text_body = (
            f"To confirm this email action, reply with the following code:\n\n"
            f"  {code}\n\n"
            f"This code expires in {ttl_hours} hour{'s' if ttl_hours != 1 else ''}.\n"
            f"If you did not send this email, please ignore this message."
        )
        html_body = (
            f"<p>To confirm this email action, reply with the following code:</p>"
            f"<p style='font-size:24px;font-weight:bold;letter-spacing:4px'>{code}</p>"
            f"<p>This code expires in {ttl_hours} hour{'s' if ttl_hours != 1 else ''}.</p>"
            f"<p><em>If you did not send this email, please ignore this message.</em></p>"
        )
        await email_client.send(to=sender, subject=subject, html=html_body, text=text_body)
        logger.info("Sent email confirmation code to %s", sender)

    def _extract_code(self, body: str) -> str | None:
        """Extract a 6-char hex code from the message body."""
        stripped = body.strip().split("\n")[0].strip()
        if len(stripped) == 6 and all(c in "0123456789ABCDEFabcdef" for c in stripped):
            return stripped.upper()
        return None

    async def _process_and_reply(
        self,
        *,
        sender: str,
        body: str,
        subject: str,
        binding: Dict[str, str],
        email_client: EmailClient,
        agent_client: AgentClient,
    ) -> None:
        chunks: List[str] = []

        async def _capture(chunk: str):
            chunks.append(chunk)

        await self.processor.process(
            channel="email",
            external_sender=sender,
            text=body,
            send_message=_capture,
            send_typing_start=None,
            send_typing_stop=None,
            agent_client=agent_client,
        )
        if not chunks:
            return
        response_text = "\n\n".join(chunks)
        await email_client.send(
            to=sender,
            subject=f"Re: {subject or 'Your message'}",
            html=response_text.replace("\n", "<br/>"),
            text=response_text,
        )

    async def run(self):
        self._running = True
        allowed_senders = set(self.settings.get_email_allowed_senders())
        email_client = EmailClient(self.settings)

        inbound = EmailInboundClient(
            host=self.settings.imap_host or "",
            port=self.settings.imap_port,
            username=self.settings.imap_user or "",
            password=self.settings.imap_password or "",
            folder=self.settings.imap_folder,
            use_ssl=self.settings.imap_use_ssl,
        )

        async with AgentClient(
            base_url=str(self.settings.agent_api_url),
            auth_token_url=str(self.settings.auth_token_url),
            delegation_token=self.settings.delegation_token,
            default_agent_id=self.settings.default_agent_id or None,
        ) as agent_client:
            async for inbound_msg in inbound.poll_messages(interval=self.settings.email_inbound_poll_interval):
                if not self._running:
                    break
                sender = inbound_msg.sender_email.strip().lower()
                if not sender:
                    continue
                if allowed_senders and sender not in allowed_senders:
                    continue

                body = inbound_msg.body or inbound_msg.subject or ""
                if not body.strip():
                    continue

                # --- Check for pending confirmation reply ---
                pending = self._pending.get(sender)
                if pending:
                    if datetime.now(timezone.utc) > pending.expires_at:
                        del self._pending[sender]
                        pending = None
                    else:
                        code = self._extract_code(body)
                        if code and code == pending.code:
                            self._touch_session(sender)
                            held = pending.original_message
                            del self._pending[sender]
                            logger.info("Email confirmed for %s — processing held message", sender)
                            await self._process_and_reply(
                                sender=sender,
                                body=held.body or held.subject or "",
                                subject=held.subject,
                                binding=pending.binding,
                                email_client=email_client,
                                agent_client=agent_client,
                            )
                            continue
                        # Code didn't match — fall through to normal flow
                        # (the user might have sent a new unrelated email)

                # --- Resolve channel binding ---
                binding = await self.processor._resolve_sender_binding("email", sender)
                if not binding:
                    await email_client.send(
                        to=sender,
                        subject=f"Re: {inbound_msg.subject or 'Your message'}",
                        html=(
                            "<p>Your email address is not linked to a Busibox account.</p>"
                            "<p>Please link your email in your <strong>Account settings</strong> "
                            "in the Busibox portal, then try again.</p>"
                        ),
                        text=(
                            "Your email address is not linked to a Busibox account.\n"
                            "Please link your email in your Account settings "
                            "in the Busibox portal, then try again."
                        ),
                    )
                    continue

                # --- Active session? Process directly ---
                if self._session_active(sender):
                    self._touch_session(sender)
                    await self._process_and_reply(
                        sender=sender,
                        body=body,
                        subject=inbound_msg.subject,
                        binding=binding,
                        email_client=email_client,
                        agent_client=agent_client,
                    )
                    continue

                # --- No active session — send confirmation ---
                await self._send_confirmation(email_client, sender, inbound_msg, binding)


async def run_api_server(settings: Settings, whatsapp_handler: Callable[[dict], Awaitable[None]] | None):
    """Start the FastAPI HTTP server."""
    import uvicorn
    from .api import create_app

    app = create_app(settings, whatsapp_handler=whatsapp_handler)
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.bridge_api_port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    logger.info("Starting Bridge API on port %s", settings.bridge_api_port)
    await server.serve()


async def run_signal_bot(settings: Settings, processor: MessageProcessor):
    if not settings.signal_enabled:
        return
    if not settings.signal_phone_number:
        logger.warning("Signal enabled but SIGNAL_PHONE_NUMBER not set")
        return
    if not settings.delegation_token:
        logger.warning("Signal enabled but DELEGATION_TOKEN not set")
        return
    await SignalBot(settings, processor).run()


async def run_telegram_bot(settings: Settings, processor: MessageProcessor):
    if not settings.telegram_enabled:
        return
    if not settings.telegram_bot_token:
        logger.warning("Telegram enabled but TELEGRAM_BOT_TOKEN not set")
        return
    if not settings.delegation_token:
        logger.warning(
            "Telegram enabled without DELEGATION_TOKEN; /link works, but non-linked chats will be blocked until linked."
        )
    await TelegramBot(settings, processor).run()


async def run_discord_bot(settings: Settings, processor: MessageProcessor):
    if not settings.discord_enabled:
        return
    if not settings.discord_bot_token:
        logger.warning("Discord enabled but DISCORD_BOT_TOKEN not set")
        return
    if not settings.delegation_token:
        logger.warning("Discord enabled but DELEGATION_TOKEN not set")
        return
    await DiscordBot(settings, processor).run()


async def run_email_inbound_bot(settings: Settings, processor: MessageProcessor):
    if not settings.email_inbound_enabled:
        return
    if not settings.imap_host or not settings.imap_user or not settings.imap_password:
        logger.warning("Inbound email enabled but IMAP credentials are incomplete")
        return
    await EmailInboundBot(settings, processor).run()


# ---------------------------------------------------------------------------
# Polling Manager — supervises all channel polling tasks
# ---------------------------------------------------------------------------

# Shared state dict — the health endpoint reads this.
_polling_status: Dict[str, str] = {}


def get_polling_status() -> Dict[str, str]:
    """Return snapshot of channel polling status for health endpoint."""
    return dict(_polling_status)


class PollingManager:
    """
    Manages the lifecycle of channel polling tasks.

    Every ``check_interval`` seconds, inspects which channels *should* be
    running and which *are* running.  Starts new tasks, restarts crashed
    ones, and logs status.

    The manager re-reads settings from the cached ``get_settings()`` each
    cycle so it picks up env-var changes that arrive via container restart.
    """

    CHECK_INTERVAL = 5.0  # seconds between supervision cycles
    BACKOFF_BASE = 5.0    # seconds to wait before restarting a crashed task
    BACKOFF_MAX = 60.0    # cap for exponential back-off

    def __init__(self, settings: Settings, processor: MessageProcessor):
        self.settings = settings
        self.processor = processor
        self._tasks: Dict[str, asyncio.Task] = {}
        self._crash_count: Dict[str, int] = {}

    def _channel_should_run(self, name: str) -> bool:
        s = self.settings
        if name == "signal":
            return bool(s.signal_enabled and s.signal_phone_number)
        if name == "telegram":
            return bool(s.telegram_enabled and s.telegram_bot_token)
        if name == "discord":
            return bool(s.discord_enabled and s.discord_bot_token)
        if name == "email_inbound":
            return bool(
                s.email_inbound_enabled
                and s.imap_host and s.imap_user and s.imap_password
            )
        return False

    def _create_coro(self, name: str):
        if name == "signal":
            return run_signal_bot(self.settings, self.processor)
        if name == "telegram":
            return run_telegram_bot(self.settings, self.processor)
        if name == "discord":
            return run_discord_bot(self.settings, self.processor)
        if name == "email_inbound":
            return run_email_inbound_bot(self.settings, self.processor)
        raise ValueError(f"Unknown channel: {name}")

    def _start_task(self, name: str) -> None:
        task = asyncio.create_task(self._create_coro(name), name=f"bridge-poll-{name}")
        self._tasks[name] = task
        _polling_status[name] = "running"
        logger.info("Polling started for %s", name)

    async def run(self) -> None:
        """Supervision loop — runs forever alongside the API server."""
        channels = ["signal", "telegram", "discord", "email_inbound"]

        # Initial start for all enabled channels
        for ch in channels:
            if self._channel_should_run(ch):
                self._start_task(ch)
            else:
                _polling_status[ch] = "disabled"
                logger.info("Channel %s not configured — skipping", ch)

        while True:
            await asyncio.sleep(self.CHECK_INTERVAL)

            for ch in channels:
                should_run = self._channel_should_run(ch)
                task = self._tasks.get(ch)
                is_running = task is not None and not task.done()

                if should_run and not is_running:
                    # Needs to be running but isn't
                    if task is not None and task.done():
                        try:
                            exc = task.exception() if not task.cancelled() else None
                        except (asyncio.CancelledError, Exception):
                            exc = None
                        if exc:
                            crashes = self._crash_count.get(ch, 0) + 1
                            self._crash_count[ch] = crashes
                            backoff = min(
                                self.BACKOFF_BASE * (2 ** (crashes - 1)),
                                self.BACKOFF_MAX,
                            )
                            _polling_status[ch] = f"crashed (attempt #{crashes})"
                            logger.error(
                                "Channel %s crashed (attempt #%d): %s — restarting in %.0fs",
                                ch, crashes, exc, backoff,
                            )
                            await asyncio.sleep(backoff)
                        else:
                            logger.info(
                                "Channel %s exited cleanly — restarting", ch,
                            )
                            self._crash_count[ch] = 0

                    self._start_task(ch)

                elif not should_run and is_running:
                    logger.info("Channel %s no longer configured — stopping", ch)
                    task.cancel()
                    self._tasks.pop(ch, None)
                    self._crash_count.pop(ch, None)
                    _polling_status[ch] = "disabled"

                elif should_run and is_running:
                    self._crash_count[ch] = 0

                elif not should_run and not is_running:
                    _polling_status[ch] = "disabled"


async def main():
    settings = get_settings()
    logging.getLogger().setLevel(settings.log_level)

    identity = ChannelIdentityResolver(settings.get_channel_user_bindings())
    processor = MessageProcessor(settings, identity)

    logger.info("Bridge starting (env=%s)", settings.environment)
    logger.info("  API server:  port %s", settings.bridge_api_port)
    logger.info("  Signal:      %s", "enabled" if settings.signal_enabled else "disabled")
    logger.info("  Telegram:    %s", "enabled" if settings.telegram_enabled else "disabled")
    logger.info("  Discord:     %s", "enabled" if settings.discord_enabled else "disabled")
    logger.info("  WhatsApp:    %s", "enabled" if settings.whatsapp_enabled else "disabled")
    logger.info("  Email:       %s", "enabled" if settings.email_enabled else "disabled")
    logger.info("  Inbound mail:%s", "enabled" if settings.email_inbound_enabled else "disabled")

    whatsapp_bot = WhatsAppWebhookBot(settings, processor) if settings.whatsapp_enabled else None
    whatsapp_handler = whatsapp_bot.handle_webhook if whatsapp_bot else None

    polling_manager = PollingManager(settings, processor)

    try:
        await asyncio.gather(
            run_api_server(settings, whatsapp_handler),
            polling_manager.run(),
        )
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error("Bridge crashed: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
