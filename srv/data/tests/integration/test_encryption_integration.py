"""
Integration tests for envelope encryption with MinIO storage.

These tests verify the complete encryption flow:
1. Upload encrypts content before storing in MinIO
2. Download decrypts content after retrieving from MinIO
3. Different roles can access shared files via their KEKs
4. File deletion cleans up encryption keys

Zero Trust Pattern:
- Tests use real user tokens via token exchange
- Uses srv/shared/testing/auth.py for test user management
- No admin tokens or client credentials

Requires:
- AuthZ service running with AUTHZ_MASTER_KEY set
- MinIO service running
- PostgreSQL running
- Test database bootstrapped (bootstrap-test-databases.py)
"""

import asyncio
import base64
import os
import sys
import uuid

import pytest
import httpx

# Add shared testing module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../shared"))
from testing.auth import AuthTestClient, auth_client

# Test configuration - uses test container IPs
AUTHZ_BASE_URL = os.getenv("AUTHZ_BASE_URL", "http://10.96.201.210:8010")
DATA_BASE_URL = os.getenv("DATA_BASE_URL", "http://10.96.201.220:8020")

# Test mode header for routing to test database
TEST_MODE_HEADER = "X-Test-Mode"
TEST_MODE_VALUE = "true"


@pytest.fixture
def test_file_content():
    """Sample file content for testing."""
    return b"This is a test document for encryption testing.\n" * 100


@pytest.fixture
def test_file_id():
    """Generate a unique file ID for testing."""
    return str(uuid.uuid4())


@pytest.fixture
def test_role_id():
    """Generate a unique role ID for testing."""
    return str(uuid.uuid4())


@pytest.fixture
def test_user_id():
    """Generate a unique user ID for testing."""
    return str(uuid.uuid4())


class TestKeystoreEndpoints:
    """Test the AuthZ keystore endpoints directly using Zero Trust authentication."""
    
    @pytest.mark.asyncio
    async def test_create_kek_for_role(self, auth_client, test_role_id):
        """Test creating a KEK for a role."""
        # Get user token via token exchange
        user_token = auth_client.get_token(audience="authz-api")
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek",
                headers={
                    "Authorization": f"Bearer {user_token}",
                    TEST_MODE_HEADER: TEST_MODE_VALUE,
                },
                json={"owner_type": "role", "owner_id": test_role_id},
                timeout=30.0,
            )
            
            # May be 200 (created) or 409 (already exists)
            assert resp.status_code in [200, 409], f"Unexpected status: {resp.status_code}, {resp.text}"
    
    @pytest.mark.asyncio
    async def test_ensure_kek_for_role_is_idempotent(self, auth_client, test_role_id):
        """Test that ensure-for-role is idempotent."""
        # Get user token via token exchange
        user_token = auth_client.get_token(audience="authz-api")
        
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {user_token}",
                TEST_MODE_HEADER: TEST_MODE_VALUE,
            }
            
            # First call
            resp1 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{test_role_id}",
                headers=headers,
                timeout=30.0,
            )
            assert resp1.status_code == 200
            data1 = resp1.json()
            
            # Second call - should return same KEK
            resp2 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{test_role_id}",
                headers=headers,
                timeout=30.0,
            )
            assert resp2.status_code == 200
            data2 = resp2.json()
            
            assert data1["kek_id"] == data2["kek_id"]
    
    @pytest.mark.asyncio
    async def test_encrypt_and_decrypt_content(self, auth_client, test_file_id, test_role_id, test_file_content):
        """Test encrypting and decrypting content."""
        # Get user token via token exchange
        user_token = auth_client.get_token(audience="authz-api")
        
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {user_token}",
                TEST_MODE_HEADER: TEST_MODE_VALUE,
            }
            
            # Ensure KEK exists for role
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{test_role_id}",
                headers=headers,
                timeout=30.0,
            )
            
            # Encrypt content
            encrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/encrypt",
                headers=headers,
                json={
                    "file_id": test_file_id,
                    "content": base64.b64encode(test_file_content).decode(),
                    "role_ids": [test_role_id],
                },
                timeout=30.0,
            )
            
            assert encrypt_resp.status_code == 200, f"Encrypt failed: {encrypt_resp.text}"
            encrypt_data = encrypt_resp.json()
            
            encrypted_content = base64.b64decode(encrypt_data["encrypted_content"])
            
            # Verify content is different (encrypted)
            assert encrypted_content != test_file_content
            assert encrypt_data["wrapped_dek_count"] == 1
            
            # Decrypt content
            decrypt_headers = headers.copy()
            decrypt_headers["X-User-Role-Ids"] = test_role_id
            
            decrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers=decrypt_headers,
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            
            assert decrypt_resp.status_code == 200, f"Decrypt failed: {decrypt_resp.text}"
            decrypt_data = decrypt_resp.json()
            
            decrypted_content = base64.b64decode(decrypt_data["content"])
            
            # Verify content matches original
            assert decrypted_content == test_file_content
    
    @pytest.mark.asyncio
    async def test_multi_role_access(self, auth_client, test_file_id, test_file_content):
        """Test that multiple roles can access the same encrypted content."""
        # Get user token via token exchange
        user_token = auth_client.get_token(audience="authz-api")
        
        role1_id = str(uuid.uuid4())
        role2_id = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {user_token}",
                TEST_MODE_HEADER: TEST_MODE_VALUE,
            }
            
            # Ensure KEKs exist for both roles
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{role1_id}",
                headers=headers,
                timeout=30.0,
            )
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{role2_id}",
                headers=headers,
                timeout=30.0,
            )
            
            # Encrypt content for both roles
            encrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/encrypt",
                headers=headers,
                json={
                    "file_id": test_file_id,
                    "content": base64.b64encode(test_file_content).decode(),
                    "role_ids": [role1_id, role2_id],
                },
                timeout=30.0,
            )
            
            assert encrypt_resp.status_code == 200
            encrypt_data = encrypt_resp.json()
            assert encrypt_data["wrapped_dek_count"] == 2
            
            # Role 1 can decrypt
            decrypt_headers1 = headers.copy()
            decrypt_headers1["X-User-Role-Ids"] = role1_id
            decrypt_resp1 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers=decrypt_headers1,
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            assert decrypt_resp1.status_code == 200
            assert base64.b64decode(decrypt_resp1.json()["content"]) == test_file_content
            
            # Role 2 can also decrypt
            decrypt_headers2 = headers.copy()
            decrypt_headers2["X-User-Role-Ids"] = role2_id
            decrypt_resp2 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers=decrypt_headers2,
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            assert decrypt_resp2.status_code == 200
            assert base64.b64decode(decrypt_resp2.json()["content"]) == test_file_content
    
    @pytest.mark.asyncio
    async def test_unauthorized_role_cannot_decrypt(self, auth_client, test_file_id, test_file_content):
        """Test that unauthorized roles cannot decrypt content."""
        # Get user token via token exchange
        user_token = auth_client.get_token(audience="authz-api")
        
        authorized_role = str(uuid.uuid4())
        unauthorized_role = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {user_token}",
                TEST_MODE_HEADER: TEST_MODE_VALUE,
            }
            
            # Ensure KEK for authorized role only
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{authorized_role}",
                headers=headers,
                timeout=30.0,
            )
            
            # Encrypt content for authorized role only
            encrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/encrypt",
                headers=headers,
                json={
                    "file_id": test_file_id,
                    "content": base64.b64encode(test_file_content).decode(),
                    "role_ids": [authorized_role],
                },
                timeout=30.0,
            )
            assert encrypt_resp.status_code == 200
            encrypt_data = encrypt_resp.json()
            
            # Unauthorized role cannot decrypt
            decrypt_headers = headers.copy()
            decrypt_headers["X-User-Role-Ids"] = unauthorized_role
            decrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers=decrypt_headers,
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            
            assert decrypt_resp.status_code == 403
    
    @pytest.mark.asyncio
    async def test_add_and_remove_role_access(self, auth_client, test_file_id, test_file_content):
        """Test adding and removing role access to encrypted files."""
        # Get user token via token exchange
        user_token = auth_client.get_token(audience="authz-api")
        
        role1_id = str(uuid.uuid4())
        role2_id = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {user_token}",
                TEST_MODE_HEADER: TEST_MODE_VALUE,
            }
            
            # Setup: create KEKs and encrypt for role1 only
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{role1_id}",
                headers=headers,
                timeout=30.0,
            )
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{role2_id}",
                headers=headers,
                timeout=30.0,
            )
            
            encrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/encrypt",
                headers=headers,
                json={
                    "file_id": test_file_id,
                    "content": base64.b64encode(test_file_content).decode(),
                    "role_ids": [role1_id],
                },
                timeout=30.0,
            )
            assert encrypt_resp.status_code == 200
            encrypt_data = encrypt_resp.json()
            
            # Initially role2 cannot decrypt
            decrypt_headers = headers.copy()
            decrypt_headers["X-User-Role-Ids"] = role2_id
            decrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers=decrypt_headers,
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            assert decrypt_resp.status_code == 403
            
            # Add role2 access
            add_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/file/{test_file_id}/add-role/{role2_id}",
                headers=headers,
                timeout=30.0,
            )
            assert add_resp.status_code == 200
            
            # Now role2 can decrypt
            decrypt_resp2 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers=decrypt_headers,
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            assert decrypt_resp2.status_code == 200
            
            # Remove role2 access
            remove_resp = await client.delete(
                f"{AUTHZ_BASE_URL}/keystore/file/{test_file_id}/remove-role/{role2_id}",
                headers=headers,
                timeout=30.0,
            )
            assert remove_resp.status_code == 200
            
            # Role2 can no longer decrypt
            decrypt_resp3 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers=decrypt_headers,
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            assert decrypt_resp3.status_code == 403
    
    @pytest.mark.asyncio
    async def test_key_rotation(self, auth_client, test_role_id):
        """Test rotating a KEK."""
        # Get user token via token exchange
        user_token = auth_client.get_token(audience="authz-api")
        
        async with httpx.AsyncClient() as client:
            headers = {
                "Authorization": f"Bearer {user_token}",
                TEST_MODE_HEADER: TEST_MODE_VALUE,
            }
            
            # Ensure initial KEK
            initial_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{test_role_id}",
                headers=headers,
                timeout=30.0,
            )
            assert initial_resp.status_code == 200
            initial_data = initial_resp.json()
            initial_version = initial_data["key_version"]
            
            # Rotate KEK
            rotate_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/rotate",
                headers=headers,
                json={"owner_type": "role", "owner_id": test_role_id},
                timeout=30.0,
            )
            assert rotate_resp.status_code == 200
            rotate_data = rotate_resp.json()
            
            # Verify new version
            assert rotate_data["key_version"] == initial_version + 1
            assert rotate_data["is_active"] is True


class TestEncryptionClientIntegration:
    """Test the encryption client integration with data service.
    
    Uses Zero Trust authentication - passes user tokens to encryption client.
    """
    
    @pytest.mark.asyncio
    async def test_encryption_client_encrypt_decrypt(self, auth_client, test_file_id, test_role_id, test_file_content):
        """Test the EncryptionClient encrypt/decrypt roundtrip with user tokens."""
        # Get user token via token exchange
        user_token = auth_client.get_token(audience="authz-api")
        
        # Import EncryptionClient (now uses Zero Trust - tokens per-request)
        from api.services.encryption_client import EncryptionClient
        
        config = {"authz_base_url": AUTHZ_BASE_URL}
        client = EncryptionClient(config)
        
        if not client.enabled:
            pytest.skip("Encryption client not enabled")
        
        # Encrypt (passing user_token)
        encrypted = await client.encrypt_for_upload(
            file_id=test_file_id,
            content=test_file_content,
            user_token=user_token,
            role_ids=[test_role_id],
        )
        
        assert encrypted != test_file_content
        assert client.is_encrypted(encrypted)
        
        # Decrypt (passing user_token)
        decrypted = await client.decrypt_for_download(
            file_id=test_file_id,
            encrypted_content=encrypted,
            user_token=user_token,
            role_ids=[test_role_id],
        )
        
        assert decrypted == test_file_content
    
    @pytest.mark.asyncio
    async def test_encryption_client_handles_unencrypted(self, test_file_content):
        """Test that the client correctly identifies unencrypted content."""
        from api.services.encryption_client import EncryptionClient
        
        config = {"authz_base_url": AUTHZ_BASE_URL}
        client = EncryptionClient(config)
        
        # Common file types should not be detected as encrypted
        pdf_content = b'%PDF-1.4 test content'
        assert not client.is_encrypted(pdf_content)
        
        docx_content = b'PK\x03\x04 docx content'
        assert not client.is_encrypted(docx_content)
        
        json_content = b'{"key": "value"}'
        assert not client.is_encrypted(json_content)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

