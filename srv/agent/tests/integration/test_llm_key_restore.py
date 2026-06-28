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
    - LITELLM_HOST (or LITELLM_IP) for Proxmox — SSH systemctl restart on litellm-lxc
    - Docker available for docker-compose environments (optional)
    - At least one cloud provider key already configured (Bedrock or OpenAI)
    - Admin credentials available via the test auth_client fixture
"""

import os
import shutil
import subprocess
import time
import pytest
import httpx

# ---------------------------------------------------------------------------
# Service config
# ---------------------------------------------------------------------------
AGENT_API_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
LITELLM_HOST = os.getenv("LITELLM_HOST", os.getenv("LITELLM_IP", "litellm"))
LITELLM_CONTAINER_NAME = os.getenv("LITELLM_CONTAINER_NAME", "litellm")
LITELLM_SYSTEMD_SERVICE = os.getenv("LITELLM_SYSTEMD_SERVICE", "litellm")
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


def _restart_litellm_via_ssh(host: str) -> bool:
    """Restart LiteLLM on a Proxmox LXC via SSH + systemctl."""
    result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=15",
            f"root@{host}",
            f"systemctl restart {LITELLM_SYSTEMD_SERVICE}",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    return result.returncode == 0


def _restart_litellm_via_docker() -> bool:
    """Restart the LiteLLM Docker container (local docker-compose dev)."""
    if not shutil.which("docker"):
        return False
    result = subprocess.run(
        ["docker", "compose", "restart", LITELLM_CONTAINER_NAME],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        return True
    result2 = subprocess.run(
        ["docker", "restart", LITELLM_CONTAINER_NAME],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result2.returncode == 0


def _restart_litellm() -> bool:
    """
    Restart LiteLLM using the best available method.

    Proxmox: SSH to litellm-lxc and systemctl restart (LITELLM_HOST from test runner).
    Docker: docker compose restart litellm.
    Override: set LITELLM_RESTART_CMD to a custom shell command.
    """
    custom_cmd = os.getenv("LITELLM_RESTART_CMD")
    if custom_cmd:
        result = subprocess.run(
            custom_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        return result.returncode == 0

    # Proxmox/LXC first — agent-lxc has no docker but shares SSH keys with peer LXCs
    if _restart_litellm_via_ssh(LITELLM_HOST):
        return True

    return _restart_litellm_via_docker()


def _wait_for_litellm_healthy(headers: dict, timeout: int = LITELLM_RESTART_TIMEOUT) -> bool:
    """Poll /llm/health until LiteLLM is reachable or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{AGENT_API_URL}/llm/health", headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                # HealthResponse uses "litellm" (bool), not "litellm_reachable"
                if data.get("litellm") or data.get("litellm_reachable"):
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


# ---------------------------------------------------------------------------
# Shared restart-cycle steps (used by monolithic + orchestrated Proxmox runs)
# ---------------------------------------------------------------------------

_STATE_FILE = "/tmp/busibox_llm_key_restore_state.json"


def _prepare_restart_cycle(auth_client) -> tuple[dict, list[str]]:
    """Steps 1-2: confirm providers and config-api persistence. Returns (headers, providers)."""
    import json

    headers = _agent_headers(auth_client)
    initial_providers = _get_configured_providers(headers)
    if not initial_providers:
        pytest.skip(
            "No cloud providers configured in LiteLLM. "
            "Save Bedrock or OpenAI keys via Settings > AI Models before running this test."
        )

    restore_resp = httpx.post(
        f"{AGENT_API_URL}/llm/keys/verify-restore",
        headers=headers,
        timeout=30,
    )
    assert restore_resp.status_code == 200, (
        f"verify-restore failed: {restore_resp.status_code} {restore_resp.text}"
    )
    restore_data = restore_resp.json()
    if restore_data.get("keys_found", 0) == 0:
        pytest.skip(
            "Keys are not persisted in config-api (keys_found=0). "
            "Re-save provider keys via the admin UI so they are backed up to config-api, "
            "then re-run this test."
        )

    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"providers": initial_providers}, f)

    return headers, initial_providers


def _verify_after_restart(auth_client, headers: dict | None = None, initial_providers: list[str] | None = None):
    """Steps 4-6: wait for LiteLLM, run verify-restore, assert keys/providers restored."""
    import json

    if headers is None or initial_providers is None:
        if os.path.isfile(_STATE_FILE):
            with open(_STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            initial_providers = state.get("providers", [])
        else:
            initial_providers = []
        headers = _agent_headers(auth_client)

    time.sleep(5)
    assert _wait_for_litellm_healthy(headers), (
        f"LiteLLM did not become healthy within {LITELLM_RESTART_TIMEOUT}s after restart. "
        "Check LiteLLM logs on the litellm-lxc host."
    )

    restore_resp = httpx.post(
        f"{AGENT_API_URL}/llm/keys/verify-restore",
        headers=headers,
        timeout=30,
    )
    assert restore_resp.status_code == 200, (
        f"verify-restore after restart failed: {restore_resp.status_code} {restore_resp.text}"
    )
    restore_data = restore_resp.json()
    keys_after = restore_data.get("keys_found", 0)
    assert keys_after > 0, (
        f"No provider keys found after LiteLLM restart + restore attempt. "
        f"verify-restore response: {restore_data}. "
        f"Check agent-api logs for [RESTORE] entries."
    )

    providers_after = _get_configured_providers(headers)
    for provider in initial_providers:
        assert provider in providers_after, (
            f"Provider '{provider}' was configured before restart but is missing after restore. "
            f"Providers after: {providers_after}"
        )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestLLMKeyRestoreAfterRestart:
    """Prove that LLM provider keys are automatically restored after a LiteLLM restart."""

    def test_keys_survive_litellm_restart_prepare(self, auth_client):
        """
        Part 1 of restart cycle (Proxmox orchestration): verify preconditions only.

        test.sh runs: this test → systemctl restart litellm on litellm-lxc → part 2.
        """
        _prepare_restart_cycle(auth_client)

    def test_keys_survive_litellm_restart_verify(self, auth_client):
        """
        Part 2 of restart cycle (Proxmox orchestration): verify restore after external restart.

        Requires LITELLM_RESTART_EXTERNAL=1 (set by test.sh after restarting litellm).
        """
        if os.getenv("LITELLM_RESTART_EXTERNAL") != "1":
            pytest.skip("Part 2 runs after test runner restarts LiteLLM (orchestrated flow)")
        _verify_after_restart(auth_client)

    def test_keys_survive_litellm_restart(self, auth_client):
        """
        Full end-to-end restart test (Docker / environments where agent can restart LiteLLM).

        On Proxmox, agent-lxc cannot SSH to litellm-lxc — use orchestrated prepare/verify tests
        via test.sh instead. Skipped when LLM_RESTART_ORCHESTRATED=1.
        """
        if os.getenv("LLM_RESTART_ORCHESTRATED") == "1":
            pytest.skip("Restart cycle run via test.sh orchestration on Proxmox")

        headers, initial_providers = _prepare_restart_cycle(auth_client)

        restarted = _restart_litellm()
        if not restarted:
            pytest.skip(
                f"Could not restart LiteLLM on host '{LITELLM_HOST}'. "
                "On Proxmox, re-run with ARGS=tests/integration/test_llm_key_restore.py "
                "or ARGS='-k llm' (test runner orchestrates the restart)."
            )

        _verify_after_restart(auth_client, headers=headers, initial_providers=initial_providers)

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
