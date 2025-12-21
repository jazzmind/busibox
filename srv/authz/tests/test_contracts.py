from oauth.contracts import OAuthTokenRequest, TOKEN_EXCHANGE_GRANT
from oauth.claims import AccessTokenClaims, RoleClaim


def test_oauth_token_request_normalizes_scope():
    req = OAuthTokenRequest(
        grant_type=TOKEN_EXCHANGE_GRANT,
        client_id="c1",
        client_secret="s1",
        scope="ingest.write  search.read ingest.write",
        audience="ingest-api",
        requested_subject="11111111-1111-1111-1111-111111111111",
    )
    assert req.scope == "ingest.write search.read"


def test_access_token_claims_contract_parses():
    """Test that access token claims parse correctly with roles (no permissions)."""
    claims = AccessTokenClaims(
        iss="busibox-authz",
        sub="11111111-1111-1111-1111-111111111111",
        aud="ingest-api",
        exp=2000000000,
        iat=1999999000,
        jti="jti-1",
        scope="ingest.write ingest.read search.read",
        roles=[
            RoleClaim(id="r1", name="Editors"),
        ],
    )
    assert claims.typ == "access"
    # Scopes are at the token level, not embedded in roles
    assert "ingest.write" in claims.scope
    assert "ingest.read" in claims.scope
    # Roles contain only id and name for data access filtering
    assert claims.roles[0].id == "r1"
    assert claims.roles[0].name == "Editors"


def test_role_claim_minimal():
    """Role claims should only have id and name."""
    role = RoleClaim(id="role-123", name="Finance Admin")
    assert role.id == "role-123"
    assert role.name == "Finance Admin"
