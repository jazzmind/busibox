"""
Personal memory: a user's own markdown files, readable only by that user.

Layout (paths are fixed so the store, the curator and the Memory page agree):

    profile.md              who they are — role, team, how they describe their work
    preferences.md          how they want the assistant to behave
    topics/<domain>.md      facts about them by domain (equipment, reporting, travel …)
    areas/<name>.md         ongoing involvements: a project, a bid, a rotation
    people/<name>.md        colleagues and contacts, as the user describes them

Each file starts with a small YAML frontmatter block whose ``description``
line is what the model reads (in the file listing) to decide which files to
open. ``profile.md`` and ``preferences.md`` are the *core* and go into every
chat turn; the rest are fetched on demand.

Who can read what — the three layers this module implements or relies on:

1. **Rows.** Every query here filters on ``user_id`` from the caller's JWT
   principal, and every transaction sets ``app.user_id`` so the row-level
   security policy (``docs/developers/user-memory.md``) can enforce the same
   thing inside PostgreSQL once an administrator applies it. There is no
   code path that reads memory for a user other than the principal.
2. **Bytes.** Content is envelope-encrypted through the authz keystore under
   the user's own key (``KeystoreCrypto``): a fresh data key per write,
   wrapped only for ``user_id``. A database dump holds ciphertext. When the
   keystore is unreachable, writes are refused (``memory_encryption_required``)
   rather than stored in clear.
3. **Use.** ``MemoryStore`` needs a ``Principal`` to exist at all; the
   reader (``memory_reader.py``) only ever runs inside that user's chat turn
   and never persists what it loaded. Nothing here logs content.

The store is also the one place the sensitive-data rule is enforced
mechanically: obvious government-id and card numbers are refused on write
whoever the writer is (curator, tool, or the user on the Memory page).
"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, select, text

from app.config.settings import get_settings
from app.db.session import get_session_context
from app.models.domain import ChatSettings, UserMemoryFile
from app.schemas.auth import Principal

logger = logging.getLogger(__name__)

CORE_FILES = ("profile.md", "preferences.md")
_PATH_RE = re.compile(r"^(profile\.md|preferences\.md|(topics|areas|people)/[a-z0-9][a-z0-9-]{0,60}\.md)$")

# Never-stored patterns (identifiable numbers). Deliberately narrow: a
# 16-digit run, a card-like 4x4 grouping, a US SSN shape. Words are the
# curator prompt's job; numbers are cheap to catch here.
_NEVER_STORE = [
    (re.compile(r"\b\d{13,19}\b"), "a card or account number"),
    (re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b"), "a card number"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "a government id number"),
]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
_DESCRIPTION_RE = re.compile(r"^description:\s*(.+)$", re.M)

NEW = "new"


class MemoryError(Exception):
    """Base class; ``code`` is stable for API clients."""

    code = "memory_error"


class PathError(MemoryError):
    code = "invalid_path"


class NotFound(MemoryError):
    code = "not_found"


class VersionConflict(MemoryError):
    code = "version_conflict"

    def __init__(self, message: str, current: Optional["MemoryFile"] = None):
        super().__init__(message)
        self.current = current  # what is there now, so the caller can merge and retry


class LimitExceeded(MemoryError):
    code = "limit_exceeded"


class PrivacyRefused(MemoryError):
    code = "privacy_refused"


class EncryptionUnavailable(MemoryError):
    code = "encryption_unavailable"


@dataclass
class MemoryFileInfo:
    path: str
    description: str
    version: int
    size_bytes: int
    updated_at: datetime

    def as_dict(self) -> Dict[str, object]:
        return {
            "path": self.path, "description": self.description, "version": self.version,
            "size_bytes": self.size_bytes, "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class MemoryFile(MemoryFileInfo):
    content: str

    def as_dict(self) -> Dict[str, object]:
        d = super().as_dict()
        d["content"] = self.content
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def validate_path(path: str) -> str:
    p = (path or "").strip().lstrip("/")
    if not _PATH_RE.match(p):
        raise PathError(
            f"'{path}' is not a memory path. Use profile.md, preferences.md, or "
            "topics/<name>.md, areas/<name>.md, people/<name>.md (lower-case, dashes)."
        )
    return p


def description_of(content: str) -> str:
    """The frontmatter ``description:`` line, else the first line of prose."""
    m = _FRONTMATTER_RE.match(content or "")
    if m:
        d = _DESCRIPTION_RE.search(m.group(1))
        if d:
            return d.group(1).strip().strip("\"'")[:300]
        body = content[m.end():]
    else:
        body = content or ""
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:300]
    return ""


def privacy_check(content: str) -> None:
    for pattern, what in _NEVER_STORE:
        if pattern.search(content or ""):
            raise PrivacyRefused(f"Memory cannot hold {what}; remove it and try again.")


def is_core(path: str) -> bool:
    return path in CORE_FILES


# ---------------------------------------------------------------------------
# Encryption through the authz keystore
# ---------------------------------------------------------------------------


class KeystoreCrypto:
    """Envelope encryption under the user's own key.

    ``POST /keystore/encrypt`` with ``user_id`` only (no roles) wraps the data
    key for that user and nobody else; ``/keystore/decrypt`` only succeeds
    for a bearer token whose subject is that user. The token is the caller's
    own JWT, exchanged for the authz audience when needed — never a service
    credential.
    """

    def __init__(self, principal: Principal, base_url: Optional[str] = None):
        self.principal = principal
        settings = get_settings()
        if base_url is None:
            parsed = urlparse(str(getattr(settings, "auth_token_url", "") or ""))
            base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
        self.base_url = base_url.rstrip("/")
        self._token: Optional[str] = principal.token
        self._exchanged = False

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self._token)

    async def _exchange(self) -> Optional[str]:
        if self._exchanged:
            return None
        self._exchanged = True
        try:
            from app.services.token_service import get_or_exchange_token

            out = await get_or_exchange_token(session=None, principal=self.principal, scopes=[], purpose="authz")
            return out.access_token
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory: authz token exchange failed: %s", exc)
            return None

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            for _attempt in (1, 2):
                resp = await client.post(
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                    json=body,
                )
                if resp.status_code == 401 and not self._exchanged:
                    fresh = await self._exchange()
                    if fresh:
                        self._token = fresh
                        continue
                if resp.status_code != 200:
                    raise EncryptionUnavailable(f"keystore {path} returned {resp.status_code}")
                return resp.json()
        raise EncryptionUnavailable("keystore rejected the caller's token")

    async def encrypt(self, blob_id: uuid.UUID, plaintext: bytes) -> bytes:
        out = await self._post("/keystore/encrypt", {
            "file_id": str(blob_id),
            "content": base64.b64encode(plaintext).decode(),
            "role_ids": [],
            "user_id": self.principal.sub,
        })
        return base64.b64decode(out["encrypted_content"])

    async def decrypt(self, blob_id: uuid.UUID, ciphertext: bytes) -> bytes:
        out = await self._post("/keystore/decrypt", {
            "file_id": str(blob_id),
            "encrypted_content": base64.b64encode(ciphertext).decode(),
        })
        return base64.b64decode(out["content"])

    async def forget_blob(self, blob_id: uuid.UUID) -> None:
        """Drop the wrapped data key of a superseded or deleted blob (best effort)."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                await client.delete(
                    f"{self.base_url}/keystore/file/{blob_id}",
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except Exception:  # noqa: BLE001
            pass


class NoCrypto:
    """Development only (``memory_encryption_required=false``): stores clear text."""

    enabled = False

    async def encrypt(self, blob_id, plaintext: bytes) -> bytes:  # pragma: no cover - trivial
        return plaintext

    async def decrypt(self, blob_id, ciphertext: bytes) -> bytes:  # pragma: no cover - trivial
        return ciphertext

    async def forget_blob(self, blob_id) -> None:  # pragma: no cover - trivial
        return None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MemoryStore:
    """All access to one user's memory. Constructed from a ``Principal`` —
    there is no way to open another user's store."""

    def __init__(self, principal: Principal, *, use_test_db: bool = False, crypto=None):
        if not principal or not principal.sub:
            raise MemoryError("a principal is required")
        self.principal = principal
        self.user_id = principal.sub
        self.use_test_db = use_test_db
        self.settings = get_settings()
        if crypto is None:
            ks = KeystoreCrypto(principal)
            if ks.enabled:
                crypto = ks
            elif getattr(self.settings, "memory_encryption_required", True):
                crypto = None  # writes refused; reads of clear rows still work
            else:
                crypto = NoCrypto()
        self.crypto = crypto

    # -- session ------------------------------------------------------------

    async def _bind(self, session) -> None:
        # For the RLS policy (layer 1). Harmless until the policy exists.
        await session.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": self.user_id})

    # -- encoding -----------------------------------------------------------

    async def _encode(self, content: str) -> tuple:
        raw = content.encode("utf-8")
        blob_id = uuid.uuid4()
        if self.crypto is None:
            raise EncryptionUnavailable(
                "Memory is not available: the encryption keystore cannot be reached, and storing memory in clear is disabled."
            )
        data = await self.crypto.encrypt(blob_id, raw)
        return data, blob_id, bool(getattr(self.crypto, "enabled", False)), len(raw)

    async def _decode(self, row: UserMemoryFile) -> str:
        if not row.is_encrypted:
            return row.content.decode("utf-8", errors="replace")
        if self.crypto is None or not getattr(self.crypto, "enabled", False):
            raise EncryptionUnavailable("Memory is encrypted and the keystore cannot be reached.")
        return (await self.crypto.decrypt(row.blob_id, row.content)).decode("utf-8", errors="replace")

    @staticmethod
    def _info(row: UserMemoryFile) -> MemoryFileInfo:
        return MemoryFileInfo(
            path=row.path, description=row.description or "", version=row.version,
            size_bytes=row.size_bytes, updated_at=row.updated_at,
        )

    # -- reads --------------------------------------------------------------

    async def list(self) -> List[MemoryFileInfo]:
        async with get_session_context(self.use_test_db) as session:
            await self._bind(session)
            rows = (await session.execute(
                select(UserMemoryFile).where(UserMemoryFile.user_id == self.user_id).order_by(UserMemoryFile.path)
            )).scalars().all()
            infos = [self._info(r) for r in rows]
        # Core files first, then the rest alphabetically.
        return sorted(infos, key=lambda i: (0 if is_core(i.path) else 1, i.path))

    async def read(self, path: str) -> MemoryFile:
        path = validate_path(path)
        async with get_session_context(self.use_test_db) as session:
            await self._bind(session)
            row = (await session.execute(
                select(UserMemoryFile).where(UserMemoryFile.user_id == self.user_id, UserMemoryFile.path == path)
            )).scalar_one_or_none()
            if row is None:
                raise NotFound(f"{path} does not exist")
            content = await self._decode(row)
            return MemoryFile(**self._info(row).__dict__, content=content)

    async def read_many(self, paths: List[str]) -> List[MemoryFile]:
        out: List[MemoryFile] = []
        for p in paths:
            try:
                out.append(await self.read(p))
            except NotFound:
                continue
        return out

    async def export(self) -> Dict[str, str]:
        return {f.path: f.content for f in await self.read_many([i.path for i in await self.list()])}

    # -- writes -------------------------------------------------------------

    def _check_size(self, content: str) -> None:
        cap = int(getattr(self.settings, "memory_max_file_bytes", 8000))
        if len(content.encode("utf-8")) > cap:
            raise LimitExceeded(f"File is over the {cap} byte limit; condense it instead of adding to it.")

    async def write(self, path: str, content: str, if_version: Union[int, str, None] = None) -> MemoryFileInfo:
        """Create or replace a file. ``if_version``: the version last read
        (optimistic concurrency), ``"new"`` for create-only, ``None`` to skip."""
        path = validate_path(path)
        content = (content or "").strip() + "\n"
        privacy_check(content)
        self._check_size(content)
        async with get_session_context(self.use_test_db) as session:
            await self._bind(session)
            row = (await session.execute(
                select(UserMemoryFile).where(UserMemoryFile.user_id == self.user_id, UserMemoryFile.path == path)
            )).scalar_one_or_none()
            if row is None:
                count = len((await session.execute(
                    select(UserMemoryFile.id).where(UserMemoryFile.user_id == self.user_id)
                )).all())
                cap = int(getattr(self.settings, "memory_max_files", 40))
                if count >= cap:
                    raise LimitExceeded(f"Memory already has {cap} files; merge or delete one first.")
                if if_version not in (None, NEW):
                    raise VersionConflict(f"{path} does not exist; pass if_version='new' to create it")
                data, blob_id, enc, size = await self._encode(content)
                row = UserMemoryFile(
                    user_id=self.user_id, path=path, description=description_of(content),
                    content=data, is_encrypted=enc, blob_id=blob_id, version=1, size_bytes=size,
                )
                session.add(row)
                # Read the row back before commit: with the RLS policy in force,
                # a post-commit refresh would run in a new transaction that no
                # longer carries app.user_id.
                await session.flush()
                info = self._info(row)
                await session.commit()
                return info

            if if_version == NEW:
                current = MemoryFile(**self._info(row).__dict__, content=await self._decode(row))
                raise VersionConflict(f"{path} already exists", current=current)
            if if_version is not None and int(if_version) != row.version:
                current = MemoryFile(**self._info(row).__dict__, content=await self._decode(row))
                raise VersionConflict(f"{path} changed since you read it (now v{row.version})", current=current)
            old_blob = row.blob_id
            data, blob_id, enc, size = await self._encode(content)
            row.content, row.blob_id, row.is_encrypted, row.size_bytes = data, blob_id, enc, size
            row.description = description_of(content)
            row.version += 1
            await session.flush()
            info = self._info(row)
            await session.commit()
        if self.crypto is not None and old_blob:
            await self.crypto.forget_blob(old_blob)
        return info

    async def str_replace(self, path: str, old: str, new: str, if_version: Union[int, str, None] = None) -> MemoryFileInfo:
        current = await self.read(path)
        if if_version is not None and int(if_version) != current.version:
            raise VersionConflict(f"{path} changed since you read it (now v{current.version})", current=current)
        n = current.content.count(old)
        if n != 1:
            raise MemoryError(f"old text matches {n} places in {path}; it must match exactly once")
        return await self.write(path, current.content.replace(old, new, 1), if_version=current.version)

    async def append(self, path: str, addition: str, if_version: Union[int, str, None] = None) -> MemoryFileInfo:
        try:
            current = await self.read(path)
        except NotFound:
            return await self.write(path, addition, if_version=NEW)
        if if_version is not None and if_version != NEW and int(if_version) != current.version:
            raise VersionConflict(f"{path} changed since you read it (now v{current.version})", current=current)
        return await self.write(path, current.content.rstrip("\n") + "\n" + addition.strip(), if_version=current.version)

    async def delete(self, path: str) -> bool:
        path = validate_path(path)
        async with get_session_context(self.use_test_db) as session:
            await self._bind(session)
            row = (await session.execute(
                select(UserMemoryFile).where(UserMemoryFile.user_id == self.user_id, UserMemoryFile.path == path)
            )).scalar_one_or_none()
            if row is None:
                return False
            blob = row.blob_id
            await session.delete(row)
            await session.commit()
        if self.crypto is not None:
            await self.crypto.forget_blob(blob)
        return True

    async def delete_all(self) -> int:
        async with get_session_context(self.use_test_db) as session:
            await self._bind(session)
            rows = (await session.execute(
                select(UserMemoryFile).where(UserMemoryFile.user_id == self.user_id)
            )).scalars().all()
            blobs = [r.blob_id for r in rows]
            await session.execute(delete(UserMemoryFile).where(UserMemoryFile.user_id == self.user_id))
            await session.commit()
        if self.crypto is not None:
            for b in blobs:
                await self.crypto.forget_blob(b)
        return len(blobs)


# ---------------------------------------------------------------------------
# Enablement
# ---------------------------------------------------------------------------


async def memory_enabled_for(user_id: str, use_test_db: bool = False) -> bool:
    """Platform switch AND the user's own switch."""
    if not getattr(get_settings(), "memory_enabled", True):
        return False
    async with get_session_context(use_test_db) as session:
        row = (await session.execute(select(ChatSettings).where(ChatSettings.user_id == user_id))).scalar_one_or_none()
        return True if row is None else bool(getattr(row, "memory_enabled", True))
