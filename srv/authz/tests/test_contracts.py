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
    claims = AccessTokenClaims(
        iss="busibox-authz",
        sub="11111111-1111-1111-1111-111111111111",
        aud="ingest-api",
        exp=2000000000,
        iat=1999999000,
        jti="jti-1",
        scope="ingest.write",
        roles=[
            RoleClaim(id="r1", name="Editors", permissions=["read", "update", "read", "invalid"]),
        ],
    )
    assert claims.typ == "access"
    assert claims.roles[0].permissions == ["read", "update"]

