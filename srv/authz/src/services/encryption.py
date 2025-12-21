"""
Envelope Encryption Service

Provides cryptographic operations for the envelope encryption scheme:
- Master key derivation from environment
- KEK (Key Encryption Key) generation and encryption/decryption
- DEK (Data Encryption Key) generation and wrapping/unwrapping
- File content encryption/decryption

Security Model:
- Master key is derived from AUTHZ_MASTER_KEY environment variable
- KEKs are stored encrypted with the master key in PostgreSQL
- DEKs are generated per-file and wrapped with one or more KEKs
- File content is encrypted with the DEK using AES-256-GCM
"""

import os
import secrets
import hashlib
from typing import Tuple, Optional
from dataclasses import dataclass

import structlog
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = structlog.get_logger()

# Constants
KEY_SIZE = 32  # 256 bits for AES-256
NONCE_SIZE = 12  # 96 bits for GCM
SALT_SIZE = 16  # 128 bits for key derivation
PBKDF2_ITERATIONS = 100_000


@dataclass
class EncryptedData:
    """Container for encrypted data with metadata."""
    ciphertext: bytes
    nonce: bytes
    algorithm: str = "AES-256-GCM"


@dataclass
class WrappedKey:
    """Container for a wrapped (encrypted) key."""
    wrapped_key: bytes
    nonce: bytes
    algorithm: str = "AES-256-GCM"


class EnvelopeEncryptionService:
    """
    Service for envelope encryption operations.
    
    Envelope encryption uses a hierarchy of keys:
    1. Master Key (derived from env) -> encrypts KEKs
    2. KEKs (per role/user) -> encrypt DEKs
    3. DEKs (per file) -> encrypt file content
    """
    
    def __init__(self, master_key_passphrase: Optional[str] = None):
        """
        Initialize the encryption service.
        
        Args:
            master_key_passphrase: Passphrase for deriving the master key.
                                   If not provided, uses AUTHZ_MASTER_KEY env var.
        """
        passphrase = master_key_passphrase or os.getenv("AUTHZ_MASTER_KEY")
        if not passphrase:
            raise ValueError(
                "AUTHZ_MASTER_KEY environment variable must be set for encryption"
            )
        
        # Derive master key from passphrase using a static salt
        # The salt is static so the same passphrase always produces the same key
        # This is acceptable because the passphrase should be high-entropy
        static_salt = b"busibox-authz-master-key-salt-v1"
        self._master_key = self._derive_key(passphrase.encode(), static_salt)
        logger.info("Envelope encryption service initialized")
    
    def _derive_key(self, passphrase: bytes, salt: bytes) -> bytes:
        """Derive a 256-bit key from a passphrase using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_SIZE,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return kdf.derive(passphrase)
    
    def _encrypt(self, key: bytes, plaintext: bytes) -> EncryptedData:
        """Encrypt data using AES-256-GCM."""
        nonce = secrets.token_bytes(NONCE_SIZE)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        return EncryptedData(ciphertext=ciphertext, nonce=nonce)
    
    def _decrypt(self, key: bytes, encrypted: EncryptedData) -> bytes:
        """Decrypt data using AES-256-GCM."""
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(encrypted.nonce, encrypted.ciphertext, None)
    
    # -------------------------------------------------------------------------
    # KEK Operations
    # -------------------------------------------------------------------------
    
    def generate_kek(self) -> bytes:
        """Generate a new random Key Encryption Key."""
        return secrets.token_bytes(KEY_SIZE)
    
    def encrypt_kek(self, kek: bytes) -> bytes:
        """
        Encrypt a KEK with the master key for storage.
        
        Returns bytes that can be stored in the database.
        Format: nonce (12 bytes) || ciphertext
        """
        encrypted = self._encrypt(self._master_key, kek)
        return encrypted.nonce + encrypted.ciphertext
    
    def decrypt_kek(self, encrypted_kek: bytes) -> bytes:
        """
        Decrypt a stored KEK using the master key.
        
        Args:
            encrypted_kek: The encrypted KEK as stored (nonce || ciphertext)
        """
        nonce = encrypted_kek[:NONCE_SIZE]
        ciphertext = encrypted_kek[NONCE_SIZE:]
        encrypted = EncryptedData(ciphertext=ciphertext, nonce=nonce)
        return self._decrypt(self._master_key, encrypted)
    
    # -------------------------------------------------------------------------
    # DEK Operations
    # -------------------------------------------------------------------------
    
    def generate_dek(self) -> bytes:
        """Generate a new random Data Encryption Key."""
        return secrets.token_bytes(KEY_SIZE)
    
    def wrap_dek(self, dek: bytes, kek: bytes) -> bytes:
        """
        Wrap (encrypt) a DEK with a KEK.
        
        Args:
            dek: The Data Encryption Key to wrap
            kek: The Key Encryption Key to use for wrapping
            
        Returns:
            Wrapped DEK as bytes (nonce || ciphertext)
        """
        encrypted = self._encrypt(kek, dek)
        return encrypted.nonce + encrypted.ciphertext
    
    def unwrap_dek(self, wrapped_dek: bytes, kek: bytes) -> bytes:
        """
        Unwrap (decrypt) a DEK using a KEK.
        
        Args:
            wrapped_dek: The wrapped DEK (nonce || ciphertext)
            kek: The Key Encryption Key to use for unwrapping
            
        Returns:
            The unwrapped DEK
        """
        nonce = wrapped_dek[:NONCE_SIZE]
        ciphertext = wrapped_dek[NONCE_SIZE:]
        encrypted = EncryptedData(ciphertext=ciphertext, nonce=nonce)
        return self._decrypt(kek, encrypted)
    
    # -------------------------------------------------------------------------
    # File Content Encryption
    # -------------------------------------------------------------------------
    
    def encrypt_file_content(self, content: bytes, dek: bytes) -> bytes:
        """
        Encrypt file content with a DEK.
        
        Args:
            content: The file content to encrypt
            dek: The Data Encryption Key
            
        Returns:
            Encrypted content as bytes (nonce || ciphertext)
        """
        encrypted = self._encrypt(dek, content)
        return encrypted.nonce + encrypted.ciphertext
    
    def decrypt_file_content(self, encrypted_content: bytes, dek: bytes) -> bytes:
        """
        Decrypt file content using a DEK.
        
        Args:
            encrypted_content: The encrypted content (nonce || ciphertext)
            dek: The Data Encryption Key
            
        Returns:
            Decrypted file content
        """
        nonce = encrypted_content[:NONCE_SIZE]
        ciphertext = encrypted_content[NONCE_SIZE:]
        encrypted = EncryptedData(ciphertext=ciphertext, nonce=nonce)
        return self._decrypt(dek, encrypted)
    
    # -------------------------------------------------------------------------
    # High-Level Operations
    # -------------------------------------------------------------------------
    
    def encrypt_for_storage(
        self,
        content: bytes,
        keks: list[Tuple[str, bytes]],  # List of (kek_id, encrypted_kek)
    ) -> Tuple[bytes, list[Tuple[str, bytes]]]:
        """
        Encrypt content for storage with envelope encryption.
        
        Args:
            content: The file content to encrypt
            keks: List of (kek_id, encrypted_kek) tuples for authorized roles/users
            
        Returns:
            Tuple of (encrypted_content, wrapped_deks)
            where wrapped_deks is list of (kek_id, wrapped_dek)
        """
        # Generate a new DEK
        dek = self.generate_dek()
        
        # Encrypt content with DEK
        encrypted_content = self.encrypt_file_content(content, dek)
        
        # Wrap DEK with each KEK
        wrapped_deks = []
        for kek_id, encrypted_kek in keks:
            kek = self.decrypt_kek(encrypted_kek)
            wrapped_dek = self.wrap_dek(dek, kek)
            wrapped_deks.append((kek_id, wrapped_dek))
        
        return encrypted_content, wrapped_deks
    
    def decrypt_from_storage(
        self,
        encrypted_content: bytes,
        wrapped_dek: bytes,
        encrypted_kek: bytes,
    ) -> bytes:
        """
        Decrypt content from storage.
        
        Args:
            encrypted_content: The encrypted file content
            wrapped_dek: The wrapped DEK for this user's access
            encrypted_kek: The encrypted KEK for unwrapping the DEK
            
        Returns:
            Decrypted file content
        """
        # Decrypt KEK
        kek = self.decrypt_kek(encrypted_kek)
        
        # Unwrap DEK
        dek = self.unwrap_dek(wrapped_dek, kek)
        
        # Decrypt content
        return self.decrypt_file_content(encrypted_content, dek)


# Singleton instance (initialized lazily)
_encryption_service: Optional[EnvelopeEncryptionService] = None


def get_encryption_service() -> EnvelopeEncryptionService:
    """Get the singleton encryption service instance."""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EnvelopeEncryptionService()
    return _encryption_service

