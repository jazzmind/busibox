"""
Key management utilities for authz (asymmetric signing + JWKS).
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64url_uint(val: int) -> str:
    # RFC7517: base64url encoding of the unsigned big-endian representation
    if val == 0:
        raw = b"\x00"
    else:
        raw = val.to_bytes((val.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_bytes(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class SigningKey:
    kid: str
    alg: str
    private_key_pem: bytes  # may be encrypted (PKCS8)
    public_jwk: Dict[str, Any]


def generate_rsa_signing_key(*, key_size: int, alg: str, passphrase: Optional[str]) -> SigningKey:
    """
    Generate a new RSA signing key and corresponding public JWK.

    `passphrase` encrypts the private key PEM if provided.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    public_key = private_key.public_key()

    pub_numbers = public_key.public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": alg,
        "n": _b64url_uint(pub_numbers.n),
        "e": _b64url_uint(pub_numbers.e),
    }

    # Deterministic-ish kid based on JWK thumbprint inputs.
    # This is not a strict RFC7638 implementation, but stable for our generated keys.
    kid_source = f"{jwk['kty']}|{jwk['alg']}|{jwk['n']}|{jwk['e']}".encode("utf-8")
    kid = _b64url_bytes(hashlib.sha256(kid_source).digest()[:18])
    jwk["kid"] = kid

    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode("utf-8"))
        if passphrase
        else serialization.NoEncryption()
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )

    return SigningKey(kid=kid, alg=alg, private_key_pem=private_pem, public_jwk=jwk)


def load_private_key(private_key_pem: bytes, passphrase: Optional[str]):
    password = passphrase.encode("utf-8") if passphrase else None
    return serialization.load_pem_private_key(private_key_pem, password=password)

