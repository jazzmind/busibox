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
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Dict, List

from .agent_client import AgentClient
from .channel_identity import ChannelIdentityResolver
from .config import Settings, get_settings
from .discord_client import DiscordClient
from .email_client import EmailClient
from .email_inbound_client import EmailInboundClient
from .signal_client import SignalClient, SignalMessage
from .telegram_client import TelegramClient, TelegramMessage
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
        send_message: Callable[[str], Awaitable[None]],
        send_typing_start: Callable[[], Awaitable[None]] | None,
        send_typing_stop: Callable[[], Awaitable[None]] | None,
        agent_client: AgentClient,
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

        if send_typing_start:
            await send_typing_start()

        try:
            response = await self._process_simple(
                text=text,
                sender=sender_key,
                agent_client=agent_client,
                delegation_token_override=delegation_token_override,
            )
            for chunk in self._split_response(response):
                await send_message(chunk)
        except Exception as e:
            logger.error("Error processing %s message: %s", channel, e, exc_info=True)
            await send_message("Sorry, I encountered an error processing your message.")
        finally:
            if send_typing_stop:
                await send_typing_stop()

    async def _process_simple(
        self,
        *,
        text: str,
        sender: str,
        agent_client: AgentClient,
        delegation_token_override: str | None = None,
    ) -> str:
        if self.settings.debug:
            parts: List[str] = []
            async for event in agent_client.chat_message_stream(
                message=text,
                sender=sender,
                enable_web_search=self.settings.enable_web_search,
                enable_doc_search=self.settings.enable_doc_search,
                model=self.settings.default_model,
                agent_id=self.settings.default_agent_id or None,
                delegation_token_override=delegation_token_override,
            ):
                if event.get("_event_type") == "content":
                    parts.append(event.get("message", ""))
                elif event.get("_event_type") == "complete":
                    break
            return "\n".join(parts) if parts else "No response generated."

        response = await agent_client.chat_message(
            message=text,
            sender=sender,
            enable_web_search=self.settings.enable_web_search,
            enable_doc_search=self.settings.enable_doc_search,
            model=self.settings.default_model,
            agent_id=self.settings.default_agent_id or None,
            delegation_token_override=delegation_token_override,
        )
        return response.content

    async def process_audio(
        self,
        *,
        channel: str,
        external_sender: str,
        audio_url: str,
        send_message: Callable[[str], Awaitable[None]],
        send_typing_start: Callable[[], Awaitable[None]] | None,
        send_typing_stop: Callable[[], Awaitable[None]] | None,
        agent_client: AgentClient,
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
            send_message=send_message,
            send_typing_start=send_typing_start,
            send_typing_stop=send_typing_stop,
            agent_client=agent_client,
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
                    if msg.audio_url and not (msg.text or "").strip():
                        await self.processor.process_audio(
                            channel="telegram",
                            external_sender=msg.sender_id,
                            audio_url=msg.audio_url,
                            send_message=lambda body, chat_id=msg.chat_id: telegram_client.send_message(chat_id, body),
                            send_typing_start=lambda chat_id=msg.chat_id: telegram_client.send_typing_indicator(chat_id),
                            send_typing_stop=None,
                            agent_client=agent_client,
                        )
                        continue

                    await self.processor.process(
                        channel="telegram",
                        external_sender=msg.sender_id,
                        text=msg.text,
                        send_message=lambda body, chat_id=msg.chat_id: telegram_client.send_message(chat_id, body),
                        send_typing_start=lambda chat_id=msg.chat_id: telegram_client.send_typing_indicator(chat_id),
                        send_typing_stop=None,
                        agent_client=agent_client,
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


class EmailInboundBot:
    """Inbound email polling + agent reply loop."""

    def __init__(self, settings: Settings, processor: MessageProcessor):
        self.settings = settings
        self.processor = processor
        self._running = False

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
                    continue
                response_text = "\n\n".join(chunks)
                await email_client.send(
                    to=sender,
                    subject=f"Re: {inbound_msg.subject or 'Your message'}",
                    html=response_text.replace("\n", "<br/>"),
                    text=response_text,
                )


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
        logger.info("Signal channel disabled")
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
        logger.info("Telegram channel disabled")
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
        logger.info("Discord channel disabled")
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
        logger.info("Inbound email channel disabled")
        return
    if not settings.delegation_token:
        logger.warning("Inbound email enabled but DELEGATION_TOKEN not set")
        return
    if not settings.imap_host or not settings.imap_user or not settings.imap_password:
        logger.warning("Inbound email enabled but IMAP credentials are incomplete")
        return
    await EmailInboundBot(settings, processor).run()


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

    try:
        await asyncio.gather(
            run_api_server(settings, whatsapp_handler),
            run_signal_bot(settings, processor),
            run_telegram_bot(settings, processor),
            run_discord_bot(settings, processor),
            run_email_inbound_bot(settings, processor),
        )
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error("Bridge crashed: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
