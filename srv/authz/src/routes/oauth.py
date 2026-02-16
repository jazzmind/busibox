"""
OAuth2 endpoints for authz.

- GET /.well-known/jwks.json
- POST /oauth/token

Token Exchange Modes:
1. Client Credentials + requested_subject (legacy) - requires client_id/client_secret
2. Subject Token (Zero Trust) - validates JWT signature, no client credentials needed
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional, Tuple

import jwt
import structlog
from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import Config
from oauth.claims import AccessTokenClaims
from oauth.client_auth import verify_client_secret
from oauth.contracts import OAuthTokenRequest, OAuthTokenResponse, TOKEN_EXCHANGE_GRANT
from oauth.keys import generate_rsa_signing_key, load_private_key

logger = structlog.get_logger()
router = APIRouter()

config = Config()

# Subject token types
SUBJECT_TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"

# PostgresService instances - will be set by main.py
# _pg is production, _pg_test is test database (optional)
_pg = None
_pg_test = None

# Header name for test mode
TEST_MODE_HEADER = "X-Test-Mode"


def set_pg_service(pg_service, pg_test_service=None):
    """Set the shared PostgresService instances."""
    global _pg, _pg_test
    _pg = pg_service
    _pg_test = pg_test_service


def _get_pg(request: Request = None):
    """Get the appropriate PostgresService based on request headers.
    
    If X-Test-Mode: true header is present and test mode is enabled,
    returns the test database service. Otherwise returns production.
    
    If request is None, returns production database.
    """
    if request and _pg_test and config.test_mode_enabled:
        test_mode = request.headers.get(TEST_MODE_HEADER, "").lower() == "true"
        if test_mode:
            return _pg_test
    return _pg


async def _ensure_bootstrap_roles() -> None:
    """
    Ensure essential roles exist (Admin, User).
    These roles are created if they don't exist.
    Admin role is also updated if scopes are missing.
    
    Scopes use glob-style wildcards (e.g., "authz.*" matches "authz.users.read").
    See oauth/jwt_auth.py _scope_matches() for wildcard semantics.
    """
    # Define essential roles with their scopes/permissions
    # Admin gets wildcard access to all service namespaces
    admin_scopes = [
        "*",           # Full wildcard - allows any scope
        "authz.*",     # All authz admin operations (users, roles, bindings, etc.)
        "data.*",    # All data operations  
        "search.*",    # All search operations
        "agent.*",     # All agent operations
        "workflow.*",  # All workflow operations
        "web_search.*",# All web search operations
        "apps.*",      # All app management
        "libraries.*", # All library management
        "admin.*",     # Legacy admin scope
    ]
    
    essential_roles = [
        {
            "name": "Admin",
            "description": "Full administrative access",
            "scopes": admin_scopes,
        },
        {
            "name": "User", 
            "description": "Standard user access",
            "scopes": [
                "search.read", 
                "data.read", 
                "data.write",  # Required for document uploads
                "data.delete",  # Required for deleting own documents
                "agent.execute",
                "libraries.read",  # Required for library access
                "libraries.write",  # Required for personal library management
            ],
        },
    ]
    
    for role_def in essential_roles:
        existing = await _pg.get_role_by_name(role_def["name"])
        if not existing:
            created = await _pg.create_role(
                name=role_def["name"],
                description=role_def["description"],
                scopes=role_def["scopes"],
            )
            logger.info("Bootstrapped role", role_name=role_def["name"], role_id=created.get("id"))
        else:
            # Ensure existing role has all required scopes (idempotent update)
            existing_scopes = set(existing.get("scopes") or [])
            required_scopes = set(role_def["scopes"])
            if not required_scopes.issubset(existing_scopes):
                # Update role with missing scopes
                new_scopes = list(existing_scopes | required_scopes)
                await _pg.update_role(
                    role_id=existing["id"],
                    name=None,  # Don't change name
                    description=None,  # Don't change description
                    scopes=new_scopes,
                )
                logger.info("Updated role with required scopes", role_name=role_def["name"], role_id=existing["id"])


async def _ensure_bootstrap_test_user() -> None:
    """
    Ensure test user exists for PVT tests (Zero Trust architecture).
    
    Creates a test user with well-known ID if it doesn't exist.
    This user is used by PVT tests to get user-scoped tokens via token exchange.
    
    Test user is created in BOTH production and test databases.
    """
    import uuid
    from datetime import datetime
    
    # Well-known test user ID (same across all environments)
    TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
    TEST_USER_EMAIL = "test@test.example.com"
    
    # Helper to create test user in a database
    async def create_test_user_in_db(pg_service, db_name: str):
        # Get User role ID
        user_role = await pg_service.get_role_by_name("User")
        if not user_role:
            logger.warning(f"User role not found in {db_name} - cannot assign to test user")
            return
        
        user_role_id = user_role["id"]
        
        # Check if test user exists
        existing_user = await pg_service.get_user(str(TEST_USER_ID))
        if not existing_user:
            # Create test user with specific UUID (direct SQL insert)
            async with pg_service.acquire(None, None) as conn:
                await conn.execute(
                    """
                    INSERT INTO authz_users (user_id, email, status, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $4)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    TEST_USER_ID,
                    TEST_USER_EMAIL.lower(),
                    "ACTIVE",
                    datetime.utcnow(),
                )
                
                # Assign User role
                await conn.execute(
                    """
                    INSERT INTO authz_user_roles (user_id, role_id)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id, role_id) DO NOTHING
                    """,
                    TEST_USER_ID,
                    uuid.UUID(user_role_id),
                )
            
            logger.info(f"Created test user in {db_name}", user_id=str(TEST_USER_ID), email=TEST_USER_EMAIL)
        else:
            # User exists - ensure they have User role
            user_roles = await pg_service.get_user_roles(str(TEST_USER_ID))
            has_user_role = any(r["id"] == user_role_id for r in user_roles)
            if not has_user_role:
                await pg_service.add_user_role(user_id=str(TEST_USER_ID), role_id=user_role_id)
                logger.info(f"Assigned User role to existing test user in {db_name}", user_id=str(TEST_USER_ID))
    
    # Create in production database
    await create_test_user_in_db(_pg, "production")
    
    # Create in test database if test mode is enabled
    if _pg_test:
        await create_test_user_in_db(_pg_test, "test")


async def _ensure_bootstrap_admin_users() -> None:
    """
    Ensure admin users from ADMIN_EMAILS config exist with ACTIVE status and Admin role.
    
    This runs on startup and:
    1. Creates users if they don't exist (ACTIVE status)
    2. Updates existing users to ACTIVE if they're PENDING
    3. Assigns Admin role if not already assigned
    
    Admin users are created in production database only.
    """
    from datetime import datetime
    
    if not config.admin_emails:
        logger.debug("No ADMIN_EMAILS configured, skipping admin user bootstrap")
        return
    
    # Get Admin role ID
    admin_role = await _pg.get_role_by_name("Admin")
    if not admin_role:
        logger.error("Admin role not found - cannot assign to admin users")
        return
    
    admin_role_id = admin_role["id"]
    
    invalid_admin_email_values = {"null", "none", "undefined", "change_me_admin_emails"}

    for email in config.admin_emails:
        email_lower = email.lower().strip()
        if not email_lower or email_lower in invalid_admin_email_values or email_lower.startswith("change_me"):
            continue
            
        # Check if user exists by email
        existing_user = await _pg.get_user_by_email(email_lower)
        
        if existing_user:
            user_id = existing_user["user_id"]
            
            # Update status to ACTIVE if PENDING
            if existing_user.get("status") != "ACTIVE":
                async with _pg.acquire(None, None) as conn:
                    await conn.execute(
                        """
                        UPDATE authz_users 
                        SET status = 'ACTIVE', updated_at = $2
                        WHERE user_id = $1
                        """,
                        uuid.UUID(user_id),
                        datetime.utcnow(),
                    )
                logger.info("Activated admin user", email=email_lower, user_id=user_id)
        else:
            # Create new user with ACTIVE status
            async with _pg.acquire(None, None) as conn:
                new_user_id = uuid.uuid4()
                await conn.execute(
                    """
                    INSERT INTO authz_users (user_id, email, status, created_at, updated_at)
                    VALUES ($1, $2, 'ACTIVE', $3, $3)
                    """,
                    new_user_id,
                    email_lower,
                    datetime.utcnow(),
                )
                user_id = str(new_user_id)
            logger.info("Created admin user", email=email_lower, user_id=user_id)
        
        # Ensure Admin role is assigned
        user_roles = await _pg.get_user_roles(user_id)
        has_admin_role = any(r["id"] == admin_role_id for r in user_roles)
        
        if not has_admin_role:
            async with _pg.acquire(None, None) as conn:
                await conn.execute(
                    """
                    INSERT INTO authz_user_roles (user_id, role_id)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id, role_id) DO NOTHING
                    """,
                    uuid.UUID(user_id),
                    uuid.UUID(admin_role_id),
                )
            logger.info("Assigned Admin role to user", email=email_lower, user_id=user_id)


async def _ensure_bootstrap() -> None:
    """
    Ensure authz has at least one active signing key and (optionally) a bootstrap OAuth client.
    
    If an existing key cannot be decrypted (e.g., passphrase changed), it will be
    deleted and a new key generated.
    """
    await _pg.connect()

    # 1) signing key - check if we have one and can decrypt it
    active_key = await _pg.get_active_signing_key()
    need_new_key = False
    
    if active_key:
        # Verify we can actually decrypt the key with current passphrase
        try:
            load_private_key(active_key["private_key_pem"], config.key_encryption_passphrase)
            logger.info("Verified existing signing key can be decrypted", kid=active_key["kid"])
        except Exception as e:
            logger.warning(
                "Existing signing key cannot be decrypted - will regenerate",
                kid=active_key["kid"],
                error=str(e),
            )
            # Delete the unusable key
            async with _pg.acquire(None, None) as conn:
                await conn.execute(
                    "DELETE FROM authz_signing_keys WHERE kid = $1",
                    active_key["kid"],
                )
            logger.info("Deleted unusable signing key", kid=active_key["kid"])
            need_new_key = True
    else:
        need_new_key = True
    
    if need_new_key:
        if config.signing_alg != "RS256":
            raise RuntimeError(f"Unsupported signing alg for bootstrap: {config.signing_alg}")
        sk = generate_rsa_signing_key(
            key_size=config.rsa_key_size,
            alg=config.signing_alg,
            passphrase=config.key_encryption_passphrase,
        )
        await _pg.insert_signing_key(
            kid=sk.kid,
            alg=sk.alg,
            private_key_pem=sk.private_key_pem,
            public_jwk=sk.public_jwk,
            is_active=True,
        )
        logger.info("Generated new authz signing key", kid=sk.kid, alg=sk.alg)


    # 4) bootstrap essential roles
    await _ensure_bootstrap_roles()
    
    # 5) bootstrap test user (for PVT tests in Zero Trust architecture)
    await _ensure_bootstrap_test_user()
    
    # 6) bootstrap admin users from ADMIN_EMAILS config
    await _ensure_bootstrap_admin_users()


async def _require_client(client_id: str, client_secret: str) -> dict:
    await _pg.connect()
    client = await _pg.get_oauth_client(client_id)
    if not client or not client.get("is_active"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_client")
    if not verify_client_secret(client_secret, client["client_secret_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_client")
    return client


async def _verify_subject_token(subject_token: str, request: Request = None) -> Tuple[str, str, str]:
    """
    Verify a subject_token JWT signed by authz.
    
    Returns (user_id, email, jti) if valid.
    Raises HTTPException if invalid.
    
    Args:
        subject_token: The JWT to verify
        request: Optional request object for test-mode DB routing
    """
    await _pg.connect()
    
    # Get the active signing key's public key for verification
    row = await _pg.get_active_signing_key()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="no_signing_key_configured"
        )
    
    # Get public key from the stored JWK
    public_jwk = row.get("public_jwk", {})
    kid = row["kid"]
    alg = row["alg"]
    
    # Also load private key to extract public key (more reliable)
    private_pem = row["private_key_pem"]
    private_key = load_private_key(private_pem, config.key_encryption_passphrase)
    public_key = private_key.public_key()
    
    try:
        # Decode and verify the JWT
        # First decode without verification to get the header and claims
        unverified = jwt.decode(subject_token, options={"verify_signature": False})
        token_kid = jwt.get_unverified_header(subject_token).get("kid")
        
        # Verify the token was signed by our key
        if token_kid != kid:
            logger.warning("Subject token signed by unknown key", token_kid=token_kid, expected_kid=kid)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_subject_token_key"
            )
        
        # Zero Trust: Accept ANY valid token signed by us for token exchange
        # The security comes from:
        # 1. Token signature verification (proves authz issued it)
        # 2. Token expiration check (not expired)
        # 3. User's scopes come from RBAC, not the incoming token
        # 4. Session/delegation revocation checks (for those types)
        #
        # We don't restrict based on audience because:
        # - The token proves who the user is (via 'sub' claim)
        # - The user's permissions come from their roles in authz DB
        # - Any service holding a valid user token should be able to exchange
        #   it for another service token (if user has appropriate roles)
        
        token_type = unverified.get("typ")
        token_audience = unverified.get("aud")
        app_id = unverified.get("app_id")
        
        logger.info(
            "Verifying subject token for exchange",
            token_type=token_type,
            audience=token_audience,
            app_id=app_id,
        )
        
        # Verify signature, issuer, and expiration - but accept ANY audience
        # We use the token's own audience for validation (just to confirm it's valid)
        claims = jwt.decode(
            subject_token,
            public_key,
            algorithms=[alg],
            issuer=config.issuer,
            audience=token_audience,  # Accept whatever audience the token has
            options={"require": ["exp", "iat", "sub"]}  # jti and typ optional for access tokens
        )
        
        # Get token type - default to "access" for service tokens without typ
        token_type = claims.get("typ", "access")
        
        user_id = claims["sub"]
        email = claims.get("email", "")
        jti = claims.get("jti", "")  # May not be present in all access tokens
        
        # Check if token has been revoked (only for session/delegation tokens)
        # Access tokens are short-lived and don't need revocation tracking
        # Use request-aware DB routing so test-mode sessions are found in the test DB
        revocation_db = _get_pg(request)
        await revocation_db.connect()
        if token_type == "session" and jti:
            session = await revocation_db.get_session_by_id(jti)
            if not session:
                logger.warning("Session revoked or not found", jti=jti)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="session_revoked"
                )
        elif token_type == "delegation" and jti:
            delegation = await revocation_db.get_delegation_token(jti)
            if not delegation:
                logger.warning("Delegation token revoked or not found", jti=jti)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="delegation_revoked"
                )
        # access tokens are short-lived and don't need revocation check
        
        logger.info(
            "Subject token verified for exchange",
            user_id=user_id,
            token_type=token_type,
            original_audience=token_audience,
            jti=jti or "(none)",
            app_id=claims.get("app_id"),
        )
        
        return user_id, email, jti
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="subject_token_expired"
        )
    except jwt.InvalidIssuerError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_subject_token_issuer"
        )
    except jwt.PyJWTError as e:
        logger.warning("Subject token verification failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_subject_token"
        )


def _enforce_audience(client: dict, audience: str) -> None:
    allowed = client.get("allowed_audiences") or []
    if audience not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unauthorized_client_audience")


def _enforce_scopes(client: dict, scope_str: str) -> str:
    requested = [s for s in scope_str.split(" ") if s]
    allowed = client.get("allowed_scopes") or []
    if not requested:
        return ""
    if not allowed:
        # no scopes allowed
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unauthorized_client_scope")
    out: List[str] = [s for s in requested if s in allowed]
    if len(out) != len(requested):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unauthorized_client_scope")
    return " ".join(out)


async def _sign_access_token(claims: dict) -> str:
    await _pg.connect()
    row = await _pg.get_active_signing_key()
    if not row:
        raise RuntimeError("no active signing key configured")
    kid = row["kid"]
    alg = row["alg"]
    private_pem = row["private_key_pem"]
    key_obj = load_private_key(private_pem, config.key_encryption_passphrase)
    # PyJWT supports cryptography key objects directly.
    token = jwt.encode(claims, key_obj, algorithm=alg, headers={"kid": kid, "typ": "JWT"})
    return token


async def _sign_delegation_token(user_id: str, email: str, jti: str, scopes: List[str], expires_at: int) -> str:
    """
    Sign a delegation token JWT for background tasks.
    """
    await _pg.connect()
    row = await _pg.get_active_signing_key()
    if not row:
        raise RuntimeError("no active signing key configured")
    
    kid = row["kid"]
    alg = row["alg"]
    private_pem = row["private_key_pem"]
    key_obj = load_private_key(private_pem, config.key_encryption_passphrase)
    
    now = int(time.time())
    
    claims = {
        "iss": config.issuer,
        "sub": user_id,
        "aud": "busibox-portal",  # Delegation tokens are for busibox-portal to present
        "exp": expires_at,
        "iat": now,
        "nbf": now,
        "jti": jti,
        "typ": "delegation",
        "email": email,
        "scope": " ".join(scopes),
    }
    
    token = jwt.encode(claims, key_obj, algorithm=alg, headers={"kid": kid, "typ": "JWT"})
    return token


@router.get("/.well-known/jwks.json")
async def jwks(request: Request):
    await _ensure_bootstrap()
    db = _get_pg(request)
    await db.connect()
    keys = await db.list_public_jwks()
    return {"keys": keys}


@router.post("/oauth/token")
async def token(request: Request):
    """
    OAuth2 token endpoint.

    Supports:
    - grant_type=client_credentials (requires client_id/client_secret)
    - grant_type=urn:ietf:params:oauth:grant-type:token-exchange
      - With subject_token: Zero Trust mode - no client credentials needed
      - With client credentials + requested_subject: Legacy mode

    Accepts both application/x-www-form-urlencoded and JSON bodies.
    """
    await _ensure_bootstrap()

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        body = dict(form)
    else:
        body = await request.json()

    try:
        token_req = OAuthTokenRequest.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request") from e

    # Client credentials grant always requires client authentication
    if token_req.grant_type == "client_credentials":
        if not token_req.client_id or not token_req.client_secret:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="client_credentials_required")
        
        client = await _require_client(token_req.client_id, token_req.client_secret)
        
        if not token_req.audience:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audience_required")
        _enforce_audience(client, token_req.audience)
        scope = _enforce_scopes(client, token_req.scope)

        now = int(time.time())
        exp = now + config.access_token_ttl
        claims = AccessTokenClaims(
            iss=config.issuer,
            sub=token_req.client_id,
            aud=token_req.audience,
            iat=now,
            nbf=now,
            exp=exp,
            jti=str(uuid.uuid4()),
            scope=scope,
            roles=[],
        ).model_dump()
        access_token = await _sign_access_token(claims)
        return OAuthTokenResponse(
            access_token=access_token,
            expires_in=config.access_token_ttl,
            scope=scope,
            issued_token_type="urn:ietf:params:oauth:token-type:access_token",
        ).model_dump()

    if token_req.grant_type == TOKEN_EXCHANGE_GRANT:
        if not token_req.audience:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audience_required")
        
        # Determine authentication mode: subject_token (Zero Trust) or client credentials (legacy)
        user_id: str
        email: str = ""
        purpose: str = token_req.requested_purpose or "token-exchange"
        
        if token_req.subject_token:
            # Zero Trust mode: Verify the subject_token JWT
            # No client credentials required - the JWT signature proves identity
            logger.info("Token exchange with subject_token (Zero Trust mode)")
            
            user_id, email, jti = await _verify_subject_token(token_req.subject_token, request)
            purpose = f"subject_token:{jti[:8]}"
            
        elif token_req.client_id and token_req.client_secret and token_req.requested_subject:
            # Legacy mode: Client credentials + requested_subject
            logger.info("Token exchange with client credentials (legacy mode)")
            
            client = await _require_client(token_req.client_id, token_req.client_secret)
            _enforce_audience(client, token_req.audience)
            
            # Validate requested_subject is a valid UUID format
            try:
                uuid.UUID(token_req.requested_subject)
            except (ValueError, AttributeError, TypeError):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, 
                    detail="invalid_subject_format"
                )
            
            user_id = token_req.requested_subject
            # Email will be fetched from DB below
            
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subject_token_or_client_credentials_required"
            )

        # Pull RBAC from authz DB
        db = _get_pg(request)
        await db.connect()
        if not await db.user_exists(user_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_subject")
        
        # Get user info including email (for legacy mode or if email not in token)
        user_info = await db.get_user(user_id)
        if user_info and not email:
            email = user_info.get("email", "")
        display_name = user_info.get("display_name") if user_info else None
        first_name = user_info.get("first_name") if user_info else None
        last_name = user_info.get("last_name") if user_info else None
        avatar_url = user_info.get("avatar_url") if user_info else None
        favorite_color = user_info.get("favorite_color") if user_info else None
        resolved_name = display_name or " ".join(part for part in [first_name, last_name] if part) or None
        
        roles = await db.get_user_roles(user_id)
        
        # App-scoped token exchange: verify user has access to the app via bindings
        # resource_id is the app UUID from busibox-portal's App table
        app_roles = None
        if token_req.resource_id:
            logger.info(
                "App-scoped token exchange",
                user_id=user_id,
                resource_id=token_req.resource_id,
                audience=token_req.audience,
            )
            
            # Check if user has access to this app via RBAC bindings
            has_access = await db.user_can_access_resource(user_id, "app", token_req.resource_id)
            if not has_access:
                logger.warning(
                    "User does not have access to app",
                    user_id=user_id,
                    resource_id=token_req.resource_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="user_does_not_have_app_access"
                )
            
            # Get roles that grant access to this specific app
            # This is the intersection of user's roles and roles bound to the app
            app_role_bindings = await db.get_roles_for_resource("app", token_req.resource_id)
            app_role_ids = {b["id"] for b in app_role_bindings}  # id is the role_id
            app_roles = [r for r in roles if r["id"] in app_role_ids]
            
            logger.info(
                "App access verified",
                user_id=user_id,
                resource_id=token_req.resource_id,
                app_roles=[r["name"] for r in app_roles],
            )

        # Build role claims (id + name only, for data access filtering)
        # Use app_roles if this is an app-scoped exchange, otherwise all user roles
        effective_roles = app_roles if app_roles is not None else roles
        role_claims = [
            {"id": r["id"], "name": r["name"]}
            for r in effective_roles
        ]

        # Aggregate scopes from effective roles (union of role scopes)
        all_scopes: set[str] = set()
        for r in effective_roles:
            role_scopes = r.get("scopes") or []
            all_scopes.update(role_scopes)
        aggregated_scope = " ".join(sorted(all_scopes))

        now = int(time.time())
        exp = now + config.access_token_ttl
        
        # For app-scoped tokens, include resource_id in claims for the app to verify
        extra_claims = {}
        if token_req.resource_id:
            extra_claims["app_id"] = token_req.resource_id
        
        claims = AccessTokenClaims(
            iss=config.issuer,
            sub=user_id,
            aud=token_req.audience,
            iat=now,
            nbf=now,
            exp=exp,
            jti=str(uuid.uuid4()),
            scope=aggregated_scope,
            roles=role_claims,
            email=email or None,  # Include email for downstream apps to display
            name=resolved_name,
            given_name=first_name,
            family_name=last_name,
            picture=avatar_url,
            favorite_color=favorite_color,
        ).model_dump()
        
        # Add extra claims
        claims.update(extra_claims)

        access_token = await _sign_access_token(claims)

        # Audit (best-effort)
        await db.insert_audit(
            actor_id=user_id,
            action="oauth.token.issued",
            resource_type="oauth_token",
            resource_id=token_req.resource_id,
            details={
                "grant_type": TOKEN_EXCHANGE_GRANT,
                "audience": token_req.audience,
                "resource_id": token_req.resource_id,
                "purpose": purpose,
                "mode": "subject_token" if token_req.subject_token else "client_credentials",
                "app_scoped": token_req.resource_id is not None,
            },
            user_id=user_id,
            role_ids=[r["id"] for r in effective_roles],
        )

        return OAuthTokenResponse(
            access_token=access_token,
            expires_in=config.access_token_ttl,
            scope=aggregated_scope,
            issued_token_type="urn:ietf:params:oauth:token-type:access_token",
        ).model_dump()

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported_grant_type")


# ============================================================================
# Delegation Token Endpoints
# ============================================================================


class DelegationTokenRequest(BaseModel):
    """Request to create a delegation token for background tasks."""
    subject_token: str = Field(..., description="Session JWT to authorize delegation")
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable name for the delegation")
    scopes: List[str] = Field(default_factory=list, description="Scopes to delegate (subset of user's scopes)")
    expires_in_seconds: int = Field(default=94608000, ge=3600, le=94608000, description="TTL in seconds (1 hour to 3 years, default 3 years)")


class DelegationTokenResponse(BaseModel):
    """Response containing a delegation token."""
    delegation_token: str
    token_type: str = "Bearer"
    expires_in: int
    expires_at: str
    jti: str
    name: str
    scopes: List[str]


@router.post("/oauth/delegation")
async def create_delegation_token(request: Request):
    """
    Create a delegation token for background tasks.
    
    The user must authenticate with their session JWT (subject_token).
    The delegation token has a longer TTL but can be revoked.
    
    Use case: User creates a recurring task that needs to run when they're offline.
    """
    await _ensure_bootstrap()
    
    body = await request.json()
    try:
        req = DelegationTokenRequest.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    
    # Verify the session JWT
    user_id, email, session_jti = await _verify_subject_token(req.subject_token, request)
    
    # Get user's roles to validate requested scopes
    db = _get_pg(request)
    await db.connect()
    user = await db.get_user_with_roles(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")
    
    # Aggregate all scopes the user has
    all_user_scopes: set[str] = set()
    for role in user.get("roles", []):
        role_scopes = role.get("scopes") or []
        all_user_scopes.update(role_scopes)
    
    def _scope_matches(requested: str, user_scopes: set[str]) -> bool:
        """Check if a requested scope is covered by user's scopes (with wildcard support)."""
        # Direct match
        if requested in user_scopes:
            return True
        # Check for wildcards - e.g., "search.*" covers "search.read"
        parts = requested.split(".")
        for i in range(len(parts)):
            prefix = ".".join(parts[:i + 1])
            if f"{prefix}.*" in user_scopes:
                return True
        # Check for full wildcard
        if "*" in user_scopes:
            return True
        return False
    
    # If no scopes requested, use all user's scopes
    if not req.scopes:
        requested_scopes = list(all_user_scopes)
    else:
        # Validate requested scopes are covered by user's scopes (with wildcard support)
        invalid_scopes = [s for s in req.scopes if not _scope_matches(s, all_user_scopes)]
        if invalid_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requested_scopes_not_allowed: {', '.join(invalid_scopes)}"
            )
        requested_scopes = req.scopes
    
    # Calculate expiration
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=req.expires_in_seconds)
    expires_at_ts = int(expires_at.timestamp())
    
    # Create delegation token record in DB
    delegation = await db.create_delegation_token(
        user_id=user_id,
        scopes=requested_scopes,
        name=req.name,
        expires_at=expires_at.isoformat(),
    )
    
    jti = delegation["jti"]
    
    # Sign the delegation JWT
    delegation_jwt = await _sign_delegation_token(
        user_id=user_id,
        email=email,
        jti=jti,
        scopes=requested_scopes,
        expires_at=expires_at_ts,
    )
    
    logger.info(
        "Delegation token created",
        user_id=user_id,
        jti=jti,
        name=req.name,
        scopes=requested_scopes,
        expires_in=req.expires_in_seconds,
    )
    
    return DelegationTokenResponse(
        delegation_token=delegation_jwt,
        expires_in=req.expires_in_seconds,
        expires_at=expires_at.isoformat(),
        jti=jti,
        name=req.name,
        scopes=requested_scopes,
    ).model_dump()


@router.get("/oauth/delegations")
async def list_delegation_tokens(request: Request):
    """
    List all active delegation tokens for the authenticated user.
    
    Requires session JWT in Authorization header.
    """
    await _ensure_bootstrap()
    
    # Get session JWT from Authorization header
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer_token_required")
    
    subject_token = auth_header[7:]
    user_id, email, session_jti = await _verify_subject_token(subject_token, request)
    
    db = _get_pg(request)
    await db.connect()
    delegations = await db.list_user_delegation_tokens(user_id)
    
    return {
        "delegations": [
            {
                "jti": d["jti"],
                "name": d["name"],
                "scopes": d["scopes"],
                "expires_at": d["expires_at"].isoformat() if hasattr(d["expires_at"], "isoformat") else str(d["expires_at"]),
                "created_at": d["created_at"].isoformat() if hasattr(d["created_at"], "isoformat") else str(d["created_at"]),
                "revoked": d.get("revoked_at") is not None,
            }
            for d in delegations
        ],
    }


@router.delete("/oauth/delegations/{jti}")
async def revoke_delegation_token(request: Request, jti: str):
    """
    Revoke a delegation token.
    
    Requires session JWT in Authorization header.
    User can only revoke their own delegation tokens.
    """
    await _ensure_bootstrap()
    
    # Get session JWT from Authorization header
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer_token_required")
    
    subject_token = auth_header[7:]
    user_id, email, session_jti = await _verify_subject_token(subject_token, request)
    
    db = _get_pg(request)
    await db.connect()
    
    # Verify the delegation token belongs to this user
    delegation = await db.get_delegation_token(jti)
    if not delegation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="delegation_not_found")
    
    if delegation["user_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not_owner")
    
    revoked = await db.revoke_delegation_token(jti)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="delegation_not_found_or_already_revoked")
    
    logger.info("Delegation token revoked", user_id=user_id, jti=jti)
    
    return {"status": "ok", "revoked": True, "jti": jti}
