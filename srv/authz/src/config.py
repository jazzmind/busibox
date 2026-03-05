import os
from typing import Dict, List, Optional


class Config:
    """
    Config for authz service.

    `srv/authz` is the Busibox internal Authorization Server:
    - Issues internal access tokens (asymmetric signing + JWKS)
    - Supports Zero Trust token exchange (no client authentication)
    - Stores internal RBAC (users, roles, bindings) in PostgreSQL
    
    Authentication Model (Zero Trust):
    - No client IDs or client secrets - services don't authenticate to authz
    - Users authenticate via magic link, passkey, or TOTP
    - Tokens are exchanged based on user identity, not client credentials
    - All service-to-service communication validated via JWKS
    
    Test Mode:
    - When X-Test-Mode: true header is sent, use test database instead
    - Test database is completely isolated from production
    - Enable with AUTHZ_TEST_MODE_ENABLED=true
    """

    def __init__(self):
        self.postgres_host = os.getenv("POSTGRES_HOST", "postgres")
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
        # Default to 3 years (94608000 seconds) to support long-lived delegation tokens for tasks.
        # For short-lived tokens, set AUTHZ_ACCESS_TOKEN_TTL explicitly.
        self.access_token_ttl = int(os.getenv("AUTHZ_ACCESS_TOKEN_TTL", os.getenv("AUTHZ_TOKEN_TTL", "94608000")))
        
        # Session JWT TTL (default 7 days = 604800 seconds)
        self.session_token_ttl = int(os.getenv("AUTHZ_SESSION_TOKEN_TTL", "604800"))

        # JWKS / signing configuration (asymmetric; published via /.well-known/jwks.json)
        self.signing_alg = os.getenv("AUTHZ_SIGNING_ALG", "RS256")
        self.rsa_key_size = int(os.getenv("AUTHZ_RSA_KEY_SIZE", "2048"))

        # Optional: encrypt stored private keys at rest (strongly recommended).
        # If unset, keys are stored unencrypted in PostgreSQL (internal-only deployments only).
        self.key_encryption_passphrase: Optional[str] = os.getenv("AUTHZ_KEY_ENCRYPTION_PASSPHRASE")

        # Allowed audiences for token exchange (Zero Trust).
        # These are the services that can receive exchanged tokens.
        self.allowed_audiences: List[str] = [
            s.strip()
            for s in (os.getenv("AUTHZ_ALLOWED_AUDIENCES", "data-api,search-api,agent-api,authz-api").split(","))
            if s.strip()
        ]

        # Master key for envelope encryption keystore.
        # This key is used to encrypt/decrypt KEKs (Key Encryption Keys) stored in PostgreSQL.
        # REQUIRED for envelope encryption - must be a high-entropy passphrase.
        # If not set, keystore operations will fail.
        self.master_key: Optional[str] = os.getenv("AUTHZ_MASTER_KEY")
        
        # Email domain allowlist (comma-separated).
        # If set, only emails from these domains can register/login.
        # Example: "company.com,subsidiary.com"
        self.allowed_email_domains: List[str] = [
            s.strip().lower()
            for s in (os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(","))
            if s.strip()
        ]
        
        # Admin emails (comma-separated).
        # Users with these emails are automatically created with ACTIVE status
        # and assigned the Admin role on startup.
        # Example: "admin@company.com,cto@company.com"
        self.admin_emails: List[str] = [
            s.strip().lower()
            for s in (os.getenv("ADMIN_EMAILS", "").split(","))
            if s.strip()
        ]

        # ---- Email / Bridge integration ----
        # Portal base URL used to construct magic link URLs.
        # Example: "https://portal.example.com/portal"
        self.app_url: str = os.getenv("APP_URL", "http://localhost:3000/portal")

        # Internal Bridge API URL for sending emails.
        # Authz calls bridge-api directly so that magic link tokens and TOTP
        # codes never leave the backend.
        self.bridge_api_url: Optional[str] = os.getenv("BRIDGE_API_URL")

        # Dev mode — when true, log magic link URL and TOTP code to console
        # so developers can authenticate without a working email setup.
        self.dev_mode: bool = os.getenv("DEV_MODE", "false").lower() == "true"

        # ---- Microsoft Entra ID / Azure AD integration ----
        self.microsoft_client_id: Optional[str] = os.getenv("MICROSOFT_CLIENT_ID")
        self.microsoft_client_secret: Optional[str] = os.getenv("MICROSOFT_CLIENT_SECRET")
        self.microsoft_tenant_id: Optional[str] = os.getenv("MICROSOFT_TENANT_ID")
        self.microsoft_enabled: bool = bool(self.microsoft_client_id and self.microsoft_tenant_id)

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
            "allowed_audiences": self.allowed_audiences,
            "master_key": self.master_key,
            "test_mode_enabled": self.test_mode_enabled,
            "test_db_name": self.test_db_name,
            "test_db_user": self.test_db_user,
            "test_db_password": self.test_db_password,
            "allowed_email_domains": self.allowed_email_domains,
            "admin_emails": self.admin_emails,
            "app_url": self.app_url,
            "bridge_api_url": self.bridge_api_url,
            "dev_mode": self.dev_mode,
        }





