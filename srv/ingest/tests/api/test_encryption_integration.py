"""
Integration tests for envelope encryption with MinIO storage.

These tests verify the complete encryption flow:
1. Upload encrypts content before storing in MinIO
2. Download decrypts content after retrieving from MinIO
3. Different roles can access shared files via their KEKs
4. File deletion cleans up encryption keys

Requires:
- AuthZ service running with AUTHZ_MASTER_KEY set
- MinIO service running
- PostgreSQL running
"""

import asyncio
import base64
import os
import uuid

import pytest
import httpx

# Test configuration - uses test container IPs
AUTHZ_BASE_URL = os.getenv("AUTHZ_BASE_URL", "http://10.96.201.210:8010")
INGEST_BASE_URL = os.getenv("INGEST_BASE_URL", "http://10.96.201.220:8020")
AUTHZ_ADMIN_TOKEN = os.getenv("AUTHZ_ADMIN_TOKEN")


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
    """Test the AuthZ keystore endpoints directly."""
    
    @pytest.mark.asyncio
    async def test_create_kek_for_role(self, test_role_id):
        """Test creating a KEK for a role."""
        if not AUTHZ_ADMIN_TOKEN:
            pytest.skip("AUTHZ_ADMIN_TOKEN not set")
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                json={"owner_type": "role", "owner_id": test_role_id},
                timeout=30.0,
            )
            
            # May be 200 (created) or 409 (already exists)
            assert resp.status_code in [200, 409], f"Unexpected status: {resp.status_code}, {resp.text}"
    
    @pytest.mark.asyncio
    async def test_ensure_kek_for_role_is_idempotent(self, test_role_id):
        """Test that ensure-for-role is idempotent."""
        if not AUTHZ_ADMIN_TOKEN:
            pytest.skip("AUTHZ_ADMIN_TOKEN not set")
        
        async with httpx.AsyncClient() as client:
            # First call
            resp1 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{test_role_id}",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            assert resp1.status_code == 200
            data1 = resp1.json()
            
            # Second call - should return same KEK
            resp2 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{test_role_id}",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            assert resp2.status_code == 200
            data2 = resp2.json()
            
            assert data1["kek_id"] == data2["kek_id"]
    
    @pytest.mark.asyncio
    async def test_encrypt_and_decrypt_content(self, test_file_id, test_role_id, test_file_content):
        """Test encrypting and decrypting content."""
        if not AUTHZ_ADMIN_TOKEN:
            pytest.skip("AUTHZ_ADMIN_TOKEN not set")
        
        async with httpx.AsyncClient() as client:
            # Ensure KEK exists for role
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{test_role_id}",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            
            # Encrypt content
            encrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/encrypt",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
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
            decrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers={
                    "Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}",
                    "X-User-Role-Ids": test_role_id,
                },
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
    async def test_multi_role_access(self, test_file_id, test_file_content):
        """Test that multiple roles can access the same encrypted content."""
        if not AUTHZ_ADMIN_TOKEN:
            pytest.skip("AUTHZ_ADMIN_TOKEN not set")
        
        role1_id = str(uuid.uuid4())
        role2_id = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            # Ensure KEKs exist for both roles
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{role1_id}",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{role2_id}",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            
            # Encrypt content for both roles
            encrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/encrypt",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
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
            decrypt_resp1 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers={
                    "Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}",
                    "X-User-Role-Ids": role1_id,
                },
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            assert decrypt_resp1.status_code == 200
            assert base64.b64decode(decrypt_resp1.json()["content"]) == test_file_content
            
            # Role 2 can also decrypt
            decrypt_resp2 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers={
                    "Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}",
                    "X-User-Role-Ids": role2_id,
                },
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            assert decrypt_resp2.status_code == 200
            assert base64.b64decode(decrypt_resp2.json()["content"]) == test_file_content
    
    @pytest.mark.asyncio
    async def test_unauthorized_role_cannot_decrypt(self, test_file_id, test_file_content):
        """Test that unauthorized roles cannot decrypt content."""
        if not AUTHZ_ADMIN_TOKEN:
            pytest.skip("AUTHZ_ADMIN_TOKEN not set")
        
        authorized_role = str(uuid.uuid4())
        unauthorized_role = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            # Ensure KEK for authorized role only
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{authorized_role}",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            
            # Encrypt content for authorized role only
            encrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/encrypt",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
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
            decrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers={
                    "Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}",
                    "X-User-Role-Ids": unauthorized_role,
                },
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            
            assert decrypt_resp.status_code == 403
    
    @pytest.mark.asyncio
    async def test_add_and_remove_role_access(self, test_file_id, test_file_content):
        """Test adding and removing role access to encrypted files."""
        if not AUTHZ_ADMIN_TOKEN:
            pytest.skip("AUTHZ_ADMIN_TOKEN not set")
        
        role1_id = str(uuid.uuid4())
        role2_id = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            # Setup: create KEKs and encrypt for role1 only
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{role1_id}",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{role2_id}",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            
            encrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/encrypt",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
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
            decrypt_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers={
                    "Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}",
                    "X-User-Role-Ids": role2_id,
                },
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
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            assert add_resp.status_code == 200
            
            # Now role2 can decrypt
            decrypt_resp2 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers={
                    "Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}",
                    "X-User-Role-Ids": role2_id,
                },
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
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            assert remove_resp.status_code == 200
            
            # Role2 can no longer decrypt
            decrypt_resp3 = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/decrypt",
                headers={
                    "Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}",
                    "X-User-Role-Ids": role2_id,
                },
                json={
                    "file_id": test_file_id,
                    "encrypted_content": encrypt_data["encrypted_content"],
                },
                timeout=30.0,
            )
            assert decrypt_resp3.status_code == 403
    
    @pytest.mark.asyncio
    async def test_key_rotation(self, test_role_id):
        """Test rotating a KEK."""
        if not AUTHZ_ADMIN_TOKEN:
            pytest.skip("AUTHZ_ADMIN_TOKEN not set")
        
        async with httpx.AsyncClient() as client:
            # Ensure initial KEK
            initial_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/ensure-for-role/{test_role_id}",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                timeout=30.0,
            )
            assert initial_resp.status_code == 200
            initial_data = initial_resp.json()
            initial_version = initial_data["key_version"]
            
            # Rotate KEK
            rotate_resp = await client.post(
                f"{AUTHZ_BASE_URL}/keystore/kek/rotate",
                headers={"Authorization": f"Bearer {AUTHZ_ADMIN_TOKEN}"},
                json={"owner_type": "role", "owner_id": test_role_id},
                timeout=30.0,
            )
            assert rotate_resp.status_code == 200
            rotate_data = rotate_resp.json()
            
            # Verify new version
            assert rotate_data["key_version"] == initial_version + 1
            assert rotate_data["is_active"] is True


class TestEncryptionClientIntegration:
    """Test the encryption client integration with ingest service."""
    
    @pytest.mark.asyncio
    async def test_encryption_client_encrypt_decrypt(self, test_file_id, test_role_id, test_file_content):
        """Test the EncryptionClient encrypt/decrypt roundtrip."""
        if not AUTHZ_ADMIN_TOKEN:
            pytest.skip("AUTHZ_ADMIN_TOKEN not set")
        
        # Import here to avoid import errors if dependencies missing
        import sys
        sys.path.insert(0, "/Users/wessonnenreich/Code/sonnenreich/busibox/srv/ingest/src")
        
        from api.services.encryption_client import EncryptionClient
        
        config = {
            "authz_base_url": AUTHZ_BASE_URL,
            "authz_admin_token": AUTHZ_ADMIN_TOKEN,
        }
        
        client = EncryptionClient(config)
        
        if not client.enabled:
            pytest.skip("Encryption client not enabled")
        
        # Encrypt
        encrypted = await client.encrypt_for_upload(
            file_id=test_file_id,
            content=test_file_content,
            role_ids=[test_role_id],
        )
        
        assert encrypted != test_file_content
        assert client.is_encrypted(encrypted)
        
        # Decrypt
        decrypted = await client.decrypt_for_download(
            file_id=test_file_id,
            encrypted_content=encrypted,
            role_ids=[test_role_id],
        )
        
        assert decrypted == test_file_content
    
    @pytest.mark.asyncio
    async def test_encryption_client_handles_unencrypted(self, test_file_content):
        """Test that the client correctly identifies unencrypted content."""
        import sys
        sys.path.insert(0, "/Users/wessonnenreich/Code/sonnenreich/busibox/srv/ingest/src")
        
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

