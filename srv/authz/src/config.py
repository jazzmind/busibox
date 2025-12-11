import os
from typing import Dict


class Config:
    """Minimal config for authz service."""

    def __init__(self):
        self.postgres_host = os.getenv("POSTGRES_HOST", "10.96.200.203")
        self.postgres_port = int(os.getenv("POSTGRES_PORT", "5432"))
        self.postgres_db = os.getenv("POSTGRES_DB", "busibox")
        self.postgres_user = os.getenv("POSTGRES_USER", "busibox_user")
        self.postgres_password = os.getenv("POSTGRES_PASSWORD", "")

        self.jwt_secret = (
            os.getenv("JWT_SECRET")
            or os.getenv("SERVICE_JWT_SECRET")
            or os.getenv("SSO_JWT_SECRET")
            or "default-service-secret-change-in-production"
        )
        self.jwt_issuer = os.getenv("JWT_ISSUER", "authz-service")
        self.jwt_audience = os.getenv("JWT_AUDIENCE", "busibox-services")
        self.authz_token_ttl = int(os.getenv("AUTHZ_TOKEN_TTL", "900"))

    def to_dict(self) -> Dict:
        return {
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_db": self.postgres_db,
            "postgres_user": self.postgres_user,
            "postgres_password": self.postgres_password,
            "jwt_secret": self.jwt_secret,
            "jwt_issuer": self.jwt_issuer,
            "jwt_audience": self.jwt_audience,
            "authz_token_ttl": self.authz_token_ttl,
        }


