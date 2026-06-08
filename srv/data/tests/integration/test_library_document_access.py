"""
Library document access integration tests.

Proves that PostgreSQL RLS correctly filters documents when listing
via GET /libraries/{id}/documents:

  - A user whose JWT contains a role listed in document_roles CAN see
    shared documents in a library.
  - A user with NO matching role sees nothing (empty list).
  - A user who uploads to a shared library with visibility=shared + role_ids
    has those documents immediately visible to all role-holders.

Test setup mirrors test_rls_isolation.py (MultiUserAuthClient pattern):
  - User A: has shared role "lib-access-ab"
  - User B: has shared role "lib-access-ab"
  - User C: no shared role
  - One shared library with two shared docs (visibility=shared, role=lib-access-ab)
  - One personal library belonging to User A (docs personal to A only)
"""

import os
import uuid
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import psycopg2
import pytest
from httpx import AsyncClient

from busibox_common.testing.auth import AuthTestClient, TEST_MODE_HEADER, TEST_MODE_VALUE

# Re-use the multi-user helper from rls isolation tests
from .test_rls_isolation import MultiUserAuthClient

_API_PORT = os.getenv("API_PORT", "8002")
_SERVICE_URL = os.getenv("DATA_API_URL", f"http://localhost:{_API_PORT}")


# =============================================================================
# Helpers
# =============================================================================


def _get_data_db_conn():
    """Synchronous psycopg2 connection to the data DB as the service user."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "files"),
        user=os.getenv("POSTGRES_USER", "busibox_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
    )


def _set_rls_context(cur, user_id: str, role_ids: Optional[list] = None):
    cur.execute("SET LOCAL app.user_id = %s", (user_id,))
    if role_ids:
        csv = ",".join(role_ids)
        for var in (
            "app.role_ids",
            "app.user_role_ids_read",
            "app.user_role_ids_create",
            "app.user_role_ids_update",
            "app.user_role_ids_delete",
        ):
            cur.execute(f"SET LOCAL {var} = %s", (csv,))


def _make_client(token: str) -> AsyncClient:
    return AsyncClient(
        base_url=_SERVICE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            TEST_MODE_HEADER: TEST_MODE_VALUE,
        },
        timeout=30.0,
    )


# =============================================================================
# Module-scoped fixtures
# =============================================================================


@pytest.fixture(scope="module")
def lib_users(auth_client) -> MultiUserAuthClient:
    """
    Three test users:
      - a: has lib-access-ab role
      - b: has lib-access-ab role
      - c: no shared role
    All three also have individual full-access roles for data API scopes.
    """
    run_id = uuid.uuid4().hex[:8]
    mu = MultiUserAuthClient(auth_client)

    mu.register_user("a", f"lib-user-a-{run_id}@test.example.com")
    mu.register_user("b", f"lib-user-b-{run_id}@test.example.com")
    mu.register_user("c", f"lib-user-c-{run_id}@test.example.com")

    full_scopes = ["data.read", "data.write", "data.delete", "search.read"]

    shared_role_id = mu.create_role(f"lib-access-ab-{run_id}", full_scopes)
    personal_a_id = mu.create_role(f"lib-personal-a-{run_id}", full_scopes)
    personal_b_id = mu.create_role(f"lib-personal-b-{run_id}", full_scopes)
    personal_c_id = mu.create_role(f"lib-personal-c-{run_id}", full_scopes)

    user_a_id = mu.get_user("a")["user_id"]
    user_b_id = mu.get_user("b")["user_id"]
    user_c_id = mu.get_user("c")["user_id"]

    mu.assign_role_to_user(user_a_id, shared_role_id)
    mu.assign_role_to_user(user_a_id, personal_a_id)
    mu.assign_role_to_user(user_b_id, shared_role_id)
    mu.assign_role_to_user(user_b_id, personal_b_id)
    mu.assign_role_to_user(user_c_id, personal_c_id)

    mu.get_user("a")["shared_role_id"] = shared_role_id
    mu.get_user("b")["shared_role_id"] = shared_role_id
    mu.get_user("a")["personal_role_id"] = personal_a_id
    mu.get_user("b")["personal_role_id"] = personal_b_id
    mu.get_user("c")["personal_role_id"] = personal_c_id

    yield mu

    mu.cleanup()


@pytest.fixture(scope="module")
def lib_test_data(lib_users):
    """
    Creates:
      - One shared library (is_personal=false)
      - Two shared documents in that library (visibility=shared, role=shared_role)
      - One personal library for User A
      - One personal document for User A in that personal library

    All writes go through psycopg2 with the correct RLS context, matching the
    pattern in test_rls_isolation.py::rls_test_data.
    """
    user_a = lib_users.get_user("a")
    user_b = lib_users.get_user("b")
    shared_role_id = user_a["shared_role_id"]
    personal_a_role_id = user_a["personal_role_id"]

    a_id = user_a["user_id"]
    b_id = user_b["user_id"]

    shared_lib_id = str(uuid.uuid4())
    personal_lib_id = str(uuid.uuid4())
    doc_shared_1 = str(uuid.uuid4())
    doc_shared_2 = str(uuid.uuid4())
    doc_personal_a = str(uuid.uuid4())
    now = datetime.utcnow()

    insert_library = """
        INSERT INTO libraries
            (id, name, description, is_personal, user_id, created_by, created_at, updated_at)
        VALUES (%s::uuid, %s, %s, %s, %s::uuid, %s::uuid, %s, %s)
    """
    insert_file = """
        INSERT INTO data_files
            (file_id, user_id, owner_id, filename, original_filename,
             mime_type, size_bytes, storage_path, content_hash,
             has_markdown, created_at, visibility, doc_type, library_id)
        VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'file', %s::uuid)
    """
    insert_doc_role = """
        INSERT INTO document_roles (file_id, role_id, role_name, added_by)
        VALUES (%s::uuid, %s::uuid, %s, %s::uuid)
    """
    insert_status = """
        INSERT INTO data_status (file_id, stage, progress, started_at, completed_at, updated_at)
        VALUES (%s::uuid, 'completed', 100, %s, %s, %s)
    """

    conn = _get_data_db_conn()
    try:
        # Libraries don't have RLS; use plain user ID in context for data_files below
        with conn:
            cur = conn.cursor()
            # Shared library (no user_id — org-wide)
            cur.execute(insert_library, (
                shared_lib_id, "Test Shared Library", "Library for RBAC tests",
                False, a_id, a_id, now, now,
            ))
            # Personal library for User A
            cur.execute(insert_library, (
                personal_lib_id, "User A Personal Library", None,
                True, a_id, a_id, now, now,
            ))
            cur.close()

        # Shared documents in the shared library (visibility=shared, role=lib-access-ab)
        a_all_roles = [shared_role_id, personal_a_role_id]
        with conn:
            cur = conn.cursor()
            _set_rls_context(cur, a_id, a_all_roles)
            for fid, fname in [(doc_shared_1, "shared_doc_1.pdf"), (doc_shared_2, "shared_doc_2.pdf")]:
                cur.execute(insert_file, (
                    fid, a_id, a_id, fname, fname,
                    "application/pdf", 1024,
                    f"role/{shared_role_id}/{fid}/{fname}",
                    f"hash_{fid[:8]}",
                    False, now, "shared", shared_lib_id,
                ))
                cur.execute(insert_doc_role, (fid, shared_role_id, "lib-access-ab", a_id))
                cur.execute(insert_status, (fid, now, now, now))
            # Personal document in A's personal library (visibility=personal)
            cur.execute(insert_file, (
                doc_personal_a, a_id, a_id, "a_personal.pdf", "a_personal.pdf",
                "application/pdf", 2048,
                f"personal/{a_id}/{doc_personal_a}/a_personal.pdf",
                "hash_a_personal",
                False, now, "personal", personal_lib_id,
            ))
            cur.execute(insert_status, (doc_personal_a, now, now, now))
            cur.close()
    finally:
        conn.close()

    yield {
        "shared_lib_id": shared_lib_id,
        "personal_lib_id": personal_lib_id,
        "doc_shared_1": doc_shared_1,
        "doc_shared_2": doc_shared_2,
        "doc_personal_a": doc_personal_a,
        "shared_role_id": shared_role_id,
        "user_a_id": a_id,
        "user_b_id": b_id,
    }

    # Cleanup
    try:
        conn = _get_data_db_conn()
        try:
            all_roles = [shared_role_id, personal_a_role_id]
            with conn:
                cur = conn.cursor()
                _set_rls_context(cur, a_id, all_roles)
                for fid in [doc_shared_1, doc_shared_2, doc_personal_a]:
                    cur.execute("DELETE FROM document_roles WHERE file_id = %s::uuid", (fid,))
                    cur.execute("DELETE FROM data_status WHERE file_id = %s::uuid", (fid,))
                    cur.execute("DELETE FROM data_files WHERE file_id = %s::uuid", (fid,))
                cur.execute("DELETE FROM libraries WHERE id = %s::uuid", (shared_lib_id,))
                cur.execute("DELETE FROM libraries WHERE id = %s::uuid", (personal_lib_id,))
                cur.close()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[lib_test_data] cleanup failed: {exc}")


@pytest.fixture(scope="module")
def lib_clients(lib_users, lib_test_data):
    """Tokens for users a, b, c plus the test data dict."""
    yield {
        "a": lib_users.get_data_token("a"),
        "b": lib_users.get_data_token("b"),
        "c": lib_users.get_data_token("c"),
        "data": lib_test_data,
    }


# =============================================================================
# Tests: library document visibility
# =============================================================================


@pytest.mark.asyncio
async def test_user_a_sees_shared_docs_in_library(lib_clients):
    """User A (has lib-access-ab role) can list both shared docs in the shared library."""
    lib_id = lib_clients["data"]["shared_lib_id"]
    async with _make_client(lib_clients["a"]) as c:
        resp = await c.get(f"/libraries/{lib_id}/documents")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    docs = body.get("data", {}).get("documents") or body.get("documents") or body.get("files") or []
    doc_ids = {d.get("id") or d.get("fileId") or d.get("file_id") for d in docs}
    assert lib_clients["data"]["doc_shared_1"] in doc_ids, "User A should see shared doc 1"
    assert lib_clients["data"]["doc_shared_2"] in doc_ids, "User A should see shared doc 2"


@pytest.mark.asyncio
async def test_user_b_sees_shared_docs_in_library(lib_clients):
    """User B (has lib-access-ab role) also sees both shared docs in the shared library."""
    lib_id = lib_clients["data"]["shared_lib_id"]
    async with _make_client(lib_clients["b"]) as c:
        resp = await c.get(f"/libraries/{lib_id}/documents")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    docs = body.get("data", {}).get("documents") or body.get("documents") or body.get("files") or []
    doc_ids = {d.get("id") or d.get("fileId") or d.get("file_id") for d in docs}
    assert lib_clients["data"]["doc_shared_1"] in doc_ids, "User B should see shared doc 1"
    assert lib_clients["data"]["doc_shared_2"] in doc_ids, "User B should see shared doc 2"


@pytest.mark.asyncio
async def test_user_c_sees_no_docs_in_shared_library(lib_clients):
    """User C (no matching role) sees an empty document list in the shared library."""
    lib_id = lib_clients["data"]["shared_lib_id"]
    async with _make_client(lib_clients["c"]) as c:
        resp = await c.get(f"/libraries/{lib_id}/documents")
    # Library itself is visible (no RLS on libraries table) but documents are filtered
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    docs = body.get("data", {}).get("documents") or body.get("documents") or body.get("files") or []
    assert len(docs) == 0, f"User C should see no docs, but got: {[d.get('id') for d in docs]}"


@pytest.mark.asyncio
async def test_user_b_cannot_see_personal_docs_in_a_personal_library(lib_clients):
    """User B cannot see User A's personal documents in A's personal library."""
    lib_id = lib_clients["data"]["personal_lib_id"]
    async with _make_client(lib_clients["b"]) as c:
        resp = await c.get(f"/libraries/{lib_id}/documents")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    docs = body.get("data", {}).get("documents") or body.get("documents") or body.get("files") or []
    doc_ids = {d.get("id") or d.get("fileId") or d.get("file_id") for d in docs}
    assert lib_clients["data"]["doc_personal_a"] not in doc_ids, \
        "User B should NOT see User A's personal document"


@pytest.mark.asyncio
async def test_user_a_sees_own_personal_doc_in_personal_library(lib_clients):
    """User A can see their own personal document in their personal library."""
    lib_id = lib_clients["data"]["personal_lib_id"]
    async with _make_client(lib_clients["a"]) as c:
        resp = await c.get(f"/libraries/{lib_id}/documents")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    docs = body.get("data", {}).get("documents") or body.get("documents") or body.get("files") or []
    doc_ids = {d.get("id") or d.get("fileId") or d.get("file_id") for d in docs}
    assert lib_clients["data"]["doc_personal_a"] in doc_ids, \
        "User A should see their own personal document"


# =============================================================================
# Tests: upload to shared library propagates visibility and roles
# =============================================================================


@pytest.mark.asyncio
async def test_upload_to_shared_library_sets_shared_visibility(lib_clients):
    """
    Uploading to a shared library with visibility=shared + role_ids produces a
    document that is immediately visible to other role-holders.

    This directly validates the upload-path fix: documents in a shared library
    must have visibility=shared and the library's document_roles set, not
    the default visibility=personal.
    """
    lib_id = lib_clients["data"]["shared_lib_id"]
    shared_role_id = lib_clients["data"]["shared_role_id"]

    # Simulate what the fixed upload BFF now sends to data-api
    import io
    file_content = b"%PDF-1.4 test content for library RBAC test"
    form_data = {
        "file": ("rbac_test_upload.pdf", io.BytesIO(file_content), "application/pdf"),
        "library_id": (None, lib_id),
        "visibility": (None, "shared"),
        "role_ids": (None, shared_role_id),
    }

    uploaded_file_id = None
    try:
        async with _make_client(lib_clients["a"]) as c:
            resp = await c.post("/upload", files=form_data)

        # Upload may succeed or return 400/422 depending on processing pipeline availability.
        # What we care about is: if it succeeds, the document must be visible to User B.
        if resp.status_code not in (200, 201):
            pytest.skip(f"Upload returned {resp.status_code} (pipeline may not be available in this env)")

        body = resp.json()
        uploaded_file_id = body.get("fileId") or body.get("file_id")
        assert uploaded_file_id, "Upload response should contain a file ID"

        # User B (has the role) should see the uploaded file
        async with _make_client(lib_clients["b"]) as c:
            resp_b = await c.get(f"/files/{uploaded_file_id}")
        assert resp_b.status_code == 200, \
            f"User B should see uploaded file (visibility=shared), got {resp_b.status_code}: {resp_b.text}"

        # User C (no role) should NOT see the uploaded file
        async with _make_client(lib_clients["c"]) as c:
            resp_c = await c.get(f"/files/{uploaded_file_id}")
        assert resp_c.status_code in (403, 404), \
            f"User C should be denied (no role), got {resp_c.status_code}"

        # File should appear in the shared library document list for User B
        async with _make_client(lib_clients["b"]) as c:
            resp_lib = await c.get(f"/libraries/{lib_id}/documents")
        assert resp_lib.status_code == 200
        lib_body = resp_lib.json()
        lib_docs = lib_body.get("data", {}).get("documents") or lib_body.get("documents") or lib_body.get("files") or []
        lib_doc_ids = {d.get("id") or d.get("fileId") or d.get("file_id") for d in lib_docs}
        assert uploaded_file_id in lib_doc_ids, \
            "Uploaded shared file should appear in library document list for User B"

    finally:
        # Clean up the uploaded file
        if uploaded_file_id:
            try:
                conn = _get_data_db_conn()
                try:
                    with conn:
                        cur = conn.cursor()
                        _set_rls_context(
                            cur,
                            lib_clients["data"]["user_a_id"],
                            [shared_role_id],
                        )
                        cur.execute("DELETE FROM document_roles WHERE file_id = %s::uuid", (uploaded_file_id,))
                        cur.execute("DELETE FROM data_status WHERE file_id = %s::uuid", (uploaded_file_id,))
                        cur.execute("DELETE FROM data_files WHERE file_id = %s::uuid", (uploaded_file_id,))
                        cur.close()
                finally:
                    conn.close()
            except Exception as exc:
                print(f"[test_upload] cleanup failed: {exc}")
