"""
Encryption utilities for deployment secrets.

Uses AES-256-GCM, matching the ai-portal encryption format (src/lib/crypto.ts).
The same SECRETS_ENCRYPTION_KEY must be used in both services so encrypted
values are portable.

Format: base64( IV[16] + AuthTag[16] + Ciphertext )
Key derivation: PBKDF2(key, salt='deployment-secrets', iterations=100000, dkLen=32, hash=sha512)
"""

import os
import base64
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

IV_LENGTH = 16
AUTH_TAG_LENGTH = 16
KEY_LENGTH = 32
PBKDF2_ITERATIONS = 100_000
PBKDF2_SALT = b'deployment-secrets'


def _get_encryption_key() -> bytes:
    """Derive a 256-bit key from the SECRETS_ENCRYPTION_KEY env var.
    
    Uses PBKDF2 with the same parameters as ai-portal's crypto.ts
    to ensure encrypted values are interchangeable.
    """
    raw_key = os.getenv('SECRETS_ENCRYPTION_KEY') or os.getenv('ENCRYPTION_KEY')
    if not raw_key:
        raise RuntimeError('SECRETS_ENCRYPTION_KEY or ENCRYPTION_KEY must be set')

    return hashlib.pbkdf2_hmac(
        'sha512',
        raw_key.encode('utf-8'),
        PBKDF2_SALT,
        PBKDF2_ITERATIONS,
        dklen=KEY_LENGTH,
    )


def encrypt(plaintext: str) -> str:
    """Encrypt a string using AES-256-GCM.
    
    Returns base64-encoded: IV (16 bytes) + AuthTag (16 bytes) + Ciphertext
    Compatible with ai-portal's crypto.ts encrypt().
    """
    key = _get_encryption_key()
    iv = os.urandom(IV_LENGTH)

    aesgcm = AESGCM(key)
    # AESGCM.encrypt returns ciphertext + tag (tag is appended at the end)
    ct_with_tag = aesgcm.encrypt(iv, plaintext.encode('utf-8'), None)

    # Split: ciphertext is everything except the last 16 bytes, tag is last 16
    ciphertext = ct_with_tag[:-AUTH_TAG_LENGTH]
    auth_tag = ct_with_tag[-AUTH_TAG_LENGTH:]

    # Combine as: IV + AuthTag + Ciphertext (matching ai-portal format)
    combined = iv + auth_tag + ciphertext
    return base64.b64encode(combined).decode('ascii')


def decrypt(encrypted_data: str) -> str:
    """Decrypt an AES-256-GCM encrypted string.
    
    Expects base64-encoded: IV (16 bytes) + AuthTag (16 bytes) + Ciphertext
    Compatible with ai-portal's crypto.ts decrypt().
    """
    key = _get_encryption_key()
    combined = base64.b64decode(encrypted_data)

    iv = combined[:IV_LENGTH]
    auth_tag = combined[IV_LENGTH:IV_LENGTH + AUTH_TAG_LENGTH]
    ciphertext = combined[IV_LENGTH + AUTH_TAG_LENGTH:]

    aesgcm = AESGCM(key)
    # AESGCM.decrypt expects ciphertext + tag concatenated
    ct_with_tag = ciphertext + auth_tag
    plaintext = aesgcm.decrypt(iv, ct_with_tag, None)
    return plaintext.decode('utf-8')
