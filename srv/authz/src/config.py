import os
from typing import Dict, List, Optional


class Config:
    """
    Config for authz service.

    `srv/authz` is evolving into the Busibox internal Authorization Server:
    - Issues internal access tokens (asymmetric signing + JWKS)
    - Supports OAuth2 token exchange / client credentials flows
    - Stores internal RBAC (users, roles, bindings) in PostgreSQL
    
    Test Mode:
    - When X-Test-Mode: true header is sent, use test database instead
    - Test database is completely isolated from production
    - Enable with AUTHZ_TEST_MODE_ENABLED=true
    """

    def __init__(self):
        self.postgres_host = os.getenv("POSTGRES_HOST", "10.96.200.203")
        self.postgres_port = int(os.getenv("POSTGRES_PORT", "5432"))
        self.postgres_db = os.getenv("POSTGRES_DB", "busibox")
        self.postgres_user = os.getenv("POSTGRES_USER", "busibox_user")
        self.postgres_password = os.getenv("POSTGRES_PASSWORD", "")
        
        # Test mode configuration (isolated test database)
        # Enable test mode header support (X-Test-Mode: true)
        self.test_mode_enabled = os.getenv("AUTHZ_TEST_MODE_ENABLED", "false").lower() == "true"
        self.test_db_name = os.getenv("TEST_DB_NAME", "test_authz")
        self.test_db_user = os.getenv("TEST_DB_USER", "busibox_test_user")
        self.test_db_password = os.getenv("TEST_DB_PASSWORD", "testpassword")

        # Token issuer used by *downstream services* when validating internal access tokens.
        self.issuer = os.getenv("AUTHZ_ISSUER", os.getenv("JWT_ISSUER", "busibox-authz"))

        # Default token TTL for internal access tokens minted by authz (seconds).
        self.access_token_ttl = int(os.getenv("AUTHZ_ACCESS_TOKEN_TTL", os.getenv("AUTHZ_TOKEN_TTL", "900")))
        
        # Session JWT TTL (default 7 days = 604800 seconds)
        self.session_token_ttl = int(os.getenv("AUTHZ_SESSION_TOKEN_TTL", "604800"))

        # JWKS / signing configuration (asymmetric; published via /.well-known/jwks.json)
        self.signing_alg = os.getenv("AUTHZ_SIGNING_ALG", "RS256")
        self.rsa_key_size = int(os.getenv("AUTHZ_RSA_KEY_SIZE", "2048"))

        # Optional: encrypt stored private keys at rest (strongly recommended).
        # If unset, keys are stored unencrypted in PostgreSQL (internal-only deployments only).
        self.key_encryption_passphrase: Optional[str] = os.getenv("AUTHZ_KEY_ENCRYPTION_PASSPHRASE")

        # Optional: bootstrap an OAuth client (e.g. ai-portal) on startup.
        self.bootstrap_client_id: Optional[str] = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID")
        self.bootstrap_client_secret: Optional[str] = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET")
        self.bootstrap_client_allowed_audiences: List[str] = [
            s.strip()
            for s in (os.getenv("AUTHZ_BOOTSTRAP_ALLOWED_AUDIENCES", "ingest-api,search-api,agent-api").split(","))
            if s.strip()
        ]
        self.bootstrap_client_allowed_scopes: List[str] = [
            s.strip()
            for s in (os.getenv("AUTHZ_BOOTSTRAP_ALLOWED_SCOPES", "").split(","))
            if s.strip()
        ]

        # Optional: shared bootstrap admin token for internal management endpoints.
        self.admin_token: Optional[str] = os.getenv("AUTHZ_ADMIN_TOKEN")
        
        # Master key for envelope encryption keystore.
        # This key is used to encrypt/decrypt KEKs (Key Encryption Keys) stored in PostgreSQL.
        # REQUIRED for envelope encryption - must be a high-entropy passphrase.
        # If not set, keystore operations will fail.
        self.master_key: Optional[str] = os.getenv("AUTHZ_MASTER_KEY")

    def to_dict(self) -> Dict:
        return {
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_db": self.postgres_db,
            "postgres_user": self.postgres_user,
            "postgres_password": self.postgres_password,
            "issuer": self.issuer,
            "access_token_ttl": self.access_token_ttl,
            "signing_alg": self.signing_alg,
            "rsa_key_size": self.rsa_key_size,
            "key_encryption_passphrase": self.key_encryption_passphrase,
            "bootstrap_client_id": self.bootstrap_client_id,
            "bootstrap_client_secret": self.bootstrap_client_secret,
            "bootstrap_client_allowed_audiences": self.bootstrap_client_allowed_audiences,
            "bootstrap_client_allowed_scopes": self.bootstrap_client_allowed_scopes,
            "admin_token": self.admin_token,
            "master_key": self.master_key,
            "test_mode_enabled": self.test_mode_enabled,
            "test_db_name": self.test_db_name,
            "test_db_user": self.test_db_user,
            "test_db_password": self.test_db_password,
        }





