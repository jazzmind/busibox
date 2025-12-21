"""
Tests for the envelope encryption service.
"""

import os
import pytest
import secrets

# Set master key before importing encryption service
os.environ["AUTHZ_MASTER_KEY"] = "test-master-key-for-envelope-encryption-do-not-use-in-prod"

from services.encryption import EnvelopeEncryptionService, KEY_SIZE, NONCE_SIZE


@pytest.fixture
def encryption_service():
    """Create an encryption service instance for testing."""
    return EnvelopeEncryptionService()


class TestKEKOperations:
    """Tests for Key Encryption Key operations."""
    
    def test_generate_kek_returns_correct_size(self, encryption_service):
        """Generated KEK should be 256 bits (32 bytes)."""
        kek = encryption_service.generate_kek()
        assert len(kek) == KEY_SIZE
    
    def test_generate_kek_returns_random_bytes(self, encryption_service):
        """Each generated KEK should be unique."""
        kek1 = encryption_service.generate_kek()
        kek2 = encryption_service.generate_kek()
        assert kek1 != kek2
    
    def test_encrypt_decrypt_kek_roundtrip(self, encryption_service):
        """Encrypting and decrypting a KEK should return the original."""
        original_kek = encryption_service.generate_kek()
        encrypted = encryption_service.encrypt_kek(original_kek)
        decrypted = encryption_service.decrypt_kek(encrypted)
        assert decrypted == original_kek
    
    def test_encrypted_kek_includes_nonce(self, encryption_service):
        """Encrypted KEK should include nonce prefix."""
        kek = encryption_service.generate_kek()
        encrypted = encryption_service.encrypt_kek(kek)
        # Should be: nonce (12 bytes) + ciphertext (32 bytes + 16 byte tag)
        assert len(encrypted) == NONCE_SIZE + KEY_SIZE + 16
    
    def test_same_kek_encrypts_differently_each_time(self, encryption_service):
        """Same KEK should encrypt to different ciphertext due to random nonce."""
        kek = encryption_service.generate_kek()
        encrypted1 = encryption_service.encrypt_kek(kek)
        encrypted2 = encryption_service.encrypt_kek(kek)
        assert encrypted1 != encrypted2
        # But both should decrypt to the same value
        assert encryption_service.decrypt_kek(encrypted1) == kek
        assert encryption_service.decrypt_kek(encrypted2) == kek


class TestDEKOperations:
    """Tests for Data Encryption Key operations."""
    
    def test_generate_dek_returns_correct_size(self, encryption_service):
        """Generated DEK should be 256 bits (32 bytes)."""
        dek = encryption_service.generate_dek()
        assert len(dek) == KEY_SIZE
    
    def test_wrap_unwrap_dek_roundtrip(self, encryption_service):
        """Wrapping and unwrapping a DEK should return the original."""
        kek = encryption_service.generate_kek()
        dek = encryption_service.generate_dek()
        
        wrapped = encryption_service.wrap_dek(dek, kek)
        unwrapped = encryption_service.unwrap_dek(wrapped, kek)
        
        assert unwrapped == dek
    
    def test_wrapped_dek_includes_nonce(self, encryption_service):
        """Wrapped DEK should include nonce prefix."""
        kek = encryption_service.generate_kek()
        dek = encryption_service.generate_dek()
        wrapped = encryption_service.wrap_dek(dek, kek)
        # Should be: nonce (12 bytes) + ciphertext (32 bytes + 16 byte tag)
        assert len(wrapped) == NONCE_SIZE + KEY_SIZE + 16
    
    def test_wrong_kek_fails_to_unwrap(self, encryption_service):
        """Unwrapping with wrong KEK should fail."""
        kek1 = encryption_service.generate_kek()
        kek2 = encryption_service.generate_kek()
        dek = encryption_service.generate_dek()
        
        wrapped = encryption_service.wrap_dek(dek, kek1)
        
        with pytest.raises(Exception):  # cryptography raises InvalidTag
            encryption_service.unwrap_dek(wrapped, kek2)


class TestFileContentEncryption:
    """Tests for file content encryption."""
    
    def test_encrypt_decrypt_content_roundtrip(self, encryption_service):
        """Encrypting and decrypting content should return the original."""
        dek = encryption_service.generate_dek()
        content = b"Hello, this is a test document content!"
        
        encrypted = encryption_service.encrypt_file_content(content, dek)
        decrypted = encryption_service.decrypt_file_content(encrypted, dek)
        
        assert decrypted == content
    
    def test_encrypt_large_content(self, encryption_service):
        """Should handle large content (e.g., multi-MB files)."""
        dek = encryption_service.generate_dek()
        content = secrets.token_bytes(1024 * 1024)  # 1 MB
        
        encrypted = encryption_service.encrypt_file_content(content, dek)
        decrypted = encryption_service.decrypt_file_content(encrypted, dek)
        
        assert decrypted == content
    
    def test_encrypted_content_includes_nonce(self, encryption_service):
        """Encrypted content should include nonce prefix."""
        dek = encryption_service.generate_dek()
        content = b"Test content"
        encrypted = encryption_service.encrypt_file_content(content, dek)
        # Should be: nonce (12 bytes) + ciphertext (content + 16 byte tag)
        assert len(encrypted) == NONCE_SIZE + len(content) + 16
    
    def test_wrong_dek_fails_to_decrypt(self, encryption_service):
        """Decrypting with wrong DEK should fail."""
        dek1 = encryption_service.generate_dek()
        dek2 = encryption_service.generate_dek()
        content = b"Secret content"
        
        encrypted = encryption_service.encrypt_file_content(content, dek1)
        
        with pytest.raises(Exception):
            encryption_service.decrypt_file_content(encrypted, dek2)


class TestEnvelopeEncryption:
    """Tests for the complete envelope encryption flow."""
    
    def test_encrypt_for_storage_with_single_kek(self, encryption_service):
        """Should encrypt content and wrap DEK for single KEK."""
        kek = encryption_service.generate_kek()
        encrypted_kek = encryption_service.encrypt_kek(kek)
        content = b"Document content"
        
        encrypted_content, wrapped_deks = encryption_service.encrypt_for_storage(
            content, [("kek-1", encrypted_kek)]
        )
        
        assert len(wrapped_deks) == 1
        assert wrapped_deks[0][0] == "kek-1"
        assert len(wrapped_deks[0][1]) == NONCE_SIZE + KEY_SIZE + 16
    
    def test_encrypt_for_storage_with_multiple_keks(self, encryption_service):
        """Should wrap DEK for multiple KEKs (multi-role access)."""
        kek1 = encryption_service.generate_kek()
        kek2 = encryption_service.generate_kek()
        encrypted_kek1 = encryption_service.encrypt_kek(kek1)
        encrypted_kek2 = encryption_service.encrypt_kek(kek2)
        content = b"Shared document"
        
        encrypted_content, wrapped_deks = encryption_service.encrypt_for_storage(
            content,
            [("kek-role-1", encrypted_kek1), ("kek-role-2", encrypted_kek2)]
        )
        
        assert len(wrapped_deks) == 2
        
        # Each wrapped DEK should decrypt to the same DEK
        unwrapped1 = encryption_service.unwrap_dek(wrapped_deks[0][1], kek1)
        unwrapped2 = encryption_service.unwrap_dek(wrapped_deks[1][1], kek2)
        assert unwrapped1 == unwrapped2
    
    def test_decrypt_from_storage_roundtrip(self, encryption_service):
        """Full roundtrip: encrypt for storage, then decrypt."""
        kek = encryption_service.generate_kek()
        encrypted_kek = encryption_service.encrypt_kek(kek)
        content = b"Top secret information"
        
        encrypted_content, wrapped_deks = encryption_service.encrypt_for_storage(
            content, [("kek-1", encrypted_kek)]
        )
        
        decrypted = encryption_service.decrypt_from_storage(
            encrypted_content,
            wrapped_deks[0][1],  # wrapped DEK
            encrypted_kek,
        )
        
        assert decrypted == content
    
    def test_multi_role_access_works(self, encryption_service):
        """Multiple roles should be able to decrypt the same content."""
        kek1 = encryption_service.generate_kek()
        kek2 = encryption_service.generate_kek()
        encrypted_kek1 = encryption_service.encrypt_kek(kek1)
        encrypted_kek2 = encryption_service.encrypt_kek(kek2)
        content = b"Shared secret"
        
        encrypted_content, wrapped_deks = encryption_service.encrypt_for_storage(
            content,
            [("role-1", encrypted_kek1), ("role-2", encrypted_kek2)]
        )
        
        # Role 1 can decrypt
        decrypted1 = encryption_service.decrypt_from_storage(
            encrypted_content,
            wrapped_deks[0][1],
            encrypted_kek1,
        )
        
        # Role 2 can also decrypt
        decrypted2 = encryption_service.decrypt_from_storage(
            encrypted_content,
            wrapped_deks[1][1],
            encrypted_kek2,
        )
        
        assert decrypted1 == content
        assert decrypted2 == content


class TestMasterKeyDerivation:
    """Tests for master key handling."""
    
    def test_same_passphrase_produces_same_key(self):
        """Same passphrase should produce consistent encryption results."""
        passphrase = "test-passphrase-123"
        
        svc1 = EnvelopeEncryptionService(passphrase)
        svc2 = EnvelopeEncryptionService(passphrase)
        
        kek = secrets.token_bytes(32)
        
        # Encrypt with svc1
        encrypted = svc1.encrypt_kek(kek)
        
        # Decrypt with svc2 (same passphrase)
        decrypted = svc2.decrypt_kek(encrypted)
        
        assert decrypted == kek
    
    def test_different_passphrase_fails_decryption(self):
        """Different passphrases should produce different master keys."""
        svc1 = EnvelopeEncryptionService("passphrase-1")
        svc2 = EnvelopeEncryptionService("passphrase-2")
        
        kek = secrets.token_bytes(32)
        encrypted = svc1.encrypt_kek(kek)
        
        with pytest.raises(Exception):
            svc2.decrypt_kek(encrypted)
    
    def test_missing_master_key_raises_error(self):
        """Service should raise error if AUTHZ_MASTER_KEY not set."""
        # Temporarily unset the env var
        original = os.environ.get("AUTHZ_MASTER_KEY")
        try:
            del os.environ["AUTHZ_MASTER_KEY"]
            with pytest.raises(ValueError, match="AUTHZ_MASTER_KEY"):
                EnvelopeEncryptionService()
        finally:
            if original:
                os.environ["AUTHZ_MASTER_KEY"] = original

