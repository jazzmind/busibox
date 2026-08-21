"""
Semantic Router — embedding-based intent routing fast path.

Routes user queries by embedding similarity against example utterances,
providing a ~100ms deterministic alternative to the fast-ack LLM call for
common, well-separated intents.

Deployment stages (see docs/developers/architecture — chat AI):
- shadow: router runs and logs its decision alongside the LLM classifier,
  but the LLM decision is always used. Zero behavior change; used to
  measure agreement and tune the threshold.
- live: matches at or above the confidence threshold short-circuit the
  fast-ack LLM call. Below-threshold queries fall through to the existing
  classifier unchanged.

Route definitions live in a YAML file (default: config/routes.yaml under
the agent service root) so routes can be tuned without a code deploy —
edit the file and restart the service.

Uses the existing embedding-api (settings.embedding_api_url) as the
encoder — the same nomic model that powers document search — so no new
model dependencies are introduced. Cosine similarity is computed in pure
Python; with a few hundred example utterances this is well under 1ms.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import yaml

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Default location: srv/agent/config/routes.yaml (repo) which deploys to
# the service root alongside app/.
_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "routes.yaml"
)


@dataclass
class RouteDefinition:
    """A single route: decision template + example utterances."""

    name: str
    utterances: List[str]
    action_type: str = "search"
    needs_tools: bool = True
    response: str = "Let me look into that for you."
    complexity: str = "moderate"
    # Optional per-route threshold override (falls back to global).
    threshold: Optional[float] = None
    # Embedded lazily at load time.
    vectors: List[List[float]] = field(default_factory=list)


@dataclass
class RouteMatch:
    """Result of routing a query."""

    route: str
    score: float
    action_type: str
    needs_tools: bool
    response: str
    complexity: str
    matched_utterance: str
    elapsed_ms: int


def _cosine(a: List[float], b: List[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class SemanticRouter:
    """Embedding-similarity router over YAML-defined routes."""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        embedding_url: Optional[str] = None,
    ) -> None:
        self.config_path = Path(config_path or _DEFAULT_CONFIG_PATH)
        self.embedding_url = (
            embedding_url or str(get_settings().embedding_api_url)
        ).rstrip("/")
        self.routes: Dict[str, RouteDefinition] = {}
        self.loaded = False
        self._load_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _parse_config(self) -> Dict[str, RouteDefinition]:
        if not self.config_path.exists():
            logger.warning(
                "semantic_router: config not found at %s — router disabled",
                self.config_path,
            )
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        routes: Dict[str, RouteDefinition] = {}
        for name, spec in (raw.get("routes") or {}).items():
            utterances = [
                u.strip() for u in (spec.get("utterances") or []) if u and u.strip()
            ]
            if not utterances:
                logger.warning(
                    "semantic_router: route '%s' has no utterances — skipped", name
                )
                continue
            decision = spec.get("decision") or {}
            routes[name] = RouteDefinition(
                name=name,
                utterances=utterances,
                action_type=decision.get("action_type", "search"),
                needs_tools=bool(decision.get("needs_tools", True)),
                response=decision.get(
                    "response", "Let me look into that for you."
                ),
                complexity=decision.get("complexity", "moderate"),
                threshold=spec.get("threshold"),
            )
        return routes

    async def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed texts via embedding-api (OpenAI-compatible /embed).

        Request: {"input": [text, ...]}
        Response: {"data": [{"embedding": [...], "index": 0}, ...], ...}
        (Same contract used by insights_service and the search embedder.)
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.embedding_url}/embed",
                json={"input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        items = data.get("data") or []
        if len(items) != len(texts):
            raise ValueError(
                "semantic_router: embedding count mismatch "
                f"({len(items)} != {len(texts)})"
            )
        items.sort(key=lambda d: d.get("index", 0))
        return [item.get("embedding", []) for item in items]

    async def load(self) -> bool:
        """Parse the YAML config and embed all route utterances.

        Safe to call multiple times; subsequent calls are no-ops unless
        reload() is used. Returns True when at least one route is active.
        """
        async with self._load_lock:
            if self.loaded:
                return bool(self.routes)
            routes = self._parse_config()
            if not routes:
                self.loaded = True
                return False
            # One batch per route keeps request sizes small and failures
            # isolated — a bad route doesn't take down the others.
            for route in routes.values():
                try:
                    route.vectors = await self._embed_batch(route.utterances)
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "semantic_router: failed embedding route '%s': %s",
                        route.name,
                        e,
                    )
                    route.vectors = []
            self.routes = {
                name: r for name, r in routes.items() if r.vectors
            }
            self.loaded = True
            logger.info(
                "semantic_router: loaded %d routes (%d utterances) from %s",
                len(self.routes),
                sum(len(r.utterances) for r in self.routes.values()),
                self.config_path,
            )
            return bool(self.routes)

    async def reload(self) -> bool:
        """Re-read the YAML and re-embed. For config changes at runtime."""
        async with self._load_lock:
            self.loaded = False
            self.routes = {}
        return await self.load()

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def route(self, query: str) -> Optional[RouteMatch]:
        """Return the best route match for a query, or None.

        None means: router unavailable, no routes loaded, embedding
        failure, or best score below the applicable threshold. Callers
        treat None as "fall through to the LLM classifier".
        """
        if not query or not query.strip():
            return None
        if not self.loaded:
            await self.load()
        if not self.routes:
            return None

        t0 = time.monotonic()
        try:
            query_vec = (await self._embed_batch([query.strip()]))[0]
        except Exception as e:  # noqa: BLE001
            logger.warning("semantic_router: query embedding failed: %s", e)
            return None

        global_threshold = get_settings().semantic_router_threshold
        best: Optional[RouteMatch] = None
        for route in self.routes.values():
            for utterance, vec in zip(route.utterances, route.vectors):
                score = _cosine(query_vec, vec)
                if best is None or score > best.score:
                    best = RouteMatch(
                        route=route.name,
                        score=score,
                        action_type=route.action_type,
                        needs_tools=route.needs_tools,
                        response=route.response,
                        complexity=route.complexity,
                        matched_utterance=utterance,
                        elapsed_ms=0,
                    )
        if best is None:
            return None
        best.elapsed_ms = round((time.monotonic() - t0) * 1000)

        threshold = (
            self.routes[best.route].threshold
            if self.routes[best.route].threshold is not None
            else global_threshold
        )
        if best.score < threshold:
            logger.debug(
                "semantic_router: below threshold (%.3f < %.3f) route=%s",
                best.score,
                threshold,
                best.route,
            )
            return None
        return best


_router: Optional[SemanticRouter] = None


def get_semantic_router() -> SemanticRouter:
    """Singleton accessor used by the chat agent."""
    global _router
    if _router is None:
        cfg = get_settings().semantic_router_config_path
        _router = SemanticRouter(config_path=Path(cfg) if cfg else None)
    return _router
