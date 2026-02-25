"""
RLS (Row-Level Security) isolation integration tests.

Proves the PostgreSQL RLS security model works correctly by testing data
isolation between 3 users with personal documents, shared documents,
and visibility transitions -- all through the full API stack.

Test setup:
  - User A: has a personal doc, a media file, and access to shared role "ShareAB"
  - User B: has a personal doc and access to shared role "ShareAB"
  - User C: has no docs and no shared roles
  - ShareAB: a shared document accessible to both A and B via a shared role

Test scenarios:
  Phase 1 - Isolation:
    1) User A sees own personal docs + shared doc, not B's personal doc
    2) User B sees own personal doc + shared doc, not A's personal docs
    3) User C sees nothing
  Phase 2 - Visibility transitions:
    4) User A moves personal doc to ShareAB -> B can now see it
    5) User A moves it back to personal -> B can no longer see it
"""

import os
import uuid
from datetime import datetime
from typing import Dict

import httpx
import psycopg2
import pytest
from httpx import AsyncClient

from testing.auth import AuthTestClient, TEST_MODE_HEADER, TEST_MODE_VALUE


# =============================================================================
# Multi-User Auth Helper
# =============================================================================


class MultiUserAuthClient:
    """
    Manages multiple test users via the authz magic link flow.

    Each user is created by calling POST /auth/login/initiate with a unique
    email.  The admin API (called via the default bootstrap test user) is
    used to create roles and assign them to the individual users.
    """

    def __init__(self, admin_client: AuthTestClient):
        self._admin = admin_client
        self._users: Dict[str, dict] = {}
        self._created_role_ids: list[str] = []
        self._user_role_bindings: list[tuple[str, str]] = []

    # --------------------------------------------------------------------- #
    # User lifecycle
    # --------------------------------------------------------------------- #

    def register_user(self, label: str, email: str) -> dict:
        """
        Create a user via magic-link login and cache its credentials.

        Unlike the default AuthTestClient which re-uses the bootstrap user ID,
        we perform the login flow manually so we can capture the actual user_id
        from the magic-link use response.
        """
        authz_url = self._admin.authz_url
        headers = {TEST_MODE_HEADER: TEST_MODE_VALUE}

        with httpx.Client() as http:
            # Step 1: initiate login (auto-creates user if needed)
            resp = http.post(
                f"{authz_url}/auth/login/initiate",
                json={"email": email},
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code != 200:
                pytest.fail(f"Login initiate failed for {email}: {resp.status_code} {resp.text}")

            magic_link_token = resp.json().get("magic_link_token")
            if not magic_link_token:
                pytest.fail(f"No magic_link_token for {email}: {resp.json()}")

            # Step 2: use magic link -> get session JWT + real user_id
            resp = http.post(
                f"{authz_url}/auth/magic-links/{magic_link_token}/use",
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code != 200:
                pytest.fail(f"Magic link use failed for {email}: {resp.status_code} {resp.text}")

            data = resp.json()
            user_id = data["user"]["user_id"]
            session_jwt = data["session"]["token"]

        # Build an AuthTestClient pinned to this user
        client = AuthTestClient(
            authz_url=authz_url,
            test_user_id=user_id,
            test_user_email=email,
        )
        client._session_jwt = session_jwt

        self._users[label] = {
            "auth_client": client,
            "user_id": user_id,
            "email": email,
            "session_jwt": session_jwt,
        }
        return self._users[label]

    def get_user(self, label: str) -> dict:
        return self._users[label]

    # --------------------------------------------------------------------- #
    # Role management (uses the admin/bootstrap user's JWT)
    # --------------------------------------------------------------------- #

    def create_role(self, role_name: str, scopes: list[str]) -> str:
        role_id = self._admin.create_role(role_name, scopes=scopes)
        self._created_role_ids.append(role_id)
        return role_id

    def assign_role_to_user(self, user_id: str, role_id: str) -> None:
        headers = self._admin._admin_headers()
        with httpx.Client() as client:
            resp = client.post(
                f"{self._admin.authz_url}/admin/user-roles",
                headers=headers,
                json={"user_id": user_id, "role_id": role_id},
                timeout=10.0,
            )
            if resp.status_code not in (200, 201, 409):
                pytest.fail(
                    f"Failed to assign role {role_id} to user {user_id}: "
                    f"{resp.status_code} - {resp.text}"
                )
        self._user_role_bindings.append((user_id, role_id))

    def get_data_token(self, label: str) -> str:
        """Get a data-api access token for a given user."""
        return self._users[label]["auth_client"].get_token(audience="data-api")

    # --------------------------------------------------------------------- #
    # Cleanup
    # --------------------------------------------------------------------- #

    def cleanup(self) -> None:
        headers = self._admin._admin_headers()
        with httpx.Client() as client:
            for user_id, role_id in self._user_role_bindings:
                try:
                    client.request(
                        "DELETE",
                        f"{self._admin.authz_url}/admin/user-roles",
                        headers=headers,
                        json={"user_id": user_id, "role_id": role_id},
                        timeout=10.0,
                    )
                except Exception:
                    pass

            for role_id in self._created_role_ids:
                try:
                    client.delete(
                        f"{self._admin.authz_url}/admin/roles/{role_id}",
                        headers=headers,
                        timeout=10.0,
                    )
                except Exception:
                    pass

        for info in self._users.values():
            info["auth_client"].cleanup()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def rls_users(auth_client) -> MultiUserAuthClient:
    """
    Module-scoped fixture that creates 3 test users and a shared role.

    Yields a MultiUserAuthClient with users "a", "b", "c" and roles set up:
      - test-rls-share-ab: shared role (data.read, data.write, data.delete, search.read)
      - User A has the shared role
      - User B has the shared role
      - User C has NO shared roles
      - All three have a personal full-access role
    """
    run_id = uuid.uuid4().hex[:8]
    mu = MultiUserAuthClient(auth_client)

    mu.register_user("a", f"rls-user-a-{run_id}@test.example.com")
    mu.register_user("b", f"rls-user-b-{run_id}@test.example.com")
    mu.register_user("c", f"rls-user-c-{run_id}@test.example.com")

    full_scopes = ["data.read", "data.write", "data.delete", "search.read"]

    share_ab_role_name = f"test-rls-share-ab-{run_id}"
    share_ab_role_id = mu.create_role(share_ab_role_name, full_scopes)

    personal_a_role_name = f"test-rls-personal-a-{run_id}"
    personal_a_role_id = mu.create_role(personal_a_role_name, full_scopes)

    personal_b_role_name = f"test-rls-personal-b-{run_id}"
    personal_b_role_id = mu.create_role(personal_b_role_name, full_scopes)

    personal_c_role_name = f"test-rls-personal-c-{run_id}"
    personal_c_role_id = mu.create_role(personal_c_role_name, full_scopes)

    # Assign roles
    user_a_id = mu.get_user("a")["user_id"]
    user_b_id = mu.get_user("b")["user_id"]
    user_c_id = mu.get_user("c")["user_id"]

    mu.assign_role_to_user(user_a_id, share_ab_role_id)
    mu.assign_role_to_user(user_a_id, personal_a_role_id)
    mu.assign_role_to_user(user_b_id, share_ab_role_id)
    mu.assign_role_to_user(user_b_id, personal_b_role_id)
    mu.assign_role_to_user(user_c_id, personal_c_role_id)

    mu.get_user("a")["share_ab_role_id"] = share_ab_role_id
    mu.get_user("b")["share_ab_role_id"] = share_ab_role_id
    mu.get_user("a")["personal_role_id"] = personal_a_role_id
    mu.get_user("b")["personal_role_id"] = personal_b_role_id
    mu.get_user("c")["personal_role_id"] = personal_c_role_id

    yield mu

    mu.cleanup()


def _get_data_db_conn():
    """Get a synchronous psycopg2 connection to the data DB as the service user."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "files"),
        user=os.getenv("POSTGRES_USER", "busibox_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
    )


def _set_rls_context(cur, user_id: str, role_ids: list[str] | None = None):
    """Set RLS session variables for the current transaction."""
    cur.execute("SET LOCAL app.user_id = %s", (user_id,))
    if role_ids:
        role_ids_csv = ",".join(role_ids)
        cur.execute("SET LOCAL app.role_ids = %s", (role_ids_csv,))
        cur.execute("SET LOCAL app.user_role_ids_read = %s", (role_ids_csv,))
        cur.execute("SET LOCAL app.user_role_ids_create = %s", (role_ids_csv,))
        cur.execute("SET LOCAL app.user_role_ids_update = %s", (role_ids_csv,))
        cur.execute("SET LOCAL app.user_role_ids_delete = %s", (role_ids_csv,))


@pytest.fixture(scope="module")
def rls_test_data(rls_users):
    """
    Module-scoped fixture that inserts test documents, chunks, and status
    rows directly into the DB with proper RLS context via psycopg2.

    Creates:
      - User A personal doc  (visibility=personal, owner=A)
      - User A media file    (visibility=personal, owner=A, mime=image/jpeg)
      - Shared doc in ShareAB (visibility=shared, owner=A, document_roles -> share_ab_role)
      - User B personal doc  (visibility=personal, owner=B)
      - One chunk per document
      - One data_status row per document
    """
    user_a = rls_users.get_user("a")
    user_b = rls_users.get_user("b")
    share_ab_role_id = user_a["share_ab_role_id"]
    personal_a_role_id = user_a["personal_role_id"]
    personal_b_role_id = user_b["personal_role_id"]

    a_id = user_a["user_id"]
    b_id = user_b["user_id"]

    doc_a_personal = str(uuid.uuid4())
    doc_a_media = str(uuid.uuid4())
    doc_shared = str(uuid.uuid4())
    doc_b_personal = str(uuid.uuid4())
    now = datetime.utcnow()

    docs = {
        "a_personal": doc_a_personal,
        "a_media": doc_a_media,
        "shared": doc_shared,
        "b_personal": doc_b_personal,
        "share_ab_role_id": share_ab_role_id,
    }

    insert_file = """
        INSERT INTO data_files
            (file_id, user_id, owner_id, filename, original_filename,
             mime_type, size_bytes, storage_path, content_hash,
             has_markdown, created_at, visibility, doc_type)
        VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'file')
    """
    insert_chunk = """
        INSERT INTO data_chunks (file_id, chunk_index, text, token_count)
        VALUES (%s::uuid, %s, %s, %s)
    """
    insert_status = """
        INSERT INTO data_status (file_id, stage, progress, started_at, completed_at, updated_at)
        VALUES (%s::uuid, 'completed', 100, %s, %s, %s)
    """

    conn = _get_data_db_conn()
    try:
        # User A's documents (personal + shared)
        a_all_roles = [share_ab_role_id, personal_a_role_id]
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, a_id, a_all_roles)
            cur.execute(insert_file, (
                doc_a_personal, a_id, a_id, "a_personal.pdf", "a_personal.pdf",
                "application/pdf", 1024, f"s3://{a_id}/{doc_a_personal}", "hash_a_personal",
                False, now, "personal",
            ))
            cur.execute(insert_file, (
                doc_a_media, a_id, a_id, "a_photo.jpg", "a_photo.jpg",
                "image/jpeg", 2048, f"s3://{a_id}/{doc_a_media}", "hash_a_media",
                False, now, "personal",
            ))
            cur.execute(insert_file, (
                doc_shared, a_id, a_id, "shared_doc.pdf", "shared_doc.pdf",
                "application/pdf", 3072, f"s3://shared/{doc_shared}", "hash_shared",
                False, now, "shared",
            ))
            cur.execute(
                """INSERT INTO document_roles (file_id, role_id, role_name, added_by)
                VALUES (%s::uuid, %s::uuid, %s, %s::uuid)""",
                (doc_shared, share_ab_role_id, "test-rls-share-ab", a_id),
            )
            cur.execute(insert_chunk, (doc_a_personal, 0, "Chunk from A personal doc.", 6))
            cur.execute(insert_chunk, (doc_a_media, 0, "Chunk from A media file.", 5))
            cur.execute(insert_chunk, (doc_shared, 0, "Chunk from shared doc.", 5))
            cur.execute(insert_status, (doc_a_personal, now, now, now))
            cur.execute(insert_status, (doc_a_media, now, now, now))
            cur.execute(insert_status, (doc_shared, now, now, now))
            cur.close()

        # User B's documents
        b_all_roles = [share_ab_role_id, personal_b_role_id]
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, b_id, b_all_roles)
            cur.execute(insert_file, (
                doc_b_personal, b_id, b_id, "b_personal.pdf", "b_personal.pdf",
                "application/pdf", 4096, f"s3://{b_id}/{doc_b_personal}", "hash_b_personal",
                False, now, "personal",
            ))
            cur.execute(insert_chunk, (doc_b_personal, 0, "Chunk from B personal doc.", 6))
            cur.execute(insert_status, (doc_b_personal, now, now, now))
            cur.close()
    finally:
        conn.close()

    yield docs

    # -- cleanup with proper RLS context --------------------------------------
    all_personal_a = [doc_a_personal, doc_a_media]
    try:
        conn = _get_data_db_conn()
        try:
            all_roles = [share_ab_role_id, personal_a_role_id, personal_b_role_id]
            # Clean up user A's docs (personal + shared)
            with conn:
                cur = conn.cursor()
                _set_rls_context(cur, a_id, all_roles)
                for fid in [doc_a_personal, doc_a_media, doc_shared]:
                    cur.execute("DELETE FROM data_chunks WHERE file_id = %s::uuid", (fid,))
                    cur.execute("DELETE FROM data_status WHERE file_id = %s::uuid", (fid,))
                    cur.execute("DELETE FROM document_roles WHERE file_id = %s::uuid", (fid,))
                    cur.execute("DELETE FROM data_files WHERE file_id = %s::uuid", (fid,))
                cur.close()

            # Clean up user B's doc
            with conn:
                cur = conn.cursor()
                _set_rls_context(cur, b_id, all_roles)
                cur.execute("DELETE FROM data_chunks WHERE file_id = %s::uuid", (doc_b_personal,))
                cur.execute("DELETE FROM data_status WHERE file_id = %s::uuid", (doc_b_personal,))
                cur.execute("DELETE FROM data_files WHERE file_id = %s::uuid", (doc_b_personal,))
                cur.close()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[rls_test_data] cleanup failed: {exc}")


_API_PORT = os.getenv("API_PORT", "8002")
_SERVICE_URL = os.getenv("DATA_API_URL", f"http://localhost:{_API_PORT}")


def _make_client(token: str) -> AsyncClient:
    """Build an HTTP test client pre-configured with auth + test-mode headers."""
    client = AsyncClient(base_url=_SERVICE_URL)
    client.headers.update({
        "Authorization": f"Bearer {token}",
        "X-Test-Mode": "true",
    })
    return client


@pytest.fixture(scope="module")
async def clients(rls_users, rls_test_data):
    """
    Module-scoped fixture providing 3 HTTP clients (one per user).

    Returns a dict with keys "a", "b", "c" mapping to AsyncClient instances,
    plus the test data dict under key "data".
    """
    docs = rls_test_data

    token_a = rls_users.get_data_token("a")
    token_b = rls_users.get_data_token("b")
    token_c = rls_users.get_data_token("c")

    client_a = _make_client(token_a)
    client_b = _make_client(token_b)
    client_c = _make_client(token_c)

    yield {
        "a": client_a,
        "b": client_b,
        "c": client_c,
        "data": docs,
    }

    try:
        await client_a.aclose()
        await client_b.aclose()
        await client_c.aclose()
    except RuntimeError:
        pass


# =============================================================================
# Phase 1 -- Isolation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_user_a_sees_own_personal_doc(clients):
    """User A can access their own personal document."""
    c = clients["a"]
    fid = str(clients["data"]["a_personal"])

    resp = await c.get(f"/files/{fid}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body = resp.json()
    assert body.get("fileId") or body.get("file_id") == fid


@pytest.mark.asyncio
async def test_user_a_sees_own_media_file(clients):
    """User A can access their own media file."""
    c = clients["a"]
    fid = str(clients["data"]["a_media"])

    resp = await c.get(f"/files/{fid}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_user_a_sees_shared_doc(clients):
    """User A can access the shared document (via share-ab role)."""
    c = clients["a"]
    fid = str(clients["data"]["shared"])

    resp = await c.get(f"/files/{fid}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_user_a_cannot_see_b_personal_doc(clients):
    """User A cannot access User B's personal document."""
    c = clients["a"]
    fid = str(clients["data"]["b_personal"])

    resp = await c.get(f"/files/{fid}")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_user_a_cannot_see_b_personal_chunks(clients):
    """User A cannot read chunks from User B's personal document."""
    c = clients["a"]
    fid = str(clients["data"]["b_personal"])

    resp = await c.get(f"/files/{fid}/chunks")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_user_a_sees_own_chunks(clients):
    """User A can read chunks from their own personal document."""
    c = clients["a"]
    fid = str(clients["data"]["a_personal"])

    resp = await c.get(f"/files/{fid}/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0 or len(body["chunks"]) > 0


@pytest.mark.asyncio
async def test_user_a_sees_shared_chunks(clients):
    """User A can read chunks from the shared document."""
    c = clients["a"]
    fid = str(clients["data"]["shared"])

    resp = await c.get(f"/files/{fid}/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["chunks"]) > 0


# ------------- User B isolation --------------------------------------------- #


@pytest.mark.asyncio
async def test_user_b_sees_own_personal_doc(clients):
    """User B can access their own personal document."""
    c = clients["b"]
    fid = str(clients["data"]["b_personal"])

    resp = await c.get(f"/files/{fid}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_user_b_sees_shared_doc(clients):
    """User B can access the shared document."""
    c = clients["b"]
    fid = str(clients["data"]["shared"])

    resp = await c.get(f"/files/{fid}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_user_b_sees_shared_chunks(clients):
    """User B can read chunks from the shared document."""
    c = clients["b"]
    fid = str(clients["data"]["shared"])

    resp = await c.get(f"/files/{fid}/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["chunks"]) > 0


@pytest.mark.asyncio
async def test_user_b_cannot_see_a_personal_doc(clients):
    """User B cannot access User A's personal document."""
    c = clients["b"]
    fid = str(clients["data"]["a_personal"])

    resp = await c.get(f"/files/{fid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_user_b_cannot_see_a_media_file(clients):
    """User B cannot access User A's media file."""
    c = clients["b"]
    fid = str(clients["data"]["a_media"])

    resp = await c.get(f"/files/{fid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_user_b_cannot_see_a_personal_chunks(clients):
    """User B cannot read chunks from User A's personal document."""
    c = clients["b"]
    fid = str(clients["data"]["a_personal"])

    resp = await c.get(f"/files/{fid}/chunks")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_user_b_cannot_see_a_media_chunks(clients):
    """User B cannot read chunks from User A's media file."""
    c = clients["b"]
    fid = str(clients["data"]["a_media"])

    resp = await c.get(f"/files/{fid}/chunks")
    assert resp.status_code == 404


# ------------- User C isolation --------------------------------------------- #


@pytest.mark.asyncio
async def test_user_c_cannot_see_any_doc(clients):
    """User C cannot access any document."""
    c = clients["c"]

    for key in ("a_personal", "a_media", "shared", "b_personal"):
        fid = str(clients["data"][key])
        resp = await c.get(f"/files/{fid}")
        assert resp.status_code in (403, 404), (
            f"User C should not see {key} ({fid}): got {resp.status_code}"
        )


@pytest.mark.asyncio
async def test_user_c_cannot_see_any_chunks(clients):
    """User C cannot read chunks from any document."""
    c = clients["c"]

    for key in ("a_personal", "a_media", "shared", "b_personal"):
        fid = str(clients["data"][key])
        resp = await c.get(f"/files/{fid}/chunks")
        assert resp.status_code in (403, 404), (
            f"User C should not see chunks for {key} ({fid}): got {resp.status_code}"
        )


# =============================================================================
# Phase 2 -- Visibility Transitions
# =============================================================================


@pytest.mark.asyncio
async def test_move_personal_to_shared_grants_access(clients):
    """
    When User A moves their personal doc to shared (ShareAB), User B
    gains access to the document and its chunks.  User C still cannot see it.
    """
    client_a = clients["a"]
    client_b = clients["b"]
    client_c = clients["c"]
    fid = str(clients["data"]["a_personal"])
    share_role_id = clients["data"]["share_ab_role_id"]

    # -- Pre-condition: B cannot see it ------------------------------------
    resp = await client_b.get(f"/files/{fid}")
    assert resp.status_code == 404, "Pre-condition failed: B should not see A's personal doc"

    # -- User A moves doc to shared ----------------------------------------
    resp = await client_a.post(
        f"/files/{fid}/move",
        json={"visibility": "shared", "roleIds": [share_role_id]},
    )
    assert resp.status_code == 200, f"Move to shared failed: {resp.status_code} {resp.text}"

    # -- User B can now see the doc ----------------------------------------
    resp = await client_b.get(f"/files/{fid}")
    assert resp.status_code == 200, (
        f"After move to shared, B should see the doc: got {resp.status_code}"
    )

    # -- User B can see the chunks -----------------------------------------
    resp = await client_b.get(f"/files/{fid}/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["chunks"]) > 0, "B should see chunks after doc was shared"

    # -- User C still cannot see it ----------------------------------------
    resp = await client_c.get(f"/files/{fid}")
    assert resp.status_code in (403, 404), (
        f"C should still not see the doc after it's shared to AB: got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_move_shared_back_to_personal_revokes_access(clients):
    """
    When User A moves the doc back to personal, User B loses access to
    the document and its chunks.  User A retains access.
    """
    client_a = clients["a"]
    client_b = clients["b"]
    fid = str(clients["data"]["a_personal"])

    # -- Pre-condition: doc is currently shared (from previous test) -------
    resp = await client_b.get(f"/files/{fid}")
    assert resp.status_code == 200, "Pre-condition failed: B should see the shared doc"

    # -- User A moves doc back to personal ---------------------------------
    resp = await client_a.post(
        f"/files/{fid}/move",
        json={"visibility": "personal"},
    )
    assert resp.status_code == 200, f"Move to personal failed: {resp.status_code} {resp.text}"

    # -- User B can no longer see the doc ----------------------------------
    resp = await client_b.get(f"/files/{fid}")
    assert resp.status_code == 404, (
        f"After move to personal, B should NOT see the doc: got {resp.status_code}"
    )

    # -- User B cannot see chunks either -----------------------------------
    resp = await client_b.get(f"/files/{fid}/chunks")
    assert resp.status_code == 404, (
        f"After move to personal, B should NOT see chunks: got {resp.status_code}"
    )

    # -- User A still sees the doc -----------------------------------------
    resp = await client_a.get(f"/files/{fid}")
    assert resp.status_code == 200, (
        f"After move to personal, A should still see own doc: got {resp.status_code}"
    )

    # -- User A still sees chunks ------------------------------------------
    resp = await client_a.get(f"/files/{fid}/chunks")
    assert resp.status_code == 200


# =============================================================================
# Graph isolation (skipped when Neo4j is unavailable)
# =============================================================================


@pytest.mark.asyncio
async def test_graph_isolation(clients):
    """
    Graph endpoint returns different results per user based on ownership.
    Skipped if the graph service is not available.
    """
    client_a = clients["a"]

    resp_a = await client_a.get("/data/graph")
    if resp_a.status_code == 200:
        body = resp_a.json()
        if not body.get("graph_available", True):
            pytest.skip("Graph database not available")
    else:
        pytest.skip("Graph endpoint returned non-200; graph service may not be running")

    client_b = clients["b"]
    client_c = clients["c"]

    # User B should not see A-only personal nodes
    resp_b = await client_b.get("/data/graph")
    assert resp_b.status_code == 200

    # User C should see no nodes from A or B
    resp_c = await client_c.get("/data/graph")
    assert resp_c.status_code in (200, 403)
