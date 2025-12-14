"""
OAuth client secret hashing/verification.

We avoid adding bcrypt/scrypt deps and use PBKDF2-HMAC-SHA256.
Format: pbkdf2_sha256$<iterations>$<salt_b64url>$<hash_b64url>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(val: str) -> bytes:
    padded = val + "=" * (-len(val) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def hash_client_secret(secret: str, *, iterations: int = 200_000, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations, dklen=32)
    return f"pbkdf2_sha256${iterations}${_b64url(salt)}${_b64url(dk)}"


def verify_client_secret(secret: str, encoded: str) -> bool:
    try:
        scheme, iters_s, salt_s, hash_s = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iters_s)
        salt = _b64url_decode(salt_s)
        expected = _b64url_decode(hash_s)
        dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations, dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False

