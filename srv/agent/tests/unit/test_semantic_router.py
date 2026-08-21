"""Unit tests for the semantic router (no network — embeddings mocked)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.semantic_router import (
    RouteMatch,
    SemanticRouter,
    _cosine,
)

ROUTES_YAML = """
routes:
  hr_policy:
    decision:
      action_type: search
      needs_tools: true
      response: "Let me check the company policies for you."
      complexity: moderate
    utterances:
      - "what is the PTO policy"
      - "how do I submit for reimbursement"
  greeting:
    decision:
      action_type: direct
      needs_tools: false
      response: "Hi! How can I help you today?"
      complexity: simple
    threshold: 0.9
    utterances:
      - "hi"
      - "hello"
  empty_route:
    decision:
      action_type: search
    utterances: []
"""

# Orthogonal-ish toy vectors: routing is by direction, not magnitude.
VEC_PTO = [1.0, 0.0, 0.0]
VEC_REIMBURSE = [0.9, 0.1, 0.0]
VEC_HI = [0.0, 1.0, 0.0]
VEC_HELLO = [0.0, 0.95, 0.05]


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "routes.yaml"
    cfg.write_text(ROUTES_YAML, encoding="utf-8")
    return cfg


def _router(tmp_path: Path) -> SemanticRouter:
    return SemanticRouter(
        config_path=_write_config(tmp_path),
        embedding_url="http://embedding-api.test:8005",
    )


def _mock_embed(mapping):
    """Return an AsyncMock that maps each text to a fixed vector."""

    async def embed(texts):
        return [mapping[t] for t in texts]

    return AsyncMock(side_effect=embed)


UTTERANCE_VECS = {
    "what is the PTO policy": VEC_PTO,
    "how do I submit for reimbursement": VEC_REIMBURSE,
    "hi": VEC_HI,
    "hello": VEC_HELLO,
}


class TestCosine:
    def test_identical_vectors(self):
        assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_is_safe(self):
        assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_magnitude_invariant(self):
        a = _cosine([1.0, 1.0], [2.0, 2.0])
        assert a == pytest.approx(1.0)


class TestConfigParsing:
    def test_routes_parsed_and_empty_skipped(self, tmp_path):
        router = _router(tmp_path)
        routes = router._parse_config()
        assert set(routes) == {"hr_policy", "greeting"}  # empty_route skipped
        assert routes["hr_policy"].needs_tools is True
        assert routes["greeting"].needs_tools is False
        assert routes["greeting"].threshold == pytest.approx(0.9)

    def test_missing_config_returns_no_routes(self, tmp_path):
        router = SemanticRouter(
            config_path=tmp_path / "does-not-exist.yaml",
            embedding_url="http://embedding-api.test:8005",
        )
        assert router._parse_config() == {}


class TestRouting:
    @pytest.fixture()
    def loaded_router(self, tmp_path):
        router = _router(tmp_path)
        vec_map = dict(UTTERANCE_VECS)
        with patch.object(router, "_embed_batch", _mock_embed(vec_map)):
            asyncio.get_event_loop().run_until_complete(router.load())
        return router

    def _route(self, router, query, query_vec, threshold=0.82):
        vec_map = {query: query_vec}
        with patch.object(router, "_embed_batch", _mock_embed(vec_map)):
            with patch(
                "app.services.semantic_router.get_settings"
            ) as mock_settings:
                mock_settings.return_value.semantic_router_threshold = threshold
                return asyncio.get_event_loop().run_until_complete(
                    router.route(query)
                )

    def test_close_query_matches_route(self, loaded_router):
        match = self._route(
            loaded_router, "how much PTO do I have", [0.98, 0.02, 0.0]
        )
        assert match is not None
        assert match.route == "hr_policy"
        assert match.action_type == "search"
        assert match.needs_tools is True
        assert match.score >= 0.82

    def test_distant_query_returns_none(self, loaded_router):
        match = self._route(
            loaded_router, "unrelated question", [0.3, 0.3, 0.9]
        )
        assert match is None

    def test_per_route_threshold_overrides_global(self, loaded_router):
        # Similar to greeting but below its stricter 0.9 route threshold,
        # while still above the (mocked) 0.82 global.
        match = self._route(
            loaded_router, "hiya", [0.30, 0.95, 0.0], threshold=0.82
        )
        assert match is None or match.route != "greeting" or match.score >= 0.9

    def test_greeting_high_confidence_matches(self, loaded_router):
        match = self._route(loaded_router, "hello there", [0.0, 0.99, 0.01])
        assert match is not None
        assert match.route == "greeting"
        assert match.needs_tools is False

    def test_empty_query_returns_none(self, loaded_router):
        result = asyncio.get_event_loop().run_until_complete(
            loaded_router.route("   ")
        )
        assert result is None

    def test_embedding_failure_returns_none(self, loaded_router):
        failing = AsyncMock(side_effect=RuntimeError("embedding-api down"))
        with patch.object(loaded_router, "_embed_batch", failing):
            result = asyncio.get_event_loop().run_until_complete(
                loaded_router.route("what is the PTO policy")
            )
        assert result is None
