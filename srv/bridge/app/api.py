"""
Bridge HTTP API

FastAPI application providing email sending and health check endpoints.
Runs alongside the Signal bot polling loop as a co-process.

Authentication:
  Bridge trusts requests from the internal container network (Option A from plan).
  All containers are isolated; bridge only listens on its internal IP and is not
  exposed externally. This is the same trust boundary that allows Busibox Portal to
  call AuthZ unauthenticated for the magic-link flow.
"""

import logging
import time
from typing import Awaitable, Callable, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr

from .config import Settings
from .agent_client import AgentClient
from .whatsapp_client import WhatsAppClient
from .signal_client import SignalClient
from .telegram_client import TelegramClient
from .discord_client import DiscordClient
from .email_client import EmailClient

logger = logging.getLogger(__name__)


def _extract_session_jwt(request: Request) -> Optional[str]:
    """Extract session JWT from Authorization header if present."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SendMagicLinkRequest(BaseModel):
    to: str
    magic_link_url: str = ""
    totp_code: str = ""


class SendMagicLinkSimpleRequest(BaseModel):
    to: str
    magic_link_url: str


class SendEmailRequest(BaseModel):
    to: str
    subject: str
    html: str
    text: str = ""


class SendWelcomeRequest(BaseModel):
    to: str
    user_name: Optional[str] = None
    portal_url: str = ""


class SendAccountNotificationRequest(BaseModel):
    to: str
    portal_url: str = ""


class SendTestRequest(BaseModel):
    to: str


class EmailResponse(BaseModel):
    success: bool
    provider: str = "none"
    message: str = ""


class AgentRoundtripRequest(BaseModel):
    message: str = "ping"
    sender: str = "bridge-health-check"
    agent_id: Optional[str] = None
    delegation_token: Optional[str] = None


class LinkInitiateRequest(BaseModel):
    user_id: str
    channel_type: str
    delegation_token: str
    delegation_token_jti: Optional[str] = None


class SendChannelMessageRequest(BaseModel):
    channel_type: str
    recipient: str
    text: str
    metadata: Optional[dict] = None


class SendNotificationRequest(BaseModel):
    """Generic notification request — bridge picks the best available channel.

    ``recipient`` is an email address, phone number, or channel-specific ID
    depending on what channels are enabled.  Bridge tries: email → telegram →
    signal → discord, stopping at the first one that succeeds.

    ``notification_type`` is a free-form label for the caller (e.g.
    ``daily_summary``, ``alert``, ``report``).  It does not change routing in
    this MVP — it is logged and passed through in the response for tracing.
    """

    app_id: str = ""
    notification_type: str = "generic"
    recipient: str
    subject: str
    body: str
    priority: str = "normal"  # low | normal | high
    metadata: Optional[dict] = None


class NotificationResponse(BaseModel):
    success: bool
    channel_used: str = ""
    provider: str = ""
    message: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    settings: Settings,
    whatsapp_handler: Optional[Callable[[dict], Awaitable[None]]] = None,
) -> FastAPI:
    """Create the FastAPI application with email endpoints."""

    app = FastAPI(
        title="Bridge API",
        description="Multi-channel communication bridge — email, Signal, and more.",
        version="1.0.0",
    )

    email_client = EmailClient(settings)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health():
        from .main import get_polling_status

        return {
            "status": "ok",
            "service": "bridge",
            "email_provider": email_client.provider,
            "email_enabled": settings.email_enabled,
            "signal_enabled": settings.signal_enabled,
            "telegram_enabled": settings.telegram_enabled,
            "discord_enabled": settings.discord_enabled,
            "whatsapp_enabled": settings.whatsapp_enabled,
            "default_agent_id": settings.default_agent_id or None,
            "polling": get_polling_status(),
        }

    @app.post("/api/v1/test/agent-roundtrip")
    async def test_agent_roundtrip(req: AgentRoundtripRequest):
        """Verify bridge can exchange token and call agent API."""
        effective_delegation_token = (req.delegation_token or "").strip() or settings.delegation_token
        if not effective_delegation_token:
            raise HTTPException(status_code=400, detail="DELEGATION_TOKEN is not configured")

        started = time.perf_counter()
        try:
            async with AgentClient(
                base_url=str(settings.agent_api_url),
                auth_token_url=str(settings.auth_token_url),
                delegation_token=effective_delegation_token,
                default_agent_id=settings.default_agent_id or None,
            ) as agent_client:
                response = await agent_client.chat_message(
                    message=req.message.strip() or "ping",
                    sender=req.sender.strip() or "bridge-health-check",
                    enable_web_search=False,
                    enable_doc_search=False,
                    model=settings.default_model,
                    agent_id=req.agent_id or None,
                    delegation_token_override=effective_delegation_token,
                )
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "conversation_id": response.conversation_id,
                "message_id": response.message_id,
                "response_preview": (response.content or "")[:200],
            }
        except Exception as exc:
            logger.error("[API] agent roundtrip test failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=str(exc))

    @app.post("/api/v1/link/initiate")
    async def initiate_link(req: LinkInitiateRequest):
        """
        Create or refresh a pending channel-link code for a user.
        Called by busibox-portal account linking UI.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{str(settings.authz_base_url).rstrip('/')}/internal/channel-bindings/initiate",
                    json={
                        "user_id": req.user_id,
                        "channel_type": req.channel_type,
                        "delegation_token": req.delegation_token,
                        "delegation_token_jti": req.delegation_token_jti,
                    },
                )
            resp.raise_for_status()
            binding = (resp.json() or {}).get("binding") or {}
            return {
                "ok": True,
                "channel_type": req.channel_type,
                "link_code": binding.get("link_code"),
                "link_expires_at": binding.get("link_expires_at"),
            }
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            raise HTTPException(status_code=502, detail=detail)
        except Exception as exc:
            logger.error("[API] link initiate failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/channels/send")
    async def send_channel_message(req: SendChannelMessageRequest):
        """
        Send an outbound bridge message to a specific channel recipient.
        """
        channel_type = (req.channel_type or "").strip().lower()
        recipient = (req.recipient or "").strip()
        text = (req.text or "").strip()

        if not channel_type:
            raise HTTPException(status_code=400, detail="channel_type is required")
        if not recipient:
            raise HTTPException(status_code=400, detail="recipient is required")
        if not text:
            raise HTTPException(status_code=400, detail="text is required")

        try:
            if channel_type == "signal":
                if not settings.signal_enabled:
                    raise HTTPException(status_code=503, detail="Signal channel not enabled")
                if not settings.signal_phone_number:
                    raise HTTPException(status_code=503, detail="SIGNAL_PHONE_NUMBER is not configured")
                async with SignalClient(
                    base_url=str(settings.signal_cli_url),
                    phone_number=settings.signal_phone_number,
                ) as signal_client:
                    ok = await signal_client.send_message(recipient, text)
                if not ok:
                    raise HTTPException(status_code=502, detail="Failed to send Signal message")
            elif channel_type == "telegram":
                if not settings.telegram_enabled:
                    raise HTTPException(status_code=503, detail="Telegram channel not enabled")
                if not settings.telegram_bot_token:
                    raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN is not configured")
                parse_mode = None
                if req.metadata and isinstance(req.metadata, dict):
                    parse_mode = req.metadata.get("telegram_parse_mode")
                async with TelegramClient(settings.telegram_bot_token) as telegram_client:
                    await telegram_client.send_message(recipient, text, parse_mode=parse_mode)
            elif channel_type == "discord":
                if not settings.discord_enabled:
                    raise HTTPException(status_code=503, detail="Discord channel not enabled")
                if not settings.discord_bot_token:
                    raise HTTPException(status_code=503, detail="DISCORD_BOT_TOKEN is not configured")
                async with DiscordClient(settings.discord_bot_token) as discord_client:
                    await discord_client.send_message(recipient, text)
            elif channel_type == "whatsapp":
                if not settings.whatsapp_enabled:
                    raise HTTPException(status_code=503, detail="WhatsApp channel not enabled")
                if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
                    raise HTTPException(status_code=503, detail="WhatsApp credentials are not configured")
                async with WhatsAppClient(
                    access_token=settings.whatsapp_access_token,
                    phone_number_id=settings.whatsapp_phone_number_id,
                    api_version=settings.whatsapp_api_version,
                ) as wa_client:
                    await wa_client.send_message(recipient, text)
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported channel_type: {channel_type}")
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("[API] channel send failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=502, detail=str(exc))

        return {"ok": True, "channel_type": channel_type, "recipient": recipient}

    # ------------------------------------------------------------------
    # WhatsApp webhook endpoints (Cloud API)
    # ------------------------------------------------------------------

    @app.get("/api/v1/channels/whatsapp/webhook")
    async def whatsapp_verify(
        mode: str = Query(alias="hub.mode"),
        verify_token: str = Query(alias="hub.verify_token"),
        challenge: str = Query(alias="hub.challenge"),
    ):
        if not settings.whatsapp_enabled:
            raise HTTPException(status_code=503, detail="WhatsApp channel not enabled")
        if mode != "subscribe":
            raise HTTPException(status_code=400, detail="Invalid mode")
        if not settings.whatsapp_verify_token or verify_token != settings.whatsapp_verify_token:
            raise HTTPException(status_code=403, detail="Invalid verify token")
        return int(challenge) if challenge.isdigit() else challenge

    @app.post("/api/v1/channels/whatsapp/webhook")
    async def whatsapp_webhook(request: Request):
        if not settings.whatsapp_enabled:
            raise HTTPException(status_code=503, detail="WhatsApp channel not enabled")
        payload = await request.json()
        # Basic validation: if parser sees nothing, still ACK to avoid retries.
        parsed = WhatsAppClient.parse_webhook_messages(payload)
        if parsed and whatsapp_handler is not None:
            await whatsapp_handler(payload)
        return {"ok": True, "message_count": len(parsed)}

    # ------------------------------------------------------------------
    # Email endpoints
    # ------------------------------------------------------------------

    @app.post("/api/v1/email/send-magic-link", response_model=EmailResponse)
    async def send_magic_link(req: SendMagicLinkRequest, request: Request):
        """
        Send a magic-link authentication email with TOTP code.
        Called by Busibox Portal during the login flow.
        """
        jwt = _extract_session_jwt(request)
        try:
            result = await email_client.send_magic_link(req.to, req.magic_link_url, req.totp_code, session_jwt=jwt)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Magic link email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-magic-link failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send-magic-link-simple", response_model=EmailResponse)
    async def send_magic_link_simple(req: SendMagicLinkSimpleRequest, request: Request):
        """
        Send a simple magic-link email (no TOTP code).
        Used for activation links sent to new users.
        """
        jwt = _extract_session_jwt(request)
        try:
            result = await email_client.send_magic_link_simple(req.to, req.magic_link_url, session_jwt=jwt)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Magic link email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-magic-link-simple failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send", response_model=EmailResponse)
    async def send_email(req: SendEmailRequest, request: Request):
        """
        Send a generic email with custom subject/body.
        """
        jwt = _extract_session_jwt(request)
        try:
            result = await email_client.send(req.to, req.subject, req.html, req.text, session_jwt=jwt)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send-welcome", response_model=EmailResponse)
    async def send_welcome(req: SendWelcomeRequest, request: Request):
        """Send a welcome email to a new user."""
        jwt = _extract_session_jwt(request)
        try:
            result = await email_client.send_welcome(req.to, req.user_name, req.portal_url, session_jwt=jwt)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Welcome email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-welcome failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send-account-deactivated", response_model=EmailResponse)
    async def send_account_deactivated(req: SendAccountNotificationRequest, request: Request):
        """Send account deactivation notification."""
        jwt = _extract_session_jwt(request)
        try:
            result = await email_client.send_account_deactivated(req.to, session_jwt=jwt)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Deactivation email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-account-deactivated failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/send-account-reactivated", response_model=EmailResponse)
    async def send_account_reactivated(req: SendAccountNotificationRequest, request: Request):
        """Send account reactivation notification."""
        jwt = _extract_session_jwt(request)
        try:
            result = await email_client.send_account_reactivated(req.to, req.portal_url, session_jwt=jwt)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Reactivation email sent to {req.to}",
            )
        except Exception as exc:
            logger.error(f"[API] send-account-reactivated failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/v1/email/test", response_model=EmailResponse)
    async def send_test_email(req: SendTestRequest, request: Request):
        """Send a test email to verify SMTP / Resend configuration."""
        jwt = _extract_session_jwt(request)
        try:
            result = await email_client.send_test(req.to, session_jwt=jwt)
            return EmailResponse(
                success=result.get("success", True),
                provider=result.get("provider", "unknown"),
                message=f"Test email sent to {req.to} via {result.get('provider')}",
            )
        except Exception as exc:
            logger.error(f"[API] test email failed: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    # ------------------------------------------------------------------
    # Generic notification endpoint
    # ------------------------------------------------------------------

    @app.post("/api/v1/notify", response_model=NotificationResponse)
    async def send_notification(req: SendNotificationRequest):
        """Send a notification via the best available channel.

        Tries channels in priority order: email → telegram → signal → discord.
        Stops at the first successful delivery and returns which channel was used.

        This is the preferred endpoint for application code (e.g. cron scripts,
        background workers) that want to notify a user without coupling to a
        specific channel.
        """
        logger.info(
            "[notify] app=%s type=%s recipient=%s priority=%s",
            req.app_id or "-",
            req.notification_type,
            req.recipient,
            req.priority,
        )

        errors: list[str] = []

        # ---- 1. Email -------------------------------------------------------
        if settings.email_enabled:
            try:
                html_body = (
                    f"<h2>{req.subject}</h2>"
                    f"<p>{req.body.replace(chr(10), '<br>')}</p>"
                )
                if req.app_id:
                    html_body += (
                        f"<p style='color:#888;font-size:12px'>"
                        f"Source: {req.app_id} | Type: {req.notification_type}"
                        f"</p>"
                    )
                result = await email_client.send(
                    req.recipient,
                    req.subject,
                    html_body,
                    req.body,
                )
                if result.get("success", True):
                    return NotificationResponse(
                        success=True,
                        channel_used="email",
                        provider=result.get("provider", "unknown"),
                        message=f"Notification sent to {req.recipient} via email",
                    )
                errors.append(f"email: {result.get('error', 'unknown error')}")
            except Exception as exc:
                errors.append(f"email: {exc}")
                logger.warning("[notify] email failed: %s", exc)

        # ---- 2. Telegram ----------------------------------------------------
        if settings.telegram_enabled and settings.telegram_bot_token:
            try:
                text = f"*{req.subject}*\n\n{req.body}"
                async with TelegramClient(settings.telegram_bot_token) as tg:
                    await tg.send_message(req.recipient, text, parse_mode="Markdown")
                return NotificationResponse(
                    success=True,
                    channel_used="telegram",
                    message=f"Notification sent to {req.recipient} via telegram",
                )
            except Exception as exc:
                errors.append(f"telegram: {exc}")
                logger.warning("[notify] telegram failed: %s", exc)

        # ---- 3. Signal ------------------------------------------------------
        if settings.signal_enabled and settings.signal_phone_number:
            try:
                text = f"{req.subject}\n\n{req.body}"
                async with SignalClient(
                    base_url=str(settings.signal_cli_url),
                    phone_number=settings.signal_phone_number,
                ) as sig:
                    ok = await sig.send_message(req.recipient, text)
                if ok:
                    return NotificationResponse(
                        success=True,
                        channel_used="signal",
                        message=f"Notification sent to {req.recipient} via signal",
                    )
                errors.append("signal: send returned False")
            except Exception as exc:
                errors.append(f"signal: {exc}")
                logger.warning("[notify] signal failed: %s", exc)

        # ---- 4. Discord -----------------------------------------------------
        if settings.discord_enabled and settings.discord_bot_token:
            try:
                text = f"**{req.subject}**\n\n{req.body}"
                async with DiscordClient(settings.discord_bot_token) as dc:
                    await dc.send_message(req.recipient, text)
                return NotificationResponse(
                    success=True,
                    channel_used="discord",
                    message=f"Notification sent to {req.recipient} via discord",
                )
            except Exception as exc:
                errors.append(f"discord: {exc}")
                logger.warning("[notify] discord failed: %s", exc)

        # ---- No channel succeeded -------------------------------------------
        error_summary = "; ".join(errors) if errors else "no channels are enabled"
        logger.error("[notify] all channels failed: %s", error_summary)
        return NotificationResponse(
            success=False,
            error=error_summary,
            message="Notification could not be delivered on any channel",
        )

    return app
