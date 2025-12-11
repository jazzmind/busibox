#!/usr/bin/env python3
"""
Generate Ed25519 JWK keypair for TOKEN_SERVICE

This is a standalone key generator that uses Python's cryptography library.
Outputs JSON with private and public keys in JWK format.
"""

import json
import sys
import base64
import uuid
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization


def base64url_encode(data: bytes) -> str:
    """Encode bytes as base64url (no padding)"""
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')


def generate_ed25519_jwk():
    """Generate Ed25519 keypair and return as JWK format"""
    
    # Generate Ed25519 private key
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    
    # Get raw key bytes
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    
    # Generate key ID
    kid = str(uuid.uuid4())
    
    # Build JWK objects
    # Ed25519 private key: 32 bytes private + 32 bytes public
    # JWK format uses:
    #   - "d" for private key (first 32 bytes)
    #   - "x" for public key (last 32 bytes or from public_bytes)
    
    private_jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "d": base64url_encode(private_bytes),
        "x": base64url_encode(public_bytes),
        "kid": kid,
        "use": "sig",
        "alg": "EdDSA",
        "agent_id": "token-service"
    }
    
    public_jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": base64url_encode(public_bytes),
        "kid": kid,
        "use": "sig",
        "alg": "EdDSA",
        "agent_id": "token-service"
    }
    
    return {
        "kid": kid,
        "privateKey": json.dumps(private_jwk),
        "publicKey": json.dumps(public_jwk)
    }


if __name__ == "__main__":
    try:
        result = generate_ed25519_jwk()
        print(json.dumps(result))
    except Exception as e:
        print(f"Error generating keys: {e}", file=sys.stderr)
        sys.exit(1)


