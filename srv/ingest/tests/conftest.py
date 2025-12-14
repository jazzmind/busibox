"""
Pytest configuration and shared fixtures.
"""
import os
import pytest
import asyncio
from pathlib import Path
from unittest.mock import Mock, AsyncMock
from httpx import AsyncClient
from fastapi.testclient import TestClient
from dotenv import load_dotenv

from api.services.minio_service import MinIOService
from api.services.postgres import PostgresService
from shared.config import Config

# Load environment variables from .env file before running tests
load_dotenv()

@pytest.fixture(autouse=True)
def set_auth_env(monkeypatch):
    """
    Tests use authz-style RS256 tokens (no legacy X-User-Id).
    """
    monkeypatch.setenv("AUTHZ_ISSUER", "busibox-authz")
    monkeypatch.setenv("AUTHZ_AUDIENCE", "ingest-api")
    monkeypatch.setenv("JWT_ALGORITHMS", "RS256")
    monkeypatch.setenv("AUTHZ_JWKS_URL", "http://test/.well-known/jwks.json")
    yield


# Sample files paths - supports both new (testdocs repo) and old (samples/) structure
SAMPLES_DIR = Path(__file__).parent.parent.parent.parent / "samples"

# New testdocs structure has files organized by type
# pdf/text/ for simple text PDFs, pdf/general/ for doc01-doc10, image/ for images
SAMPLE_PDF_DIAGRAM = SAMPLES_DIR / "pdf" / "plans" / "doc2_washington" / "683 Washington Street As-Built (06-26-25) Sheet 1 (Rev 1) (09-14-25).pdf"
if not SAMPLE_PDF_DIAGRAM.exists():
    SAMPLE_PDF_DIAGRAM = SAMPLES_DIR / "diagram.pdf"  # Old location fallback

SAMPLE_PDF_BEGINNING = SAMPLES_DIR / "pdf" / "text" / "inthebeginning.pdf"
if not SAMPLE_PDF_BEGINNING.exists():
    SAMPLE_PDF_BEGINNING = SAMPLES_DIR / "inthebeginning.pdf"  # Old location fallback

# New location: pdf/general/, old location: docs/
_pdf_general_dir = SAMPLES_DIR / "pdf" / "general"
if _pdf_general_dir.exists():
    SAMPLE_PDF_DOCS = list(_pdf_general_dir.glob("*/source.pdf"))
else:
    SAMPLE_PDF_DOCS = list((SAMPLES_DIR / "docs").glob("*/source.pdf"))

SAMPLE_IMAGE = SAMPLES_DIR / "image" / "cat.jpg"
if not SAMPLE_IMAGE.exists():
    SAMPLE_IMAGE = SAMPLES_DIR / "cat.jpg"  # Old location fallback


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


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
def postgres_service(config):
    """Real PostgreSQL service instance."""
    service = PostgresService(config)
    service.connect()
    yield service
    service.close()


@pytest.fixture
async def async_client():
    """
    Async HTTP client for API testing.
    Uses TestClient with FastAPI app directly.
    """
    from api.main import app
    import uuid
    import time
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    
    test_user_id = str(uuid.uuid4())

    # Create an in-memory RSA key and patch the JWKS client used by middleware.
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class _SigningKey:
        def __init__(self, key):
            self.key = key

    class _FakeJwksClient:
        def get_signing_key_from_jwt(self, _token: str):
            return _SigningKey(public_key)

    from api.middleware import jwt_auth as jwt_auth_mod
    jwt_auth_mod.jwks_client = _FakeJwksClient()

    now = int(time.time())
    token = pyjwt.encode(
        {
            "iss": "busibox-authz",
            "sub": test_user_id,
            "aud": "ingest-api",
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "jti": str(uuid.uuid4()),
            "typ": "access",
            "scope": "ingest.write search.read",
            "roles": [
                {"id": str(uuid.uuid4()), "name": "TestRole", "permissions": ["read", "create", "update", "delete"]}
            ],
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        client.headers.update({
            "Authorization": f"Bearer {token}"
        })
        yield client


@pytest.fixture
async def async_client_different_user():
    """
    Async HTTP client for testing unauthorized access.
    """
    from api.main import app
    import uuid
    import time
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    
    different_user_id = str(uuid.uuid4())

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class _SigningKey:
        def __init__(self, key):
            self.key = key

    class _FakeJwksClient:
        def get_signing_key_from_jwt(self, _token: str):
            return _SigningKey(public_key)

    from api.middleware import jwt_auth as jwt_auth_mod
    jwt_auth_mod.jwks_client = _FakeJwksClient()

    now = int(time.time())
    token = pyjwt.encode(
        {
            "iss": "busibox-authz",
            "sub": different_user_id,
            "aud": "ingest-api",
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "jti": str(uuid.uuid4()),
            "typ": "access",
            "scope": "ingest.write search.read",
            "roles": [
                {"id": str(uuid.uuid4()), "name": "TestRole", "permissions": ["read", "create", "update", "delete"]}
            ],
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        client.headers.update({
            "Authorization": f"Bearer {token}"
        })
        yield client


@pytest.fixture
async def test_file_with_markdown(postgres_service, minio_service):
    """
    Create a test file with markdown generated.
    """
    import uuid
    from datetime import datetime
    
    file_id = str(uuid.uuid4())
    user_id = "test-user-123"
    
    # Upload test markdown to MinIO
    markdown_content = "# Test Document\n\nThis is a test."
    markdown_path = f"{user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    # Create database record
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, filename, status, markdown_storage_path, has_markdown, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, file_id, user_id, "test.pdf", "completed", markdown_path, True, datetime.utcnow())
    
    yield {"file_id": file_id, "has_markdown": True}
    
    # Cleanup
    await minio_service.delete_file(markdown_path)
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", file_id)


@pytest.fixture
async def test_file_without_markdown(postgres_service):
    """
    Create a test file without markdown.
    """
    import uuid
    from datetime import datetime
    
    file_id = str(uuid.uuid4())
    user_id = "test-user-123"
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, filename, status, has_markdown, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, file_id, user_id, "test.pdf", "completed", False, datetime.utcnow())
    
    yield {"file_id": file_id, "has_markdown": False}
    
    # Cleanup
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", file_id)


@pytest.fixture
async def test_file_with_images(postgres_service, minio_service):
    """
    Create a test file with extracted images.
    """
    import uuid
    from datetime import datetime
    
    file_id = str(uuid.uuid4())
    user_id = "test-user-123"
    
    # Upload test images to MinIO
    test_image_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
    image_count = 3
    
    for i in range(image_count):
        image_path = f"{user_id}/{file_id}/images/image_{i}.png"
        await minio_service.upload_bytes(test_image_data, image_path, content_type='image/png')
    
    # Upload markdown with image references
    markdown_content = f"# Test Document\n\n"
    for i in range(image_count):
        markdown_content += f"![Image {i}](image_{i}.png)\n\n"
    
    markdown_path = f"{user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    # Create database record
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, filename, status, markdown_storage_path, has_markdown, 
             extracted_images_storage_path, has_extracted_images, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """, file_id, user_id, "test.pdf", "completed", markdown_path, True, 
            f"{user_id}/{file_id}/images", True, datetime.utcnow())
    
    yield {"file_id": file_id, "image_count": image_count}
    
    # Cleanup
    await minio_service.delete_file(markdown_path)
    for i in range(image_count):
        await minio_service.delete_file(f"{user_id}/{file_id}/images/image_{i}.png")
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", file_id)


@pytest.fixture
async def test_file_with_headings(postgres_service, minio_service):
    """
    Create a test file with headings for TOC testing.
    """
    import uuid
    from datetime import datetime
    
    file_id = str(uuid.uuid4())
    user_id = "test-user-123"
    
    # Upload markdown with headings
    markdown_content = """# Main Title

## Section 1

Content for section 1.

### Subsection 1.1

More content.

## Section 2

Content for section 2.
"""
    
    markdown_path = f"{user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    # Create database record
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, filename, status, markdown_storage_path, has_markdown, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, file_id, user_id, "test.pdf", "completed", markdown_path, True, datetime.utcnow())
    
    yield {"file_id": file_id, "has_headings": True}
    
    # Cleanup
    await minio_service.delete_file(markdown_path)
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", file_id)


@pytest.fixture
async def test_file_with_dangerous_content(postgres_service, minio_service):
    """
    Create a test file with potentially dangerous content for sanitization testing.
    """
    import uuid
    from datetime import datetime
    
    file_id = str(uuid.uuid4())
    user_id = "test-user-123"
    
    # Upload markdown with script tags
    markdown_content = """# Test Document

<script>alert('XSS')</script>

Regular content here.
"""
    
    markdown_path = f"{user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    # Create database record
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, filename, status, markdown_storage_path, has_markdown, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, file_id, user_id, "test.pdf", "completed", markdown_path, True, datetime.utcnow())
    
    yield {"file_id": file_id, "has_scripts": True}
    
    # Cleanup
    await minio_service.delete_file(markdown_path)
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", file_id)


@pytest.fixture
async def test_file_without_images(postgres_service, minio_service):
    """
    Create a test file without images.
    """
    import uuid
    from datetime import datetime
    
    file_id = str(uuid.uuid4())
    user_id = "test-user-123"
    
    markdown_content = "# Test Document\n\nText only, no images."
    markdown_path = f"{user_id}/{file_id}/content.md"
    await minio_service.upload_text(markdown_content, markdown_path)
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (id, user_id, filename, status, markdown_storage_path, has_markdown, 
             has_extracted_images, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """, file_id, user_id, "test.pdf", "completed", markdown_path, True, False, datetime.utcnow())
    
    yield {"file_id": file_id, "image_count": 0}
    
    # Cleanup
    await minio_service.delete_file(markdown_path)
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("DELETE FROM ingestion_files WHERE id = $1", file_id)


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
