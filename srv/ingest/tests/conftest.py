"""
Pytest configuration and shared fixtures.

Uses real JWT tokens from authz - no mocks.
Test user starts with NO roles/scopes. Tests must explicitly grant permissions
using the admin API and clean up when done.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# CRITICAL: Load environment variables BEFORE any other imports
# This must happen at the very top of conftest.py before pytest imports test files
# which may import api.main and trigger Config() initialization
_env_local = Path(__file__).parent.parent / ".env.local"
_env_file = Path(__file__).parent.parent / ".env"
if _env_local.exists():
    load_dotenv(_env_local, override=True)
elif _env_file.exists():
    load_dotenv(_env_file, override=True)

# Verify critical env vars are set
_pg_host = os.getenv("POSTGRES_HOST")
if _pg_host:
    print(f"[conftest] Using POSTGRES_HOST={_pg_host}")

import pytest
import asyncio
import httpx
import uuid
from unittest.mock import Mock
from httpx import AsyncClient, ASGITransport
from contextlib import asynccontextmanager

# Now import app modules (they will use the loaded env vars)
from api.services.minio_service import MinIOService
from api.services.postgres import PostgresService
from shared.config import Config


# Sample files paths - supports both new (testdocs repo) and old (samples/) structure
SAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "samples"

SAMPLE_PDF_DIAGRAM = SAMPLES_DIR / "pdf" / "plans" / "doc2_washington" / "683 Washington Street As-Built (06-26-25) Sheet 1 (Rev 1) (09-14-25).pdf"
if not SAMPLE_PDF_DIAGRAM.exists():
    SAMPLE_PDF_DIAGRAM = SAMPLES_DIR / "diagram.pdf"

SAMPLE_PDF_BEGINNING = SAMPLES_DIR / "pdf" / "text" / "inthebeginning.pdf"
if not SAMPLE_PDF_BEGINNING.exists():
    SAMPLE_PDF_BEGINNING = SAMPLES_DIR / "inthebeginning.pdf"

_pdf_general_dir = SAMPLES_DIR / "pdf" / "general"
if _pdf_general_dir.exists():
    SAMPLE_PDF_DOCS = list(_pdf_general_dir.glob("*/source.pdf"))
else:
    SAMPLE_PDF_DOCS = list((SAMPLES_DIR / "docs").glob("*/source.pdf"))

SAMPLE_IMAGE = SAMPLES_DIR / "image" / "cat.jpg"
if not SAMPLE_IMAGE.exists():
    SAMPLE_IMAGE = SAMPLES_DIR / "cat.jpg"


@pytest.fixture(scope="session")
def event_loop_policy():
    """Return the event loop policy for the session."""
    return asyncio.get_event_loop_policy()


# ============================================================================
# AuthZ Admin Helpers
# ============================================================================

class AuthzAdmin:
    """Helper class for managing roles/scopes via authz admin API."""
    
    def __init__(self):
        self.authz_url = os.getenv("AUTHZ_URL") or os.getenv("AUTHZ_BASE_URL", "")
        self.admin_token = os.getenv("AUTHZ_ADMIN_TOKEN", "")
        self.test_user_id = os.getenv("TEST_USER_ID", "")
        self.client_id = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "")
        self.client_secret = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")
        
        if not all([self.authz_url, self.admin_token, self.test_user_id]):
            raise ValueError(
                f"AuthZ not configured. authz_url={bool(self.authz_url)}, "
                f"admin_token={bool(self.admin_token)}, test_user_id={bool(self.test_user_id)}"
            )
    
    def _headers(self):
        return {"Authorization": f"Bearer {self.admin_token}"}
    
    def create_role(self, name: str, scopes: list[str]) -> str:
        """Create a role with the given scopes. Returns role_id."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{self.authz_url}/admin/roles",
                headers=self._headers(),
                json={"name": name, "scopes": scopes},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to create role: {resp.status_code} - {resp.text}")
            return resp.json()["id"]
    
    def delete_role(self, role_id: str):
        """Delete a role."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.delete(
                f"{self.authz_url}/admin/roles/{role_id}",
                headers=self._headers(),
            )
            # 404 is ok - role may already be deleted
            if resp.status_code not in [200, 404]:
                raise RuntimeError(f"Failed to delete role: {resp.status_code} - {resp.text}")
    
    def assign_role(self, user_id: str, role_id: str):
        """Assign a role to a user."""
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{self.authz_url}/admin/user-roles",
                headers=self._headers(),
                json={"user_id": user_id, "role_id": role_id},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to assign role: {resp.status_code} - {resp.text}")
    
    def remove_role(self, user_id: str, role_id: str):
        """Remove a role from a user."""
        with httpx.Client(timeout=10.0) as client:
            # DELETE with body - use request() instead of delete()
            resp = client.request(
                "DELETE",
                f"{self.authz_url}/admin/user-roles",
                headers=self._headers(),
                json={"user_id": user_id, "role_id": role_id},
            )
            # 404 is ok - binding may already be removed
            if resp.status_code not in [200, 404]:
                raise RuntimeError(f"Failed to remove role: {resp.status_code} - {resp.text}")
    
    def get_token(self, audience: str = None) -> str:
        """Get a token for the test user with their current roles/scopes.
        
        If audience is not specified, uses AUTHZ_AUDIENCE from environment
        (which should match what the service expects).
        """
        if audience is None:
            audience = os.getenv("AUTHZ_AUDIENCE", "ingest-api")
        
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{self.authz_url}/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "requested_subject": self.test_user_id,
                    "audience": audience,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to get token: {resp.status_code} - {resp.text}")
            return resp.json()["access_token"]


@pytest.fixture(scope="session")
def authz_admin():
    """AuthZ admin helper for managing roles/scopes."""
    try:
        return AuthzAdmin()
    except ValueError as e:
        pytest.skip(str(e))


@pytest.fixture
def test_user_id():
    """The test user ID."""
    return os.getenv("TEST_USER_ID", "")


# ============================================================================
# Role/Scope Fixtures - Tests use these to grant permissions
# ============================================================================

@pytest.fixture
def ingest_read_role(authz_admin, test_user_id):
    """
    Grant the test user ingest.read scope for the duration of the test.
    Cleans up after the test completes.
    """
    role_name = f"test-ingest-read-{uuid.uuid4().hex[:8]}"
    role_id = authz_admin.create_role(role_name, ["ingest.read"])
    authz_admin.assign_role(test_user_id, role_id)
    
    yield {"role_id": role_id, "scopes": ["ingest.read"]}
    
    # Cleanup
    authz_admin.remove_role(test_user_id, role_id)
    authz_admin.delete_role(role_id)


@pytest.fixture
def ingest_write_role(authz_admin, test_user_id):
    """Grant the test user ingest.write scope."""
    role_name = f"test-ingest-write-{uuid.uuid4().hex[:8]}"
    role_id = authz_admin.create_role(role_name, ["ingest.write"])
    authz_admin.assign_role(test_user_id, role_id)
    
    yield {"role_id": role_id, "scopes": ["ingest.write"]}
    
    authz_admin.remove_role(test_user_id, role_id)
    authz_admin.delete_role(role_id)


@pytest.fixture
def ingest_delete_role(authz_admin, test_user_id):
    """Grant the test user ingest.delete scope."""
    role_name = f"test-ingest-delete-{uuid.uuid4().hex[:8]}"
    role_id = authz_admin.create_role(role_name, ["ingest.delete"])
    authz_admin.assign_role(test_user_id, role_id)
    
    yield {"role_id": role_id, "scopes": ["ingest.delete"]}
    
    authz_admin.remove_role(test_user_id, role_id)
    authz_admin.delete_role(role_id)


@pytest.fixture
def ingest_full_access_role(authz_admin, test_user_id):
    """Grant the test user full ingest access (read, write, delete)."""
    role_name = f"test-ingest-full-{uuid.uuid4().hex[:8]}"
    scopes = ["ingest.read", "ingest.write", "ingest.delete", "search.read"]
    role_id = authz_admin.create_role(role_name, scopes)
    authz_admin.assign_role(test_user_id, role_id)
    
    yield {"role_id": role_id, "scopes": scopes}
    
    authz_admin.remove_role(test_user_id, role_id)
    authz_admin.delete_role(role_id)


# ============================================================================
# App and Client Fixtures
# ============================================================================

@pytest.fixture(scope="session")
async def initialized_app():
    """
    Session-scoped fixture that initializes the FastAPI app and its services.
    """
    from api import main as main_module
    app = main_module.app
    pg_service = main_module.pg_service
    
    await pg_service.connect()
    
    yield app, pg_service
    
    await pg_service.disconnect()


@pytest.fixture
async def async_client(initialized_app, authz_admin, ingest_full_access_role):
    """
    Async HTTP client for API testing with full ingest access.
    
    This fixture automatically grants the test user full ingest permissions
    and cleans them up after the test.
    """
    app, _ = initialized_app
    
    # Get a fresh token with the newly assigned role
    token = authz_admin.get_token("ingest-api")
    
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


@pytest.fixture
async def async_client_no_auth(initialized_app):
    """
    Async HTTP client with NO authentication.
    Use this to test that endpoints require auth.
    """
    app, _ = initialized_app
    
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def async_client_read_only(initialized_app, authz_admin, ingest_read_role):
    """
    Async HTTP client with only read scope.
    Use this to test scope enforcement.
    """
    app, _ = initialized_app
    
    token = authz_admin.get_token("ingest-api")
    
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


@pytest.fixture
async def async_client_no_scopes(initialized_app, authz_admin):
    """
    Async HTTP client with valid auth but NO scopes.
    Use this to test that scope enforcement is working.
    """
    app, _ = initialized_app
    
    # Get token without any roles assigned (test user has no roles by default)
    token = authz_admin.get_token("ingest-api")
    
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


# ============================================================================
# Service Fixtures
# ============================================================================

@pytest.fixture
def sample_pdf_simple():
    """Path to a simple PDF for testing."""
    if SAMPLE_PDF_DIAGRAM.exists():
        return str(SAMPLE_PDF_DIAGRAM)
    return None


@pytest.fixture
def sample_pdf_text():
    """Path to a text-heavy PDF for testing."""
    if SAMPLE_PDF_BEGINNING.exists():
        return str(SAMPLE_PDF_BEGINNING)
    return None


@pytest.fixture
def sample_pdf_docs():
    """List of paths to document PDFs for testing."""
    return [str(pdf) for pdf in SAMPLE_PDF_DOCS if pdf.exists()]


@pytest.fixture
def sample_image():
    """Path to a sample image for testing."""
    if SAMPLE_IMAGE.exists():
        return str(SAMPLE_IMAGE)
    return None


@pytest.fixture
def config():
    """Real configuration from environment."""
    return Config().to_dict()


@pytest.fixture
def minio_service(config):
    """Real MinIO service instance."""
    return MinIOService(config)


@pytest.fixture
async def postgres_service(initialized_app):
    """Real PostgreSQL service instance."""
    _, pg_service = initialized_app
    yield pg_service


# ============================================================================
# Test Data Fixtures
# ============================================================================

@pytest.fixture
async def test_file_with_markdown(postgres_service, minio_service, test_user_id):
    """Create a test file with markdown generated."""
    from datetime import datetime
    
    file_id = str(uuid.uuid4())
    
    markdown_content = "# Test Document\n\nThis is a test."
    markdown_path = f"{test_user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, owner_id, filename, status, markdown_storage_path, has_markdown, created_at, visibility)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """, uuid.UUID(file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id), 
            "test.pdf", "completed", markdown_path, True, datetime.utcnow(), "personal")
    
    yield {"file_id": file_id, "user_id": test_user_id, "has_markdown": True}
    
    # Cleanup
    try:
        await minio_service.delete_file(markdown_path)
    except Exception:
        pass
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", uuid.UUID(file_id))


@pytest.fixture
async def test_file_without_markdown(postgres_service, test_user_id):
    """Create a test file without markdown."""
    from datetime import datetime
    
    file_id = str(uuid.uuid4())
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, owner_id, filename, status, has_markdown, created_at, visibility)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """, uuid.UUID(file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id),
            "test.pdf", "completed", False, datetime.utcnow(), "personal")
    
    yield {"file_id": file_id, "user_id": test_user_id, "has_markdown": False}
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", uuid.UUID(file_id))


# ============================================================================
# Legacy Mock Fixtures (for tests that don't need real services)
# ============================================================================

@pytest.fixture
def mock_postgres_service():
    """Mock PostgreSQL service."""
    service = Mock()
    service.create_file_record = Mock(return_value="file-test-123")
    service.update_status = Mock()
    service.get_file_metadata = Mock(return_value={
        "file_id": "file-test-123",
        "filename": "test.pdf",
        "status": "completed",
    })
    service.delete_file = Mock()
    return service


@pytest.fixture
def mock_minio_service():
    """Mock MinIO service."""
    service = Mock()
    service.upload_file = Mock(return_value="s3://bucket/path")
    service.delete_file = Mock()
    service.file_exists = Mock(return_value=True)
    return service


@pytest.fixture
def mock_config():
    """Mock configuration."""
    config = Mock()
    config.postgres_host = "localhost"
    config.postgres_port = 5432
    config.postgres_db = "test_db"
    config.postgres_user = "test_user"
    config.postgres_password = "test_pass"
    config.minio_endpoint = "localhost:9000"
    config.minio_access_key = "minioadmin"
    config.minio_secret_key = "minioadmin"
    return config
