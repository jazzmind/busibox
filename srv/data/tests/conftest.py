"""
Pytest configuration and shared fixtures.

Uses real JWT tokens from authz - no mocks.
Test user starts with NO roles/scopes. Tests must explicitly grant permissions
using the admin API and clean up when done.

Uses shared test_utils library for auth handling.
"""
import os
import sys
from pathlib import Path

# Add shared testing library to path FIRST (before any other imports)
# When deployed: /srv/data/src/testing/ (via PYTHONPATH=/srv/data/src)
# When local Docker: /app/shared/testing/ (via PYTHONPATH=/app/src:/app:/app/shared)
# When local dev: ../../srv/shared/testing/
_test_utils_paths = [
    os.path.join(os.path.dirname(__file__), "..", "src"),  # Deployed: /srv/data/src (contains testing/)
    os.path.join(os.path.dirname(__file__), "..", "..", "shared"),  # Local: srv/shared (contains testing/)
]
for _path in _test_utils_paths:
    if os.path.exists(_path) and _path not in sys.path:
        sys.path.insert(0, _path)

# CRITICAL: Load environment variables BEFORE any other imports
# This must happen at the very top of conftest.py before pytest imports test files
# which may import api.main and trigger Config() initialization
from testing.environment import load_env_files, get_test_doc_repo_path, create_service_auth_fixture
load_env_files(Path(__file__).parent.parent)

# Verify critical env vars are set
_pg_host = os.getenv("POSTGRES_HOST")
if _pg_host:
    print(f"[conftest] Using POSTGRES_HOST={_pg_host}")

import pytest
import asyncio
import uuid
from contextlib import contextmanager
from unittest.mock import Mock
from httpx import AsyncClient, ASGITransport

# Import shared testing utilities
from testing.auth import AuthTestClient, auth_client  # noqa: F401 - auth_client for fixture discovery
from testing.fixtures import require_env
from testing.database import RLSEnabledPool


@contextmanager
def _suspend_admin_role(auth_client: AuthTestClient):
    """
    Temporarily remove ALL Admin role assignments so that scope-enforcement
    tests can verify 403 behaviour. Restores on exit.
    """
    try:
        import psycopg2
    except ImportError:
        yield
        return

    pg_host = os.getenv("POSTGRES_HOST", "localhost")
    pg_port = int(os.getenv("POSTGRES_PORT", "5432"))
    pg_user = os.getenv("TEST_DB_USER", os.getenv("POSTGRES_USER", "busibox_test_user"))
    pg_pass = os.getenv("TEST_DB_PASSWORD", os.getenv("POSTGRES_PASSWORD", ""))
    pg_db = os.getenv("AUTHZ_TEST_DB", "test_authz")

    try:
        conn = psycopg2.connect(
            host=pg_host, port=pg_port,
            dbname=pg_db, user=pg_user, password=pg_pass,
            connect_timeout=5,
        )
        conn.autocommit = True
        cur = conn.cursor()
        try:
            # Save existing Admin assignments so we can restore them
            cur.execute(
                "SELECT user_id FROM authz_user_roles "
                "WHERE role_id IN (SELECT id FROM authz_roles WHERE name = 'Admin')"
            )
            admin_users = [row[0] for row in cur.fetchall()]

            cur.execute(
                "DELETE FROM authz_user_roles "
                "WHERE role_id IN (SELECT id FROM authz_roles WHERE name = 'Admin')"
            )
            yield
        finally:
            for uid in admin_users:
                cur.execute(
                    "INSERT INTO authz_user_roles (user_id, role_id) "
                    "SELECT %s::uuid, id FROM authz_roles WHERE name = 'Admin' "
                    "ON CONFLICT DO NOTHING",
                    (str(uid),),
                )
            cur.close()
            conn.close()
    except Exception:
        yield

# Enable pytest plugin for failed test filter generation
pytest_plugins = ["testing.pytest_failed_filter"]


# =============================================================================
# Environment setup - using shared service auth fixture factory
# =============================================================================

# Creates an autouse fixture that sets AUTHZ_AUDIENCE=data-api
set_auth_env = create_service_auth_fixture("data")


# =============================================================================
# Database Fixtures
# =============================================================================

# Session-scoped RLS pool - shared across all tests for efficiency
# Requires asyncio_default_test_loop_scope = session in pytest.ini
@pytest.fixture(scope="session")
async def rls_pool():
    """
    Session-scoped RLS-enabled database pool.
    Uses POSTGRES_DB from environment (should be 'files' for data service).
    
    Shared across all tests in the session for connection efficiency.
    RLS context is set per-test via set_rls_context().
    """
    pool = RLSEnabledPool(
        database=os.getenv("POSTGRES_DB", "files"),
    )
    await pool.initialize()
    yield pool
    try:
        await pool.close()
    except RuntimeError:
        pass

# Now import app modules (they will use the loaded env vars)
from api.services.minio_service import MinIOService
from api.services.postgres import PostgresService
from shared.config import Config


# Sample files paths - uses shared utility that handles both local and container paths
# Local: points to busibox-testdocs directory (set by generate-local-test-env.sh)
# Container: points to /srv/test-docs (set by Ansible template)
SAMPLES_DIR = get_test_doc_repo_path()

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
# AuthZ Admin Helpers - Using shared test_utils
# ============================================================================

# The auth_client fixture is imported at the top of this file from testing.auth
# It's a session-scoped fixture that:
# - Automatically cleans up stale test roles from previous runs
# - Cleans up created roles at session end


@pytest.fixture
def test_user_id(auth_client):
    """The test user ID from auth_client."""
    return auth_client.test_user_id


@pytest.fixture
def random_user_id():
    """A random user ID for tests that don't need auth integration."""
    return str(uuid.uuid4())


# ============================================================================
# Role/Scope Fixtures - Tests use these to grant permissions
# ============================================================================

@pytest.fixture
def data_read_role(auth_client, test_user_id):
    """
    Grant the test user data.read scope for the duration of the test.
    Cleans up after the test completes.
    """
    role_name = f"test-data-read-{uuid.uuid4().hex[:8]}"
    role_id = auth_client.create_role(role_name, scopes=["data.read"])
    auth_client.add_role_to_user(role_name)
    
    yield {"role_id": role_id, "scopes": ["data.read"]}
    
    # Cleanup
    auth_client.remove_role_from_user(role_name)
    auth_client.delete_role(role_id)


@pytest.fixture
def data_write_role(auth_client, test_user_id):
    """Grant the test user data.write scope."""
    role_name = f"test-data-write-{uuid.uuid4().hex[:8]}"
    role_id = auth_client.create_role(role_name, scopes=["data.write"])
    auth_client.add_role_to_user(role_name)
    
    yield {"role_id": role_id, "scopes": ["data.write"]}
    
    auth_client.remove_role_from_user(role_name)
    auth_client.delete_role(role_id)


@pytest.fixture
def data_delete_role(auth_client, test_user_id):
    """Grant the test user data.delete scope."""
    role_name = f"test-data-delete-{uuid.uuid4().hex[:8]}"
    role_id = auth_client.create_role(role_name, scopes=["data.delete"])
    auth_client.add_role_to_user(role_name)
    
    yield {"role_id": role_id, "scopes": ["data.delete"]}
    
    auth_client.remove_role_from_user(role_name)
    auth_client.delete_role(role_id)


@pytest.fixture
def data_full_access_role(auth_client, test_user_id):
    """Grant the test user full data access (read, write, delete)."""
    role_name = f"test-data-full-{uuid.uuid4().hex[:8]}"
    scopes = ["data.read", "data.write", "data.delete", "search.read"]
    role_id = auth_client.create_role(role_name, scopes=scopes)
    auth_client.add_role_to_user(role_name)
    
    yield {"role_id": role_id, "scopes": scopes}
    
    auth_client.remove_role_from_user(role_name)
    auth_client.delete_role(role_id)


# ============================================================================
# App and Client Fixtures
# ============================================================================

@pytest.fixture(scope="session")
async def initialized_app():
    """
    Session-scoped fixture that initializes the FastAPI app and its services.
    Only needed for direct DB access (e.g. test_file_with_markdown fixtures).
    """
    from api import main as main_module
    app = main_module.app
    pg_service = main_module.pg_service
    
    await pg_service.connect()
    
    yield app, pg_service
    
    try:
        await pg_service.disconnect()
    except RuntimeError:
        pass


_API_PORT = os.getenv("API_PORT", "8002")
_SERVICE_URL = os.getenv("DATA_API_URL", f"http://localhost:{_API_PORT}")


@pytest.fixture
async def async_client(auth_client, data_full_access_role):
    """
    Async HTTP client for API testing with full data access.
    
    This fixture automatically grants the test user full data permissions
    and cleans them up after the test.
    
    Makes real HTTP requests to the running data-api service (avoids
    BaseHTTPMiddleware/asyncpg pool conflicts with ASGITransport).
    """
    token = auth_client.get_token(audience="data-api")
    
    async with AsyncClient(base_url=_SERVICE_URL, timeout=60.0) as client:
        client.headers.update({
            "Authorization": f"Bearer {token}",
            "X-Test-Mode": "true",
        })
        yield client


@pytest.fixture
async def async_client_no_auth():
    """
    Async HTTP client with NO authentication.
    Use this to test that endpoints require auth.
    """
    async with AsyncClient(base_url=_SERVICE_URL) as client:
        yield client


@pytest.fixture
async def async_client_read_only(auth_client, data_read_role):
    """
    Async HTTP client with only read scope.
    Use this to test scope enforcement.
    """
    with _suspend_admin_role(auth_client):
        token = auth_client.get_token(audience="data-api")
        
        async with AsyncClient(base_url=_SERVICE_URL) as client:
            client.headers.update({
                "Authorization": f"Bearer {token}",
                "X-Test-Mode": "true",
            })
            yield client


@pytest.fixture
async def async_client_no_scopes(auth_client):
    """
    Async HTTP client with valid auth but NO scopes.
    Use this to test that scope enforcement is working.
    """
    with _suspend_admin_role(auth_client):
        token = auth_client.get_token(audience="data-api")
        
        async with AsyncClient(base_url=_SERVICE_URL) as client:
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
            INSERT INTO data_files 
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
        await conn.execute("DELETE FROM data_files WHERE id = $1", uuid.UUID(file_id))


@pytest.fixture
async def test_file_without_markdown(postgres_service, test_user_id):
    """Create a test file without markdown."""
    from datetime import datetime
    
    file_id = str(uuid.uuid4())
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO data_files 
            (id, user_id, owner_id, filename, status, has_markdown, created_at, visibility)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """, uuid.UUID(file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id),
            "test.pdf", "completed", False, datetime.utcnow(), "personal")
    
    yield {"file_id": file_id, "user_id": test_user_id, "has_markdown": False}
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM data_files WHERE id = $1", uuid.UUID(file_id))


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
