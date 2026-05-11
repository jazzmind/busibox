"""
Integration test: LLM provider keys survive a LiteLLM restart.

Proves the full restore loop:
1. Keys are configured in LiteLLM AND persisted to config-api.
2. LiteLLM is restarted (simulating OOM kill or salt key mismatch).
3. The first authenticated request to agent-api triggers _ensure_litellm_keys,
   which reads from config-api and pushes keys back to LiteLLM.
4. Provider keys are still configured — LiteLLM can route to cloud providers.

Markers:
    integration -- requires running services (agent-api, LiteLLM, config-api, authz)
    slow        -- involves a container restart (30-90 seconds total)

Run with:
    make test-docker SERVICE=agent ARGS="tests/integration/test_llm_key_restore.py" FAST=0
    # or against staging:
    make test-local SERVICE=agent INV=staging ARGS="tests/integration/test_llm_key_restore.py"

Prerequisites:
    - AGENT_API_URL env var pointing at a running agent-api
    - LITELLM_BASE_URL env var (or discoverable via agent-api health)
    - At least one cloud provider key already configured (Bedrock or OpenAI)
    - Docker available so we can restart the litellm container
    - Admin credentials available via the test auth_client fixture
"""

import asyncio
import os
import subprocess
import time
import pytest
import httpx

# ---------------------------------------------------------------------------
# Service config
# ---------------------------------------------------------------------------
AGENT_API_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
LITELLM_CONTAINER_NAME = os.getenv("LITELLM_CONTAINER_NAME", "litellm")
LITELLM_RESTART_TIMEOUT = int(os.getenv("LITELLM_RESTART_TIMEOUT", "90"))

pytestmark = [pytest.mark.integration, pytest.mark.slow]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_headers(auth_client) -> dict:
    token = auth_client.get_token(audience="agent-api")
    return {"Authorization": f"Bearer {token}"}


def _get_configured_providers(headers: dict) -> list[str]:
    """Return list of provider names that are currently configured."""
    resp = httpx.get(f"{AGENT_API_URL}/llm/keys", headers=headers, timeout=15)
    if resp.status_code != 200:
        return []
    data = resp.json()
    # Handle both list and dict response shapes
    if isinstance(data, list):
        return [p["provider"] for p in data if p.get("configured")]
    providers = data.get("providers", [])
    return [p["provider"] for p in providers if p.get("configured")]


def _restart_litellm_container() -> bool:
    """Restart the LiteLLM Docker container. Returns True if successful."""
    result = subprocess.run(
        ["docker", "compose", "restart", LITELLM_CONTAINER_NAME],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        # Try plain docker restart as fallback (e.g. if running in a plain Docker env)
        result2 = subprocess.run(
            ["docker", "restart", LITELLM_CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result2.returncode == 0
    return True


def _wait_for_litellm_healthy(headers: dict, timeout: int = LITELLM_RESTART_TIMEOUT) -> bool:
    """Poll /llm/health until LiteLLM is reachable or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{AGENT_API_URL}/llm/health", headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                # LiteLLM is healthy if the agent can reach it
                if data.get("litellm_reachable") or data.get("status") == "healthy":
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestLLMKeyRestoreAfterRestart:
    """Prove that LLM provider keys are automatically restored after a LiteLLM restart."""

    def test_keys_survive_litellm_restart(self, auth_client):
        """
        Full end-to-end restart test.

        Steps:
        1. Confirm at least one cloud provider is configured.
        2. Confirm keys are persisted in config-api (via verify-restore reporting keys_found > 0).
        3. Restart LiteLLM container.
        4. Wait for LiteLLM to be healthy again.
        5. Reset agent-api's in-memory key state via POST /llm/keys/verify-restore.
        6. Assert providers are still configured (restore succeeded).
        """
        headers = _agent_headers(auth_client)

        # Step 1: Verify at least one provider is configured before we start
        initial_providers = _get_configured_providers(headers)
        if not initial_providers:
            pytest.skip(
                "No cloud providers configured in LiteLLM. "
                "Save Bedrock or OpenAI keys via Settings > AI Models before running this test."
            )

        # Step 2: Confirm config-api has the durable copy
        restore_resp = httpx.post(
            f"{AGENT_API_URL}/llm/keys/verify-restore",
            headers=headers,
            timeout=30,
        )
        assert restore_resp.status_code == 200, (
            f"verify-restore failed: {restore_resp.status_code} {restore_resp.text}"
        )
        restore_data = restore_resp.json()
        keys_in_config_api = restore_data.get("keys_found", 0)
        if keys_in_config_api == 0:
            pytest.skip(
                "Keys are not persisted in config-api (keys_found=0). "
                "Re-save provider keys via the admin UI so they are backed up to config-api, "
                "then re-run this test."
            )

        # Step 3: Restart LiteLLM
        restarted = _restart_litellm_container()
        if not restarted:
            pytest.skip(
                f"Could not restart LiteLLM container '{LITELLM_CONTAINER_NAME}'. "
                "Ensure Docker is available and the container name matches LITELLM_CONTAINER_NAME env var."
            )

        # Give LiteLLM a moment to actually stop before we poll health
        time.sleep(5)

        # Step 4: Wait for LiteLLM to come back
        is_healthy = _wait_for_litellm_healthy(headers)
        assert is_healthy, (
            f"LiteLLM did not become healthy within {LITELLM_RESTART_TIMEOUT}s after restart. "
            "Check LiteLLM container logs."
        )

        # Step 5: Reset agent-api's in-memory key state and trigger restore
        # This simulates what happens on the first authenticated request after restart.
        restore_resp2 = httpx.post(
            f"{AGENT_API_URL}/llm/keys/verify-restore",
            headers=headers,
            timeout=30,
        )
        assert restore_resp2.status_code == 200, (
            f"verify-restore after restart failed: {restore_resp2.status_code} {restore_resp2.text}"
        )
        restore_data2 = restore_resp2.json()

        # Step 6: Assert keys were restored
        keys_after = restore_data2.get("keys_found", 0)
        assert keys_after > 0, (
            f"No provider keys found after LiteLLM restart + restore attempt. "
            f"verify-restore response: {restore_data2}. "
            f"This means _ensure_litellm_keys failed to restore from config-api. "
            f"Check agent-api logs for [RESTORE] entries."
        )

        # Also verify the provider list still matches what we had before
        providers_after = _get_configured_providers(headers)
        for provider in initial_providers:
            assert provider in providers_after, (
                f"Provider '{provider}' was configured before restart but is missing after restore. "
                f"Providers after: {providers_after}"
            )

    def test_verify_restore_reports_keys(self, auth_client):
        """
        Simpler smoke test: POST /llm/keys/verify-restore resets the verified flag
        and re-checks. This doesn't require a real container restart — it just verifies
        the endpoint works and the restore logic runs without error.
        """
        headers = _agent_headers(auth_client)

        resp = httpx.post(
            f"{AGENT_API_URL}/llm/keys/verify-restore",
            headers=headers,
            timeout=30,
        )
        assert resp.status_code == 200, f"verify-restore failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data.get("success") is True
        assert isinstance(data.get("keys_found"), int)
        assert isinstance(data.get("providers_detected"), list)
        assert "message" in data
