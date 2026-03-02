"""
Inbound email polling client (IMAP).
"""

import asyncio
import email
import imaplib
from dataclasses import dataclass
from email.message import Message
from email.utils import parseaddr
from typing import AsyncGenerator, List, Optional


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
