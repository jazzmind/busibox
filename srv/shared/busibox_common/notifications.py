"""
Bridge Notification Client — shared across all Busibox services.

Provides a thin wrapper around the bridge-api ``POST /api/v1/notify``
endpoint so any service (or custom app) can send notifications without
knowing which delivery channel the user prefers.

Usage (async, inside a FastAPI/asyncio service):

    from busibox_common.notifications import BridgeNotificationClient

    client = BridgeNotificationClient(bridge_api_url="http://bridge-api:8081")
    result = await client.notify(
        recipient="user@example.com",
        subject="Daily Summary",
        body="Here is your summary...",
        app_id="vessel-tracking",
        notification_type="daily_summary",
    )

Usage (sync, inside a cron script):

    from busibox_common.notifications import BridgeNotificationClient

    client = BridgeNotificationClient(bridge_api_url="http://bridge-api:8081")
    result = client.notify_sync(
        recipient="user@example.com",
        subject="Daily Summary",
        body="Here is your summary...",
    )
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class NotificationResult:
    """Result returned by BridgeNotificationClient.notify / notify_sync."""

    def __init__(
        self,
        success: bool,
        channel_used: str = "",
        provider: str = "",
        message: str = "",
        error: Optional[str] = None,
    ) -> None:
        self.success = success
        self.channel_used = channel_used
        self.provider = provider
        self.message = message
        self.error = error

    def __repr__(self) -> str:  # pragma: no cover
        if self.success:
            return f"<NotificationResult ok channel={self.channel_used!r}>"
        return f"<NotificationResult failed error={self.error!r}>"


def _build_payload(
    recipient: str,
    subject: str,
    body: str,
    app_id: str = "",
    notification_type: str = "generic",
    priority: str = "normal",
    metadata: Optional[Dict[str, Any]] = None,
) -> dict:
    return {
        "app_id": app_id,
        "notification_type": notification_type,
        "recipient": recipient,
        "subject": subject,
        "body": body,
        "priority": priority,
        "metadata": metadata or {},
    }


class BridgeNotificationClient:
    """Send notifications via bridge-api.

    The client reads ``bridge_api_url`` from the constructor argument or the
    ``BRIDGE_API_URL`` environment variable.  If neither is set, every call
    returns a failed ``NotificationResult`` with an explanatory error rather
    than raising an exception, so cron scripts don't crash on missing config.
    """

    def __init__(self, bridge_api_url: Optional[str] = None, timeout: float = 30.0) -> None:
        self.bridge_api_url = (
            (bridge_api_url or os.environ.get("BRIDGE_API_URL", "")).rstrip("/")
        )
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Async interface (for FastAPI / asyncio services)
    # ------------------------------------------------------------------

    async def notify(
        self,
        recipient: str,
        subject: str,
        body: str,
        app_id: str = "",
        notification_type: str = "generic",
        priority: str = "normal",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationResult:
        """Send a notification asynchronously via bridge-api.

        Returns ``NotificationResult`` — never raises.
        """
        if not self.bridge_api_url:
            return NotificationResult(
                success=False,
                error="BRIDGE_API_URL is not configured",
            )

        try:
            import httpx  # optional — available wherever fastapi is installed
        except ImportError:
            return NotificationResult(
                success=False,
                error="httpx is not installed; add it to requirements.txt",
            )

        url = f"{self.bridge_api_url}/api/v1/notify"
        payload = _build_payload(
            recipient=recipient,
            subject=subject,
            body=body,
            app_id=app_id,
            notification_type=notification_type,
            priority=priority,
            metadata=metadata,
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return NotificationResult(
                success=data.get("success", False),
                channel_used=data.get("channel_used", ""),
                provider=data.get("provider", ""),
                message=data.get("message", ""),
                error=data.get("error"),
            )
        except Exception as exc:
            logger.error("[BridgeNotificationClient] notify failed: %s", exc)
            return NotificationResult(success=False, error=str(exc))

    # ------------------------------------------------------------------
    # Sync interface (for cron scripts, CLI tools)
    # ------------------------------------------------------------------

    def notify_sync(
        self,
        recipient: str,
        subject: str,
        body: str,
        app_id: str = "",
        notification_type: str = "generic",
        priority: str = "normal",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationResult:
        """Send a notification synchronously via bridge-api.

        Uses ``httpx`` if available, falls back to ``urllib`` from the stdlib
        so the client works even in environments where only the standard library
        is present (e.g. minimal Docker images without httpx).

        Returns ``NotificationResult`` — never raises.
        """
        if not self.bridge_api_url:
            return NotificationResult(
                success=False,
                error="BRIDGE_API_URL is not configured",
            )

        url = f"{self.bridge_api_url}/api/v1/notify"
        payload = _build_payload(
            recipient=recipient,
            subject=subject,
            body=body,
            app_id=app_id,
            notification_type=notification_type,
            priority=priority,
            metadata=metadata,
        )

        # Try httpx first (preferred — better error messages, timeout support)
        try:
            import httpx

            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return NotificationResult(
                success=data.get("success", False),
                channel_used=data.get("channel_used", ""),
                provider=data.get("provider", ""),
                message=data.get("message", ""),
                error=data.get("error"),
            )
        except ImportError:
            pass  # fall through to urllib
        except Exception as exc:
            logger.error("[BridgeNotificationClient] notify_sync (httpx) failed: %s", exc)
            return NotificationResult(success=False, error=str(exc))

        # Fallback: urllib (stdlib only)
        try:
            import json
            import urllib.request

            encoded = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=encoded,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=int(self.timeout)) as resp:  # type: ignore[assignment]
                raw = resp.read()
            data = json.loads(raw)
            return NotificationResult(
                success=data.get("success", False),
                channel_used=data.get("channel_used", ""),
                provider=data.get("provider", ""),
                message=data.get("message", ""),
                error=data.get("error"),
            )
        except Exception as exc:
            logger.error("[BridgeNotificationClient] notify_sync (urllib) failed: %s", exc)
            return NotificationResult(success=False, error=str(exc))


def get_notification_client(bridge_api_url: Optional[str] = None) -> BridgeNotificationClient:
    """Convenience factory that reads BRIDGE_API_URL from env if not supplied."""
    return BridgeNotificationClient(bridge_api_url=bridge_api_url)
