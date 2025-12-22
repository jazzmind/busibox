"""
Pytest configuration and shared fixtures.

Uses real JWT tokens from authz via token exchange - no mocks.
"""
import os
import pytest
import asyncio
import httpx
from pathlib import Path
from unittest.mock import Mock
from httpx import AsyncClient, ASGITransport
from dotenv import load_dotenv

from api.services.minio_service import MinIOService
from api.services.postgres import PostgresService
from shared.config import Config

# Load environment variables from .env.local (for make test-local) or .env
env_local = Path(__file__).parent.parent / ".env.local"
env_file = Path(__file__).parent.parent / ".env"
if env_local.exists():
    load_dotenv(env_local)
elif env_file.exists():
    load_dotenv(env_file)


# Sample files paths - supports both new (testdocs repo) and old (samples/) structure
SAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "samples"

# New testdocs structure has files organized by type
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


def _ensure_test_user_has_roles():
    """
    Ensure the test user has roles with required scopes.
    
    This syncs the test user with authz to ensure they have the necessary
    permissions for testing. This is idempotent - safe to call multiple times.
    """
    import uuid as uuid_mod
    
    client_id = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
    client_secret = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")
    test_user_id = os.getenv("TEST_USER_ID", "")
    authz_url = os.getenv("AUTHZ_URL") or os.getenv("AUTHZ_BASE_URL", "")
    
    if not all([client_id, client_secret, test_user_id, authz_url]):
        return  # Skip if not configured
    
    # Use a deterministic role ID based on test user ID so it's consistent
    role_id = str(uuid_mod.uuid5(uuid_mod.NAMESPACE_DNS, f"test-admin-{test_user_id}"))
    
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{authz_url}/internal/sync/user",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "user_id": test_user_id,
                "email": os.getenv("TEST_USER_EMAIL", "test@busibox.local"),
                "status": "active",
                "roles": [{
                    "id": role_id,
                    "name": "TestAdmin",
                    "description": "Test admin role with all scopes",
                    "scopes": [
                        "ingest.read", "ingest.write", "ingest.delete",
                        "search.read", "search.write",
                        "agent.read", "agent.write",
                    ]
                }],
                "user_role_ids": [role_id],
            },
        )
        if resp.status_code != 200:
            print(f"Warning: Failed to sync test user roles: {resp.status_code} - {resp.text}")


# Ensure test user has roles before any tests run
_ensure_test_user_has_roles()


def _get_real_token(audience: str = "ingest-api") -> str:
    """
    Get a real access token from authz via token exchange.
    
    Uses the bootstrap client credentials and TEST_USER_ID to get
    a token with real user identity for RLS.
    """
    client_id = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
    client_secret = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")
    test_user_id = os.getenv("TEST_USER_ID", "")
    authz_url = os.getenv("AUTHZ_URL") or os.getenv("AUTHZ_BASE_URL", "")
    
    if not all([client_id, client_secret, test_user_id, authz_url]):
        pytest.skip(
            f"AuthZ credentials not configured. "
            f"client_id={bool(client_id)}, secret={bool(client_secret)}, "
            f"user_id={bool(test_user_id)}, authz_url={bool(authz_url)}"
        )
    
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{authz_url}/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": client_id,
                "client_secret": client_secret,
                "requested_subject": test_user_id,
                "audience": audience,
            },
        )
        
        if resp.status_code != 200:
            pytest.fail(f"Failed to get access token: {resp.status_code} - {resp.text}")
        
        data = resp.json()
        if "access_token" not in data:
            pytest.fail(f"No access_token in response: {data}")
        
        return data["access_token"]


@pytest.fixture(scope="session")
def real_auth_header():
    """
    Get a real access token for the ingest API.
    Uses token exchange to get a token with:
    - sub = TEST_USER_ID (real user for RLS)
    - aud = ingest-api (correct audience)
    """
    token = _get_real_token("ingest-api")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
async def initialized_app():
    """
    Session-scoped fixture that initializes the FastAPI app and its services.
    This ensures the PostgreSQL pool is created in the session's event loop
    and reused across all tests.
    """
    from api import main as main_module
    app = main_module.app
    pg_service = main_module.pg_service
    
    # Connect the postgres service in this event loop
    await pg_service.connect()
    
    yield app, pg_service
    
    # Cleanup at end of session
    await pg_service.disconnect()


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
    """
    Real PostgreSQL service instance.
    Reuses the session-scoped connection from initialized_app.
    """
    _, pg_service = initialized_app
    yield pg_service


@pytest.fixture
async def async_client(initialized_app, real_auth_header):
    """
    Async HTTP client for API testing with real auth.
    Uses the session-scoped app to ensure connection pool consistency.
    """
    app, _ = initialized_app
    
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update(real_auth_header)
        yield client


@pytest.fixture
async def async_client_different_user(initialized_app):
    """
    Async HTTP client for testing unauthorized access with a different user.
    Gets a token for a different user ID to test RLS.
    """
    app, _ = initialized_app
    
    # Get a token for a different (non-existent) user
    # This will fail RLS checks for resources owned by TEST_USER_ID
    import uuid
    different_user_id = str(uuid.uuid4())
    
    client_id = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
    client_secret = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")
    authz_url = os.getenv("AUTHZ_URL") or os.getenv("AUTHZ_BASE_URL", "")
    
    if not all([client_id, client_secret, authz_url]):
        pytest.skip("AuthZ credentials not configured for different_user fixture")
    
    with httpx.Client(timeout=10.0) as http_client:
        resp = http_client.post(
            f"{authz_url}/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": client_id,
                "client_secret": client_secret,
                "requested_subject": different_user_id,
                "audience": "ingest-api",
            },
        )
        
        if resp.status_code != 200:
            pytest.skip(f"Could not get token for different user: {resp.status_code}")
        
        token = resp.json().get("access_token")
    
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


@pytest.fixture
async def async_client_read_only(initialized_app):
    """
    Async HTTP client with only read scopes (for testing scope enforcement).
    Note: With real tokens, scopes are determined by the authz service.
    This fixture gets a token with reduced scopes if possible.
    """
    app, _ = initialized_app
    
    # For now, use the same token - scope enforcement is server-side
    # The authz service determines scopes based on user roles
    token = _get_real_token("ingest-api")
    
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


@pytest.fixture
async def test_file_with_markdown(postgres_service, minio_service):
    """Create a test file with markdown generated."""
    import uuid
    from datetime import datetime
    
    test_user_id = os.getenv("TEST_USER_ID", str(uuid.uuid4()))
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
async def test_file_without_markdown(postgres_service):
    """Create a test file without markdown."""
    import uuid
    from datetime import datetime
    
    test_user_id = os.getenv("TEST_USER_ID", str(uuid.uuid4()))
    file_id = str(uuid.uuid4())
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, owner_id, filename, status, has_markdown, created_at, visibility)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """, uuid.UUID(file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id),
            "test.pdf", "completed", False, datetime.utcnow(), "personal")
    
    yield {"file_id": file_id, "user_id": test_user_id, "has_markdown": False}
    
    # Cleanup
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", uuid.UUID(file_id))


@pytest.fixture
async def test_file_with_images(postgres_service, minio_service):
    """Create a test file with extracted images."""
    import uuid
    from datetime import datetime
    
    test_user_id = os.getenv("TEST_USER_ID", str(uuid.uuid4()))
    file_id = str(uuid.uuid4())
    
    test_image_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
    image_count = 3
    
    for i in range(image_count):
        image_path = f"{test_user_id}/{file_id}/images/image_{i}.png"
        await minio_service.upload_bytes(test_image_data, image_path, content_type='image/png')
    
    markdown_content = f"# Test Document\n\n"
    for i in range(image_count):
        markdown_content += f"![Image {i}](image_{i}.png)\n\n"
    
    markdown_path = f"{test_user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, owner_id, filename, status, markdown_storage_path, has_markdown, 
             extracted_images_storage_path, has_extracted_images, created_at, visibility)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """, uuid.UUID(file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id),
            "test.pdf", "completed", markdown_path, True, 
            f"{test_user_id}/{file_id}/images", True, datetime.utcnow(), "personal")
    
    yield {"file_id": file_id, "user_id": test_user_id, "image_count": image_count}
    
    # Cleanup
    try:
        await minio_service.delete_file(markdown_path)
        for i in range(image_count):
            await minio_service.delete_file(f"{test_user_id}/{file_id}/images/image_{i}.png")
    except Exception:
        pass
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", uuid.UUID(file_id))


@pytest.fixture
async def test_file_with_headings(postgres_service, minio_service):
    """Create a test file with headings for TOC testing."""
    import uuid
    from datetime import datetime
    
    test_user_id = os.getenv("TEST_USER_ID", str(uuid.uuid4()))
    file_id = str(uuid.uuid4())
    
    markdown_content = """# Main Title

## Section 1

Content for section 1.

### Subsection 1.1

More content.

## Section 2

Content for section 2.
"""
    
    markdown_path = f"{test_user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, owner_id, filename, status, markdown_storage_path, has_markdown, created_at, visibility)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """, uuid.UUID(file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id),
            "test.pdf", "completed", markdown_path, True, datetime.utcnow(), "personal")
    
    yield {"file_id": file_id, "user_id": test_user_id, "has_headings": True}
    
    # Cleanup
    try:
        await minio_service.delete_file(markdown_path)
    except Exception:
        pass
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", uuid.UUID(file_id))


@pytest.fixture
async def test_file_with_dangerous_content(postgres_service, minio_service):
    """Create a test file with potentially dangerous content for sanitization testing."""
    import uuid
    from datetime import datetime
    
    test_user_id = os.getenv("TEST_USER_ID", str(uuid.uuid4()))
    file_id = str(uuid.uuid4())
    
    markdown_content = """# Test Document

<script>alert('XSS')</script>

Regular content here.
"""
    
    markdown_path = f"{test_user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, owner_id, filename, status, markdown_storage_path, has_markdown, created_at, visibility)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """, uuid.UUID(file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id),
            "test.pdf", "completed", markdown_path, True, datetime.utcnow(), "personal")
    
    yield {"file_id": file_id, "user_id": test_user_id, "has_scripts": True}
    
    # Cleanup
    try:
        await minio_service.delete_file(markdown_path)
    except Exception:
        pass
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", uuid.UUID(file_id))


@pytest.fixture
async def test_file_without_images(postgres_service, minio_service):
    """Create a test file without images."""
    import uuid
    from datetime import datetime
    
    test_user_id = os.getenv("TEST_USER_ID", str(uuid.uuid4()))
    file_id = str(uuid.uuid4())
    
    markdown_content = "# Test Document\n\nText only, no images."
    markdown_path = f"{test_user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, owner_id, filename, status, markdown_storage_path, has_markdown, 
             has_extracted_images, created_at, visibility)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """, uuid.UUID(file_id), uuid.UUID(test_user_id), uuid.UUID(test_user_id),
            "test.pdf", "completed", markdown_path, True, False, datetime.utcnow(), "personal")
    
    yield {"file_id": file_id, "user_id": test_user_id, "image_count": 0}
    
    # Cleanup
    try:
        await minio_service.delete_file(markdown_path)
    except Exception:
        pass
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", uuid.UUID(file_id))


# Legacy mock fixtures (for tests that don't need real services)
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
def mock_redis_service():
    """Mock Redis service."""
    service = Mock()
    service.add_job = Mock()
    service.get_job = Mock(return_value=None)
    return service


@pytest.fixture
def mock_milvus_service():
    """Mock Milvus service."""
    service = Mock()
    service.insert_text_chunks = Mock()
    service.insert_page_images = Mock()
    service.check_duplicate = Mock(return_value=None)
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
    config.redis_host = "localhost"
    config.redis_port = 6379
    config.milvus_host = "localhost"
    config.milvus_port = 19530
    config.litellm_base_url = "http://localhost:4000"
    config.litellm_api_key = "test-key"
    return config
