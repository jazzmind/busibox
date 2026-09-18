"""Personal memory store (services/user_memory.py).

Pure tests cover path rules, descriptions and the never-store guard. The
store tests run against the test database with the authz keystore replaced
by an in-process fake that behaves like it (fresh key per blob, decrypt only
for the owner). Run with::

    make test-docker SERVICE=agent ARGS="tests/unit/test_user_memory.py"
"""

from __future__ import annotations

import types
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Optional, Tuple

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.domain import UserMemoryFile
from app.schemas.auth import Principal
from app.services import user_memory as um


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["profile.md", "preferences.md", "topics/reporting.md", "areas/boston-harbor-bid.md", "people/maria.md", "/topics/x.md"])
def test_valid_paths(path):
    assert um.validate_path(path) == path.lstrip("/")


@pytest.mark.parametrize("path", ["", "notes.md", "topics/Reporting.md", "topics/../profile.md", "topics/a b.md", "secrets/x.md", "topics/x.txt", "topics/.md"])
def test_invalid_paths(path):
    with pytest.raises(um.PathError):
        um.validate_path(path)


def test_description_from_frontmatter_else_first_prose_line():
    assert um.description_of("---\ndescription: Who I am\n---\n- x\n") == "Who I am"
    assert um.description_of('---\ndescription: "quoted"\n---\n') == "quoted"
    assert um.description_of("# Heading\n\n- prefers short answers\n") == "Heading"
    assert um.description_of("\n\n- first fact\n") == "first fact"
    assert um.description_of("") == ""


def test_privacy_check_refuses_identifiable_numbers():
    for bad in ("card 4111111111111111", "4111 1111 1111 1111", "ssn 123-45-6789"):
        with pytest.raises(um.PrivacyRefused):
            um.privacy_check(bad)
    um.privacy_check("crew of 12, 2026-09-15, job 4521, phone 617-555-0100")  # fine


# ---------------------------------------------------------------------------
# Fake keystore
# ---------------------------------------------------------------------------


class FakeKeystore:
    """Same contract as KeystoreCrypto, keyed by (blob_id) with owner check."""

    enabled = True

    def __init__(self, owner: str):
        self.owner = owner
        self.keys: Dict[str, Tuple[str, bytes]] = {}  # blob_id -> (owner, key)
        self.forgotten = []

    @staticmethod
    def _xor(data: bytes, key: bytes) -> bytes:
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

    async def encrypt(self, blob_id, plaintext: bytes) -> bytes:
        key = uuid.uuid4().bytes
        self.keys[str(blob_id)] = (self.owner, key)
        return b"ENC:" + self._xor(plaintext, key)

    async def decrypt(self, blob_id, ciphertext: bytes) -> bytes:
        owner, key = self.keys[str(blob_id)]
        if owner != self.owner:
            raise um.EncryptionUnavailable("no access")
        assert ciphertext.startswith(b"ENC:")
        return self._xor(ciphertext[4:], key)

    async def forget_blob(self, blob_id) -> None:
        self.forgotten.append(str(blob_id))
        self.keys.pop(str(blob_id), None)


@pytest.fixture
async def db(session_engine, monkeypatch):
    factory = async_sessionmaker(session_engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _ctx(use_test_db: bool = False):
        async with factory() as session:
            try:
                yield session
            finally:
                await session.close()

    monkeypatch.setattr(um, "get_session_context", _ctx)
    monkeypatch.setattr(um, "get_settings", lambda: types.SimpleNamespace(
        memory_enabled=True, memory_encryption_required=True, memory_max_files=3, memory_max_file_bytes=300,
        auth_token_url="http://authz.test/oauth/token",
    ))
    users = []

    def make(user_id: Optional[str] = None):
        uid = user_id or f"mem-test-{uuid.uuid4().hex[:8]}"
        users.append(uid)
        principal = Principal(sub=uid, email=f"{uid}@example.com", token="jwt")
        ks = FakeKeystore(uid)
        return um.MemoryStore(principal, crypto=ks), ks, factory

    try:
        yield make
    finally:
        async with factory() as s:
            for uid in users:
                await s.execute(delete(UserMemoryFile).where(UserMemoryFile.user_id == uid))
            await s.commit()


PROFILE = "---\ndescription: Who I am\n---\n- Project engineer on the dredging side\n"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


async def test_write_read_list_roundtrip_is_encrypted_at_rest(db):
    store, ks, factory = db()
    info = await store.write("profile.md", PROFILE, if_version="new")
    assert info.path == "profile.md" and info.version == 1 and info.description == "Who I am"

    f = await store.read("profile.md")
    assert f.content == PROFILE and f.version == 1

    # The row holds ciphertext, not the text, and knows which key to ask for.
    async with factory() as s:
        row = (await s.execute(select(UserMemoryFile).where(UserMemoryFile.user_id == store.user_id))).scalar_one()
    assert row.is_encrypted and row.content.startswith(b"ENC:") and b"dredging" not in row.content
    assert str(row.blob_id) in ks.keys and row.description == "Who I am"

    listing = await store.list()
    assert [i.path for i in listing] == ["profile.md"]


async def test_core_files_list_first_then_alphabetical(db):
    store, _, _ = db()
    await store.write("topics/reporting.md", "- weekly", if_version="new")
    await store.write("areas/bid.md", "- bid", if_version="new")
    await store.write("preferences.md", "- short answers", if_version="new")
    assert [i.path for i in await store.list()] == ["preferences.md", "areas/bid.md", "topics/reporting.md"]


async def test_versions_conflict_and_old_keys_are_forgotten(db):
    store, ks, _ = db()
    v1 = await store.write("profile.md", PROFILE, if_version="new")
    first_blob = list(ks.keys)[0]
    v2 = await store.write("profile.md", PROFILE + "- Based in Quincy\n", if_version=v1.version)
    assert v2.version == 2
    assert ks.forgotten == [first_blob]  # superseded ciphertext key dropped

    with pytest.raises(um.VersionConflict) as exc:
        await store.write("profile.md", "stale", if_version=1)
    assert exc.value.current is not None and exc.value.current.version == 2
    with pytest.raises(um.VersionConflict):
        await store.write("profile.md", "again", if_version="new")
    with pytest.raises(um.VersionConflict):
        await store.write("topics/new.md", "x", if_version=7)  # does not exist, numbered version


async def test_str_replace_requires_exactly_one_match(db):
    store, _, _ = db()
    await store.write("preferences.md", "- likes tables\n- likes tables\n- brief\n", if_version="new")
    with pytest.raises(um.MemoryError):
        await store.str_replace("preferences.md", "likes tables", "prefers tables")
    info = await store.str_replace("preferences.md", "- brief", "- brief, no preamble")
    assert info.version == 2
    assert "- brief, no preamble" in (await store.read("preferences.md")).content


async def test_append_creates_or_extends(db):
    store, _, _ = db()
    await store.append("topics/general.md", "- uses metric units")
    await store.append("topics/general.md", "- prefers PDF exports")
    assert (await store.read("topics/general.md")).content == "- uses metric units\n- prefers PDF exports\n"


async def test_delete_and_delete_all_drop_rows_and_keys(db):
    store, ks, _ = db()
    await store.write("profile.md", PROFILE, if_version="new")
    await store.write("topics/a.md", "- a", if_version="new")
    assert await store.delete("topics/a.md") is True
    assert await store.delete("topics/a.md") is False
    assert [i.path for i in await store.list()] == ["profile.md"]
    assert await store.delete_all() == 1
    assert await store.list() == [] and ks.keys == {}


async def test_limits_and_privacy_are_enforced_on_write(db):
    store, _, _ = db()
    for i in range(3):
        await store.write(f"topics/t{i}.md", f"- {i}", if_version="new")
    with pytest.raises(um.LimitExceeded):
        await store.write("topics/t9.md", "- over", if_version="new")
    with pytest.raises(um.LimitExceeded):
        await store.write("topics/t0.md", "x" * 400)
    with pytest.raises(um.PrivacyRefused):
        await store.write("topics/t0.md", "- card 4111 1111 1111 1111")
    assert (await store.read("topics/t0.md")).content == "- 0\n"  # untouched


async def test_users_cannot_see_each_other(db):
    alice, _, _ = db()
    bob, _, _ = db()
    await alice.write("profile.md", PROFILE, if_version="new")
    assert await bob.list() == []
    with pytest.raises(um.NotFound):
        await bob.read("profile.md")
    assert await bob.delete("profile.md") is False
    assert (await alice.read("profile.md")).content == PROFILE


async def test_no_keystore_refuses_to_store_in_clear(db, monkeypatch):
    store, _, _ = db()
    store.crypto = None  # what MemoryStore does when the keystore is unreachable and encryption is required
    with pytest.raises(um.EncryptionUnavailable):
        await store.write("profile.md", PROFILE, if_version="new")
    assert await store.list() == []


async def test_memory_enabled_for_honours_platform_and_user_switch(db, monkeypatch):
    from app.models.domain import ChatSettings

    store, _, factory = db()
    assert await um.memory_enabled_for(store.user_id) is True  # no settings row → default on
    async with factory() as s:
        s.add(ChatSettings(user_id=store.user_id, memory_enabled=False))
        await s.commit()
    try:
        assert await um.memory_enabled_for(store.user_id) is False
        monkeypatch.setattr(um, "get_settings", lambda: types.SimpleNamespace(memory_enabled=False))
        assert await um.memory_enabled_for("anyone") is False
    finally:
        async with factory() as s:
            await s.execute(delete(ChatSettings).where(ChatSettings.user_id == store.user_id))
            await s.commit()
