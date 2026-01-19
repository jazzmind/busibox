"""
Unit tests for API endpoints.
"""

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_liveness(self, client: TestClient):
        """Test liveness probe."""
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readiness(self, client: TestClient):
        """Test readiness probe."""
        response = client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        # Services may not be fully initialized in test
        assert "freeswitch_connected" in data
        assert "transcription_ready" in data

    def test_root(self, client: TestClient):
        """Test root endpoint."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "voice-agent"
        assert "version" in data


class TestCallsAPI:
    """Tests for call management API."""

    def test_list_calls_empty(self, client: TestClient):
        """Test listing calls when none exist."""
        response = client.get("/api/v1/calls")
        assert response.status_code == 200
        assert response.json() == []

    def test_start_call_validation(self, client: TestClient):
        """Test call start validation."""
        # Missing phone number
        response = client.post(
            "/api/v1/calls",
            json={},
        )
        assert response.status_code == 422

        # Invalid max_parallel_lines
        response = client.post(
            "/api/v1/calls",
            json={
                "phone_number": "+18001234567",
                "max_parallel_lines": 10,  # Max is 5
            },
        )
        assert response.status_code == 422


class TestTranscriptsAPI:
    """Tests for transcript management API."""

    def test_list_transcripts_empty(self, client: TestClient):
        """Test listing transcripts when none exist."""
        response = client.get("/api/v1/transcripts")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_nonexistent_transcript(self, client: TestClient):
        """Test getting a transcript that doesn't exist."""
        import uuid
        response = client.get(f"/api/v1/transcripts/{uuid.uuid4()}")
        assert response.status_code == 404
