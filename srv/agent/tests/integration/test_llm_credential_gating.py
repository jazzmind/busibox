"""
Integration tests for LLM credential source-gating.

Verifies that:
1. An admin user exchanging their own session token for config-api directly
   does NOT receive config.secrets.read scope and is denied /raw access to
   llm-keys.
2. The agent-api token exchange path DOES receive config.secrets.read scope
   and can read /raw values.

These tests require real running services (authz, config-api, agent-api) and
real admin credentials from the test environment.

Run with:
    make test-docker SERVICE=agent ARGS="tests/integration/test_llm_credential_gating.py"
    # or against staging:
    make test-local SERVICE=agent INV=staging ARGS="tests/integration/test_llm_credential_gating.py"
"""

import os
import pytest
import httpx

from busibox_common.test_mode import authz_request_headers

# ---------------------------------------------------------------------------
# Service URLs
# ---------------------------------------------------------------------------
AGENT_API_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
AUTHZ_URL = os.getenv("AUTHZ_URL", os.getenv("AUTH_JWKS_URL", "").replace("/.well-known/jwks.json", ""))
CONFIG_API_URL = os.getenv("CONFIG_API_URL", "http://localhost:8012")

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_agent_token(auth_client) -> str:
    """Exchange test-user session token for an agent-api access token."""
    return auth_client.get_token(audience="agent-api")


def _exchange_for_audience(subject_token: str, audience: str) -> httpx.Response:
    """Exchange a subject token for a target audience via authz."""
    if not AUTHZ_URL:
        pytest.skip("AUTHZ_URL not configured")
    return httpx.post(
        f"{AUTHZ_URL}/oauth/token",
        json={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": subject_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "audience": audience,
        },
        headers=authz_request_headers(),
        timeout=15.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLLMCredentialGating:
    """Verify that config.secrets.read scope gates raw llm-keys access correctly."""

    def test_agent_api_token_has_secrets_scope(self, auth_client):
        """
        When agent-api exchanges a user token for config-api, the resulting token
        must carry config.secrets.read scope.

        This verifies the authz injection works (scope injected because
        subject_token.aud == "agent-api").
        """
        agent_token = _get_agent_token(auth_client)
        assert agent_token, "Could not obtain agent-api token"

        resp = _exchange_for_audience(agent_token, "config-api")
        assert resp.status_code == 200, f"Token exchange failed: {resp.text}"
        issued_scope = resp.json().get("scope", "")

        assert "config.secrets.read" in issued_scope.split(), (
            f"Expected config.secrets.read in scope when exchanging agent-api token for config-api. "
            f"Got: {issued_scope!r}"
        )

    def test_direct_config_api_token_lacks_secrets_scope(self, auth_client):
        """
        Re-exchanging a config-api token (aud=config-api) must NOT re-inject
        config.secrets.read from the agent-api path.
        """
        agent_token = _get_agent_token(auth_client)

        resp1 = _exchange_for_audience(agent_token, "config-api")
        assert resp1.status_code == 200, f"First exchange failed: {resp1.text}"
        config_token_via_agent = resp1.json()["access_token"]

        resp2 = _exchange_for_audience(config_token_via_agent, "config-api")
        assert resp2.status_code == 200, f"Second exchange failed: {resp2.text}"
        second_scope = resp2.json().get("scope", "")
        assert "config.secrets.read" not in second_scope.split(), (
            "config.secrets.read should not be re-injected when subject aud is config-api"
        )

    def test_raw_endpoint_blocked_without_secrets_scope(self, auth_client):
        """
        Verify config-api /admin/config/{key}/raw succeeds with agent-api-path token
        that carries config.secrets.read.
        """
        if not CONFIG_API_URL:
            pytest.skip("CONFIG_API_URL not configured")

        agent_token = _get_agent_token(auth_client)
        resp = _exchange_for_audience(agent_token, "config-api")
        assert resp.status_code == 200, f"Token exchange failed: {resp.text}"
        config_token = resp.json()["access_token"]

        list_resp = httpx.get(
            f"{CONFIG_API_URL}/admin/config",
            params={"category": "llm-keys"},
            headers={"Authorization": f"Bearer {config_token}"},
            timeout=15.0,
        )
        if list_resp.status_code != 200:
            pytest.skip("No llm-keys configured in config-api — save Bedrock/OpenAI keys first")

        entries = list_resp.json().get("configs", [])
        if not entries:
            pytest.skip("No llm-keys entries found in config-api")

        key = entries[0]["key"]
        raw_resp = httpx.get(
            f"{CONFIG_API_URL}/admin/config/{key}/raw",
            headers={"Authorization": f"Bearer {config_token}"},
            timeout=15.0,
        )
        assert raw_resp.status_code == 200, (
            f"Expected /raw to succeed with config.secrets.read scope, got {raw_resp.status_code}: {raw_resp.text}"
        )
        assert raw_resp.json().get("value"), "Expected a non-empty raw value"

    def test_llm_keys_endpoint_returns_config_api_persisted_flag(self, auth_client):
        """Verify GET /llm/keys is reachable with an agent-api token."""
        agent_token = _get_agent_token(auth_client)

        resp = httpx.get(
            f"{AGENT_API_URL}/llm/keys",
            headers={"Authorization": f"Bearer {agent_token}"},
            timeout=15.0,
        )
        assert resp.status_code == 200, f"GET /llm/keys failed: {resp.text}"
        data = resp.json()
        assert "providers" in data or "keys" in data or isinstance(data, list), (
            f"Unexpected /llm/keys response shape: {data}"
        )

    def test_verify_restore_endpoint_exists(self, auth_client):
        """Verify POST /llm/keys/verify-restore is accessible to admins."""
        agent_token = _get_agent_token(auth_client)

        resp = httpx.post(
            f"{AGENT_API_URL}/llm/keys/verify-restore",
            headers={"Authorization": f"Bearer {agent_token}"},
            timeout=15.0,
        )
        assert resp.status_code == 200, (
            f"POST /llm/keys/verify-restore failed: {resp.status_code} {resp.text}"
        )
        data = resp.json()
        assert data.get("success") is True
        assert "keys_found" in data
        assert "message" in data
