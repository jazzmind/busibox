"""
Email Client

Handles sending emails via SMTP (primary) or Resend (fallback).
Provides magic-link, welcome, deactivation, reactivation, and generic email templates.

All email sending logic that previously lived in Busibox Portal's email.ts is now here.

Configuration priority:
  1. Config-api (dynamic, fetched on demand via session JWT)
  2. Environment variables (static fallback from Settings)
"""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional, Union

import aiosmtplib

from .config import Settings
from .config_api_client import EmailSettings, get_email_settings, clear_config_cache

logger = logging.getLogger(__name__)


class EmailClient:
    """
    Sends emails via SMTP or Resend based on the bridge settings.

    Priority:
      1. SMTP (if smtp_host, smtp_port, smtp_user are set)
      2. Resend (if resend_api_key is set)
      3. None — logs a warning, does not crash

    Config can come from config-api (dynamic) or env vars (static fallback).
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._provider: Optional[str] = None

    def _effective_settings(self, dynamic: Optional[EmailSettings] = None) -> Union[EmailSettings, Settings]:
        """Return dynamic config if available and has a provider, else static."""
        if dynamic and dynamic.provider != "none":
            return dynamic
        return self.settings

    @property
    def provider(self) -> str:
        """Detect the active provider from static settings (for health check)."""
        s = self.settings
        if s.smtp_enabled and s.smtp_host and s.smtp_port and s.smtp_user:
            return "smtp"
        if s.resend_enabled and s.resend_api_key:
            return "resend"
        return "none"

    def _get_provider(self, s: Union[EmailSettings, Settings]) -> str:
        if isinstance(s, EmailSettings):
            return s.provider
        if s.smtp_enabled and s.smtp_host and s.smtp_port and s.smtp_user:
            return "smtp"
        if s.resend_enabled and s.resend_api_key:
            return "resend"
        return "none"

    def _get_from_email(self, s: Union[EmailSettings, Settings]) -> str:
        return s.email_from or "Portal <noreply@email.com>"

    async def _resolve_settings(self, session_jwt: Optional[str] = None) -> Union[EmailSettings, Settings]:
        """Resolve email settings: try config-api first, fall back to env vars.

        The session_jwt here is actually a config-api scoped token (minted by
        authz with scope=config.email.read). It's passed directly to config-api
        without any token exchange.
        """
        if session_jwt:
            try:
                dynamic = await get_email_settings(
                    config_token=session_jwt,
                    config_api_url=self.settings.config_api_url or "http://config-api:8012",
                )
                if dynamic and dynamic.provider != "none":
                    logger.debug(f"[EMAIL] Using config-api settings (provider={dynamic.provider})")
                    return dynamic
            except Exception as exc:
                logger.warning(f"[EMAIL] Config-api lookup failed, using env vars: {exc}")
        return self.settings

    # ------------------------------------------------------------------
    # Low-level send
    # ------------------------------------------------------------------

    async def _send_smtp(self, s: Union[EmailSettings, Settings], to: str, subject: str, html: str, text: str) -> dict:
        """Send an email via SMTP using aiosmtplib."""
        from_email = self._get_from_email(s)
        msg = MIMEMultipart("alternative")
        msg["From"] = from_email
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        use_tls = s.smtp_secure
        port = s.smtp_port or (465 if use_tls else 587)

        try:
            await aiosmtplib.send(
                msg,
                hostname=s.smtp_host,
                port=port,
                username=s.smtp_user,
                password=s.smtp_password or "",
                use_tls=use_tls,
                start_tls=not use_tls and port != 25,
            )
            logger.info(f"[EMAIL] Sent via SMTP to {to}: {subject}")
            return {"provider": "smtp", "success": True}
        except Exception as exc:
            logger.error(f"[EMAIL] SMTP send failed: {exc}")
            raise

    async def _send_resend(self, s: Union[EmailSettings, Settings], to: str, subject: str, html: str, text: str) -> dict:
        """Send an email via Resend HTTP API."""
        import httpx

        api_key = s.resend_api_key
        if not api_key:
            raise RuntimeError("Resend API key not configured")

        from_email = self._get_from_email(s)
        logger.info(f"[EMAIL] Resend request: from={from_email!r}, to={to!r}")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": from_email,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                    "text": text,
                },
            )
            if not resp.is_success:
                body = resp.text
                logger.error(f"[EMAIL] Resend API error {resp.status_code}: {body}")
                raise RuntimeError(f"Resend API {resp.status_code}: {body}")
            data = resp.json()
            logger.info(f"[EMAIL] Sent via Resend to {to}: {subject} (id={data.get('id')})")
            return {"provider": "resend", "success": True, "id": data.get("id")}

    async def send(self, to: str, subject: str, html: str, text: str, session_jwt: Optional[str] = None) -> dict:
        """Send an email using the best available provider."""
        s = await self._resolve_settings(session_jwt)
        provider = self._get_provider(s)
        if provider == "smtp":
            return await self._send_smtp(s, to, subject, html, text)
        elif provider == "resend":
            return await self._send_resend(s, to, subject, html, text)
        else:
            logger.warning(f"[EMAIL] No provider configured — email NOT sent to {to}: {subject}")
            return {"provider": "none", "success": False, "reason": "No email provider configured"}

    # ------------------------------------------------------------------
    # High-level email methods
    # ------------------------------------------------------------------

    async def send_magic_link(self, to: str, magic_link_url: str, totp_code: str, session_jwt: Optional[str] = None) -> dict:
        """Send a magic-link + TOTP code authentication email."""
        subject = f"Sign in to Busibox Portal - Your code: {totp_code}"
        html = _magic_link_with_code_html(magic_link_url, totp_code)
        text = _magic_link_with_code_text(magic_link_url, totp_code)
        return await self.send(to, subject, html, text, session_jwt=session_jwt)

    async def send_magic_link_simple(self, to: str, magic_link_url: str, session_jwt: Optional[str] = None) -> dict:
        """Send a simple magic-link email (no TOTP code)."""
        subject = "Sign in to Busibox Portal"
        html = _magic_link_html(magic_link_url)
        text = _magic_link_text(magic_link_url)
        return await self.send(to, subject, html, text, session_jwt=session_jwt)

    async def send_welcome(self, to: str, user_name: Optional[str] = None, portal_url: str = "", session_jwt: Optional[str] = None) -> dict:
        """Send a welcome email."""
        subject = "Welcome to Busibox Portal"
        html = _welcome_html(user_name, portal_url)
        text = _welcome_text(user_name, portal_url)
        return await self.send(to, subject, html, text, session_jwt=session_jwt)

    async def send_account_deactivated(self, to: str, session_jwt: Optional[str] = None) -> dict:
        """Send an account deactivation notification."""
        return await self.send(
            to,
            "Your Portal account has been deactivated",
            "<p>Your Busibox Portal account has been deactivated.</p>"
            "<p>If you believe this is an error, please contact your system administrator.</p>",
            "Your Busibox Portal account has been deactivated.\n\n"
            "If you believe this is an error, please contact your system administrator.",
            session_jwt=session_jwt,
        )

    async def send_account_reactivated(self, to: str, portal_url: str = "", session_jwt: Optional[str] = None) -> dict:
        """Send an account reactivation notification."""
        return await self.send(
            to,
            "Your Portal account has been reactivated",
            f'<p>Good news! Your Busibox Portal account has been reactivated.</p>'
            f'<p><a href="{portal_url}">Sign in to the Portal</a></p>',
            f"Good news! Your Busibox Portal account has been reactivated.\n\n"
            f"Sign in to the Portal: {portal_url}",
            session_jwt=session_jwt,
        )

    async def send_test(self, to: str, session_jwt: Optional[str] = None) -> dict:
        """Send a test email to verify configuration."""
        s = await self._resolve_settings(session_jwt)
        provider = self._get_provider(s)
        # Config is cached for regular sends; for test-email, retry once with a
        # forced refresh so UI changes are reflected immediately.
        if provider == "none" and session_jwt:
            clear_config_cache()
            s = await self._resolve_settings(session_jwt)
            provider = self._get_provider(s)
        if provider == "none":
            raise RuntimeError("No email provider configured. Configure SMTP or Resend first.")
        now = datetime.utcnow().isoformat() + "Z"
        return await self.send(
            to,
            "Busibox Portal - Test Email",
            f'<div style="font-family: sans-serif; padding: 20px;">'
            f'<h2 style="color: #1f2937;">Email Configuration Test</h2>'
            f'<p style="color: #4b5563;">This is a test email from your Busibox Portal instance.</p>'
            f'<p style="color: #4b5563;">If you received this message, your email configuration is working correctly.</p>'
            f'<p style="color: #9ca3af; font-size: 12px; margin-top: 20px;">'
            f'Provider: <strong>{provider}</strong> &bull; Sent at: {now}</p></div>',
            f"Email Configuration Test\n\nThis is a test email from your Busibox Portal instance.\n"
            f"If you received this message, your email configuration is working correctly.\n\n"
            f"Provider: {provider}\nSent at: {now}",
            session_jwt=session_jwt,
        )


# ==========================================================================
# HTML / Plain-text templates
# ==========================================================================

def _year() -> int:
    return datetime.utcnow().year


def _magic_link_with_code_html(magic_link_url: str, totp_code: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sign in to Portal</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
  <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 10px 10px 0 0; text-align: center;">
    <h1 style="color: white; margin: 0; font-size: 28px;">Busibox Portal</h1>
  </div>
  <div style="background: #ffffff; padding: 30px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 10px 10px;">
    <h2 style="color: #1f2937; margin-top: 0;">Sign in to your account</h2>
    <p style="color: #4b5563; margin-bottom: 25px;">
      You can sign in using <strong>either</strong> the button below <strong>or</strong> by entering your verification code.
    </p>
    <div style="background: #f3f4f6; border-radius: 8px; padding: 20px; margin: 20px 0; text-align: center;">
      <p style="color: #6b7280; margin: 0 0 10px 0; font-size: 14px;">Your verification code:</p>
      <div style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #1f2937; font-family: 'Monaco', 'Consolas', monospace;">
        {totp_code}
      </div>
      <p style="color: #9ca3af; margin: 10px 0 0 0; font-size: 12px;">Enter this code on any device to sign in</p>
    </div>
    <div style="text-align: center; margin: 20px 0;">
      <span style="color: #9ca3af; font-size: 14px;">&mdash; or &mdash;</span>
    </div>
    <div style="text-align: center; margin: 20px 0;">
      <a href="{magic_link_url}" style="display: inline-block; background: #3b82f6; color: white; padding: 14px 32px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 16px;">Sign In with Link</a>
    </div>
    <p style="color: #6b7280; font-size: 14px; margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb;">
      <strong>Security notice:</strong> Both the link and code expire in <strong>15 minutes</strong> and can only be used once. If you didn't request this email, you can safely ignore it.
    </p>
    <p style="color: #9ca3af; font-size: 12px; margin-top: 20px;">
      If the button doesn't work, copy and paste this URL into your browser:<br>
      <a href="{magic_link_url}" style="color: #3b82f6; word-break: break-all;">{magic_link_url}</a>
    </p>
  </div>
  <div style="text-align: center; margin-top: 20px; color: #9ca3af; font-size: 12px;">
    <p>&copy; {_year()} Busibox Portal. All rights reserved.</p>
  </div>
</body>
</html>"""


def _magic_link_with_code_text(magic_link_url: str, totp_code: str) -> str:
    return f"""Sign in to Busibox Portal

You can sign in using EITHER the link below OR by entering your verification code.

YOUR VERIFICATION CODE: {totp_code}
(Enter this code on any device to sign in)

OR click this link:
{magic_link_url}

SECURITY NOTICE:
Both the link and code expire in 15 minutes and can only be used once.
If you didn't request this email, you can safely ignore it.

---
(c) {_year()} Busibox Portal. All rights reserved."""


def _magic_link_html(magic_link_url: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sign in to Portal</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
  <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 10px 10px 0 0; text-align: center;">
    <h1 style="color: white; margin: 0; font-size: 28px;">Busibox Portal</h1>
  </div>
  <div style="background: #ffffff; padding: 30px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 10px 10px;">
    <h2 style="color: #1f2937; margin-top: 0;">Sign in to your account</h2>
    <p style="color: #4b5563; margin-bottom: 25px;">
      Click the button below to securely sign in to the Busibox Portal. This link will expire in <strong>15 minutes</strong>.
    </p>
    <div style="text-align: center; margin: 30px 0;">
      <a href="{magic_link_url}" style="display: inline-block; background: #3b82f6; color: white; padding: 14px 32px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 16px;">Sign In to Portal</a>
    </div>
    <p style="color: #6b7280; font-size: 14px; margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb;">
      <strong>Security notice:</strong> If you didn't request this email, you can safely ignore it. This link will only work once and expires in 15 minutes.
    </p>
    <p style="color: #9ca3af; font-size: 12px; margin-top: 20px;">
      If the button doesn't work, copy and paste this URL into your browser:<br>
      <a href="{magic_link_url}" style="color: #3b82f6; word-break: break-all;">{magic_link_url}</a>
    </p>
  </div>
  <div style="text-align: center; margin-top: 20px; color: #9ca3af; font-size: 12px;">
    <p>&copy; {_year()} Busibox Portal. All rights reserved.</p>
  </div>
</body>
</html>"""


def _magic_link_text(magic_link_url: str) -> str:
    return f"""Sign in to Busibox Portal

Click the link below to securely sign in to your account. This link will expire in 15 minutes.

{magic_link_url}

SECURITY NOTICE:
If you didn't request this email, you can safely ignore it. This link will only work once and expires in 15 minutes.

---
(c) {_year()} Busibox Portal. All rights reserved."""


def _welcome_html(user_name: Optional[str], portal_url: str) -> str:
    greeting = f"Hi {user_name}" if user_name else "Welcome"
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Welcome to Portal</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
  <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 10px 10px 0 0; text-align: center;">
    <h1 style="color: white; margin: 0; font-size: 28px;">Busibox Portal</h1>
  </div>
  <div style="background: #ffffff; padding: 30px; border: 1px solid #e5e7eb; border-top: none; border-radius: 0 0 10px 10px;">
    <h2 style="color: #1f2937; margin-top: 0;">{greeting}!</h2>
    <p style="color: #4b5563;">Your account has been created and you now have access to the Busibox Portal.</p>
    <p style="color: #4b5563;">The Portal provides secure access to all your internal tools and applications in one centralized location.</p>
    <div style="text-align: center; margin: 30px 0;">
      <a href="{portal_url}" style="display: inline-block; background: #3b82f6; color: white; padding: 14px 32px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 16px;">Access Portal</a>
    </div>
    <p style="color: #6b7280; font-size: 14px; margin-top: 30px;">
      If you have any questions or need assistance, please contact your system administrator.
    </p>
  </div>
  <div style="text-align: center; margin-top: 20px; color: #9ca3af; font-size: 12px;">
    <p>&copy; {_year()} Busibox Portal. All rights reserved.</p>
  </div>
</body>
</html>"""


def _welcome_text(user_name: Optional[str], portal_url: str) -> str:
    greeting = f"Hi {user_name}" if user_name else "Welcome"
    return f"""{greeting}!

Your account has been created and you now have access to the Busibox Portal.

The Portal provides secure access to all your internal tools and applications in one centralized location.

Access the Portal: {portal_url}

If you have any questions or need assistance, please contact your system administrator.

---
(c) {_year()} Busibox Portal. All rights reserved."""
