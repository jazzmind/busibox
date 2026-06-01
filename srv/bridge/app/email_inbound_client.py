"""
Inbound email polling client (IMAP and POP3).
"""

import asyncio
import email
import imaplib
import json
import logging
import poplib
from dataclasses import dataclass
from email.message import Message
from email.utils import parseaddr
from pathlib import Path
from typing import AsyncGenerator, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class InboundEmailMessage:
    message_id: str
    sender_email: str
    subject: str
    body: str
    in_reply_to: str = ""
    references: str = ""


class EmailInboundClient:
    """Simple IMAP polling client for unseen inbound messages."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str = "INBOX",
        use_ssl: bool = True,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.folder = folder
        self.use_ssl = use_ssl

    async def poll_messages(self, interval: float = 30.0) -> AsyncGenerator[InboundEmailMessage, None]:
        while True:
            messages = await asyncio.to_thread(self._fetch_unseen_messages)
            for message in messages:
                yield message
            await asyncio.sleep(interval)

    def _connect(self):
        if self.use_ssl:
            mail = imaplib.IMAP4_SSL(self.host, self.port)
        else:
            mail = imaplib.IMAP4(self.host, self.port)
        mail.login(self.username, self.password)
        mail.select(self.folder)
        return mail

    def _fetch_unseen_messages(self) -> List[InboundEmailMessage]:
        mail = self._connect()
        out: List[InboundEmailMessage] = []
        try:
            status, data = mail.search(None, "(UNSEEN)")
            if status != "OK" or not data or not data[0]:
                return []

            for uid in data[0].split():
                status, msg_data = mail.fetch(uid, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue
                raw = msg_data[0][1]
                parsed = email.message_from_bytes(raw)
                sender_name, sender_email = parseaddr(parsed.get("From", ""))
                subject = parsed.get("Subject", "") or ""
                body = self._extract_body(parsed)
                message_id = parsed.get("Message-ID", uid.decode("utf-8", errors="ignore"))
                in_reply_to = parsed.get("In-Reply-To", "") or ""
                references = parsed.get("References", "") or ""
                out.append(
                    InboundEmailMessage(
                        message_id=message_id,
                        sender_email=sender_email,
                        subject=subject,
                        body=body.strip(),
                        in_reply_to=in_reply_to.strip(),
                        references=references.strip(),
                    )
                )

                # Mark as seen explicitly after parsing.
                mail.store(uid, "+FLAGS", "\\Seen")
        finally:
            try:
                mail.close()
            except Exception:
                pass
            mail.logout()
        return out

    def _extract_body(self, msg: Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                content_type = (part.get_content_type() or "").lower()
                disposition = (part.get("Content-Disposition") or "").lower()
                if content_type == "text/plain" and "attachment" not in disposition:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    if payload:
                        return payload.decode(charset, errors="replace")
            return ""
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        if payload:
            return payload.decode(charset, errors="replace")
        return ""


def _extract_body_from_message(msg: Message) -> str:
    """Shared body extraction used by both IMAP and POP3 clients."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = (part.get_content_type() or "").lower()
            disposition = (part.get("Content-Disposition") or "").lower()
            if content_type == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                if payload:
                    return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    charset = msg.get_content_charset() or "utf-8"
    if payload:
        return payload.decode(charset, errors="replace")
    return ""


class POP3InboundClient:
    """POP3 polling client that tracks processed messages locally.

    Messages are never deleted from the server (no DELE). UIDL-based
    deduplication ensures each message is processed exactly once, even
    across restarts (via a local state file).
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        use_ssl: bool = True,
        state_file: str = "/tmp/bridge_pop3_seen.json",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.state_file = Path(state_file)
        self._seen_uids: Set[str] = self._load_seen_uids()

    def _load_seen_uids(self) -> Set[str]:
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text())
                if isinstance(data, list):
                    return set(data)
        except Exception as exc:
            logger.warning("POP3: could not load seen UIDs from %s: %s", self.state_file, exc)
        return set()

    def _save_seen_uids(self) -> None:
        try:
            self.state_file.write_text(json.dumps(sorted(self._seen_uids)))
        except Exception as exc:
            logger.warning("POP3: could not save seen UIDs to %s: %s", self.state_file, exc)

    def _connect(self):
        if self.use_ssl:
            conn = poplib.POP3_SSL(self.host, self.port)
        else:
            conn = poplib.POP3(self.host, self.port)
        conn.user(self.username)
        conn.pass_(self.password)
        return conn

    def _fetch_new_messages(self) -> List[InboundEmailMessage]:
        conn = self._connect()
        out: List[InboundEmailMessage] = []
        try:
            # UIDL returns lines like b"+OK 1 unique-id-string"
            resp, uidl_lines, _ = conn.uidl()
            new_indices: List[tuple] = []  # (msg_number_str, uid_str)
            for line in uidl_lines:
                parts = line.decode("utf-8", errors="ignore").strip().split(None, 1)
                if len(parts) == 2:
                    msg_num, uid = parts
                    if uid not in self._seen_uids:
                        new_indices.append((msg_num, uid))

            if not new_indices:
                return []

            newly_seen: List[str] = []
            for msg_num, uid in new_indices:
                try:
                    # RETR fetches the full message; POP3 doesn't support header-only fetch
                    resp2, raw_lines, _ = conn.retr(int(msg_num))
                    raw = b"\r\n".join(raw_lines)
                    parsed = email.message_from_bytes(raw)
                    _, sender_email = parseaddr(parsed.get("From", ""))
                    subject = parsed.get("Subject", "") or ""
                    body = _extract_body_from_message(parsed)
                    message_id = parsed.get("Message-ID", uid)
                    in_reply_to = parsed.get("In-Reply-To", "") or ""
                    references = parsed.get("References", "") or ""
                    out.append(
                        InboundEmailMessage(
                            message_id=message_id,
                            sender_email=sender_email,
                            subject=subject,
                            body=body.strip(),
                            in_reply_to=in_reply_to.strip(),
                            references=references.strip(),
                        )
                    )
                    newly_seen.append(uid)
                except Exception as exc:
                    logger.warning("POP3: error fetching message %s (uid %s): %s", msg_num, uid, exc)

            self._seen_uids.update(newly_seen)
            if newly_seen:
                self._save_seen_uids()
        finally:
            try:
                conn.quit()
            except Exception:
                pass
        return out

    async def poll_messages(self, interval: float = 30.0) -> AsyncGenerator[InboundEmailMessage, None]:
        while True:
            messages = await asyncio.to_thread(self._fetch_new_messages)
            for message in messages:
                yield message
            await asyncio.sleep(interval)
