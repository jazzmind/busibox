"""Shared configuration, rate limiting, caching, and error codes for the scraper stack.

This module is the single source of truth for the stealth scraper engine used by
`web_scraper_tool` and `playwright_tool`. It provides:

- UA profiles: coherent (User-Agent, sec-ch-ua, platform, locale, viewport) tuples
- Header builder: produces fully-consistent browser request headers
- Per-domain rate limiter + concurrency semaphore
- Redis-backed response cache
- Proxy configuration from environment
- Resource-block patterns for browser tiers
- Structured error-code taxonomy

The scraper is used as a general capability for agentic search and any apps that need robust web access.
"""

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# =============================================================================
# Error taxonomy
# =============================================================================

class ScraperErrorCode(str, Enum):
    """Structured error codes so agents can make smart retry decisions."""

    OK = "OK"
    BLOCKED_403 = "BLOCKED_403"
    BLOCKED_429 = "BLOCKED_429"
    BLOCKED_CLOUDFLARE = "BLOCKED_CLOUDFLARE"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    TIMEOUT = "TIMEOUT"
    DNS_FAILED = "DNS_FAILED"
    TLS_FAILED = "TLS_FAILED"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    JS_REQUIRED = "JS_REQUIRED"
    PDF_INGESTED = "PDF_INGESTED"
    PDF_INGEST_FAILED = "PDF_INGEST_FAILED"
    UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"
    PARSE_FAILED = "PARSE_FAILED"
    INVALID_URL = "INVALID_URL"
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    UNKNOWN = "UNKNOWN"


def classify_http_status(status: int) -> ScraperErrorCode:
    """Map an HTTP status code to a structured error code."""
    if status == 403:
        return ScraperErrorCode.BLOCKED_403
    if status == 429:
        return ScraperErrorCode.BLOCKED_429
    if status in (502, 503, 504):
        return ScraperErrorCode.JS_REQUIRED
    return ScraperErrorCode.UNKNOWN


def detect_cloudflare_or_captcha(body: str, headers: Dict[str, str]) -> Optional[ScraperErrorCode]:
    """Heuristic detection for Cloudflare challenges and CAPTCHAs in response body/headers."""
    if not body:
        return None
    server_header = (headers.get("server") or headers.get("Server") or "").lower()
    cf_ray = headers.get("cf-ray") or headers.get("CF-Ray") or ""
    body_sample = body[:4096].lower()
    if "cf-chl-opt" in body_sample or "challenge-platform" in body_sample or "cf_chl_" in body_sample:
        return ScraperErrorCode.BLOCKED_CLOUDFLARE
    if "enable javascript and cookies" in body_sample and ("cloudflare" in body_sample or cf_ray):
        return ScraperErrorCode.BLOCKED_CLOUDFLARE
    if "turnstile" in body_sample or "hcaptcha" in body_sample or "g-recaptcha" in body_sample:
        return ScraperErrorCode.CAPTCHA_REQUIRED
    if "cloudflare" in server_header and "just a moment" in body_sample:
        return ScraperErrorCode.BLOCKED_CLOUDFLARE
    return None


# =============================================================================
# UA profiles
# =============================================================================

@dataclass(frozen=True)
class UAProfile:
    """A coherent browser fingerprint: UA, client hints, viewport, locale, TZ.

    All fields must be mutually consistent — a Chrome 131 UA with a Firefox-style
    `Sec-Ch-Ua` header is a classic detection signal.
    """

    name: str
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str
    sec_ch_ua_platform: str
    accept_language: str
    viewport_width: int
    viewport_height: int
    locale: str
    timezone_id: str
    # curl_cffi impersonation target (must match major browser & version)
    curl_impersonate: str
    # Playwright browser engine that matches this profile
    playwright_engine: str = "chromium"


# Modern browser profiles. User-Agent versions are intentionally kept current-ish;
# update these quarterly to match real browser release trains.
UA_PROFILES: Tuple[UAProfile, ...] = (
    UAProfile(
        name="chrome131_win",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform='"Windows"',
        accept_language="en-US,en;q=0.9",
        viewport_width=1920,
        viewport_height=1080,
        locale="en-US",
        timezone_id="America/New_York",
        curl_impersonate="chrome131",
    ),
    UAProfile(
        name="chrome131_mac",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform='"macOS"',
        accept_language="en-US,en;q=0.9",
        viewport_width=1680,
        viewport_height=1050,
        locale="en-US",
        timezone_id="America/Los_Angeles",
        curl_impersonate="chrome131",
    ),
    UAProfile(
        name="chrome124_linux",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform='"Linux"',
        accept_language="en-US,en;q=0.9",
        viewport_width=1920,
        viewport_height=1080,
        locale="en-US",
        timezone_id="America/Chicago",
        curl_impersonate="chrome124",
    ),
    UAProfile(
        name="firefox121_win",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
            "Gecko/20100101 Firefox/121.0"
        ),
        # Firefox does not send Sec-Ch-Ua; we leave these empty and the builder
        # strips them so the profile stays consistent.
        sec_ch_ua="",
        sec_ch_ua_mobile="",
        sec_ch_ua_platform="",
        accept_language="en-US,en;q=0.5",
        viewport_width=1920,
        viewport_height=1080,
        locale="en-US",
        timezone_id="America/New_York",
        curl_impersonate="firefox120",
        playwright_engine="firefox",
    ),
    UAProfile(
        name="safari17_mac",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        ),
        sec_ch_ua="",
        sec_ch_ua_mobile="",
        sec_ch_ua_platform="",
        accept_language="en-US,en;q=0.9",
        viewport_width=1680,
        viewport_height=1050,
        locale="en-US",
        timezone_id="America/Los_Angeles",
        curl_impersonate="safari17_0",
        playwright_engine="webkit",
    ),
)


def pick_profile(preferred: Optional[str] = None) -> UAProfile:
    """Pick a UA profile by name, or return a randomly chosen chromium-compatible one."""
    if preferred:
        for p in UA_PROFILES:
            if p.name == preferred:
                return p
    # Default pool: chromium-compatible profiles (most servers expect Chrome-like behavior)
    chromium_pool = [p for p in UA_PROFILES if p.playwright_engine == "chromium"]
    return random.choice(chromium_pool)


def build_headers(profile: UAProfile, url: str, referer: Optional[str] = None) -> Dict[str, str]:
    """Build a fully-consistent set of browser headers for the given profile.

    Sec-Ch-Ua headers are only emitted for Chromium profiles; Firefox/Safari omit them.
    Sec-Fetch-Site is derived from whether we have a same-site referer.
    """
    parsed = urlparse(url)
    same_site = False
    if referer:
        try:
            r = urlparse(referer)
            same_site = bool(r.netloc) and r.netloc == parsed.netloc
        except Exception:
            same_site = False

    headers: Dict[str, str] = {
        "User-Agent": profile.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": profile.accept_language,
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if same_site else "none",
        "Sec-Fetch-User": "?1",
    }
    if profile.sec_ch_ua:
        headers["Sec-Ch-Ua"] = profile.sec_ch_ua
        headers["Sec-Ch-Ua-Mobile"] = profile.sec_ch_ua_mobile
        headers["Sec-Ch-Ua-Platform"] = profile.sec_ch_ua_platform
    if referer:
        headers["Referer"] = referer
    return headers


# =============================================================================
# Rate limiting + per-domain concurrency
# =============================================================================

@dataclass
class _DomainState:
    last_hit: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    semaphore: Optional[asyncio.Semaphore] = None


class RateLimiter:
    """Per-domain rate limiter with random jitter and concurrency cap.

    Prevents parallel agents from hammering the same host. Each domain has:
    - A minimum interval between consecutive requests (default 2s)
    - Random jitter of 0.5x-1.5x applied to interval
    - A semaphore capping simultaneous in-flight requests (default 2)
    """

    def __init__(
        self,
        min_interval_seconds: float = 2.0,
        max_concurrent_per_domain: int = 2,
        jitter_min: float = 0.5,
        jitter_max: float = 1.5,
    ) -> None:
        self.min_interval = min_interval_seconds
        self.max_concurrent = max_concurrent_per_domain
        self.jitter_min = jitter_min
        self.jitter_max = jitter_max
        self._domains: Dict[str, _DomainState] = {}
        self._domains_lock = asyncio.Lock()

    async def _get_state(self, domain: str) -> _DomainState:
        async with self._domains_lock:
            state = self._domains.get(domain)
            if state is None:
                state = _DomainState(
                    semaphore=asyncio.Semaphore(self.max_concurrent),
                )
                self._domains[domain] = state
            return state

    async def acquire(self, url: str) -> "RateLimitTicket":
        """Acquire a rate-limit slot for `url`. Blocks until slot is available."""
        domain = urlparse(url).netloc or "_default_"
        state = await self._get_state(domain)
        assert state.semaphore is not None

        await state.semaphore.acquire()
        try:
            async with state.lock:
                now = time.monotonic()
                jitter = random.uniform(self.jitter_min, self.jitter_max)
                required_gap = self.min_interval * jitter
                elapsed = now - state.last_hit
                if state.last_hit > 0 and elapsed < required_gap:
                    wait_for = required_gap - elapsed
                    logger.debug("Rate limit %s: sleeping %.2fs", domain, wait_for)
                    await asyncio.sleep(wait_for)
                state.last_hit = time.monotonic()
        except Exception:
            state.semaphore.release()
            raise

        return RateLimitTicket(state)


class RateLimitTicket:
    """Context-manager-like release handle returned by `RateLimiter.acquire()`."""

    def __init__(self, state: _DomainState) -> None:
        self._state = state
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        assert self._state.semaphore is not None
        self._state.semaphore.release()

    async def __aenter__(self) -> "RateLimitTicket":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


# Global rate limiter instance. Configurable via env vars.
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


_global_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = RateLimiter(
            min_interval_seconds=_float_env("SCRAPER_MIN_INTERVAL_SECONDS", 2.0),
            max_concurrent_per_domain=_int_env("SCRAPER_MAX_CONCURRENT_PER_DOMAIN", 2),
        )
    return _global_rate_limiter


# =============================================================================
# Response cache (Redis-backed, with in-memory fallback for tests)
# =============================================================================

_CACHE_VERSION = "v1"  # bump when cached payload shape changes


def cache_key(url: str, tier: str, render_js: bool) -> str:
    """Compute the cache key for a scrape request."""
    raw = f"{_CACHE_VERSION}|{tier}|{render_js}|{url}".encode("utf-8")
    return "scraper:" + hashlib.sha256(raw).hexdigest()


class _InMemoryCache:
    """Fallback cache used when Redis is unavailable (tests, local dev without Redis)."""

    def __init__(self, max_entries: int = 1024) -> None:
        self._data: Dict[str, Tuple[float, str]] = {}
        self._max = max_entries
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                self._data.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        async with self._lock:
            if len(self._data) >= self._max:
                # drop oldest entry (naive LRU)
                oldest = min(self._data.items(), key=lambda kv: kv[1][0])
                self._data.pop(oldest[0], None)
            self._data[key] = (time.time() + ttl, value)


_redis_client: Any = None
_redis_tried = False
_in_memory_cache: Optional[_InMemoryCache] = None


async def _get_redis_client() -> Any:
    """Return a connected Redis client, or None if unavailable. Tries once, then gives up."""
    global _redis_client, _redis_tried
    if _redis_client is not None or _redis_tried:
        return _redis_client

    _redis_tried = True
    redis_url = os.getenv("REDIS_URL") or os.getenv("SCRAPER_REDIS_URL")
    if not redis_url:
        logger.info("Scraper cache: REDIS_URL not set, using in-memory fallback")
        return None

    try:
        import redis.asyncio as aioredis  # type: ignore
        client = aioredis.from_url(redis_url, decode_responses=True)
        # Ping synchronously to verify connection is live
        await client.ping()
        _redis_client = client
        logger.info("Scraper cache: connected to Redis at %s", redis_url.split("@")[-1])
    except Exception as e:
        logger.warning("Scraper cache: Redis unavailable (%s), using in-memory fallback", e)
        _redis_client = None
    return _redis_client


def _get_in_memory_cache() -> _InMemoryCache:
    global _in_memory_cache
    if _in_memory_cache is None:
        _in_memory_cache = _InMemoryCache()
    return _in_memory_cache


async def cache_get(key: str) -> Optional[Dict[str, Any]]:
    """Look up a cached scrape result. Returns the raw dict or None."""
    client = await _get_redis_client()
    raw: Optional[str] = None
    if client is not None:
        try:
            raw = await client.get(key)
        except Exception as e:
            logger.debug("Scraper cache GET failed: %s", e)
            raw = None
    if raw is None:
        raw = await _get_in_memory_cache().get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def cache_set(key: str, payload: Dict[str, Any], ttl_seconds: int) -> None:
    """Store a scrape result in the cache with TTL."""
    if ttl_seconds <= 0:
        return
    try:
        raw = json.dumps(payload, default=str)
    except Exception as e:
        logger.debug("Scraper cache SET serialize failed: %s", e)
        return

    client = await _get_redis_client()
    wrote = False
    if client is not None:
        try:
            await client.set(key, raw, ex=ttl_seconds)
            wrote = True
        except Exception as e:
            logger.debug("Scraper cache SET failed: %s", e)
    if not wrote:
        await _get_in_memory_cache().set(key, raw, ttl_seconds)


# =============================================================================
# Proxy config
# =============================================================================

@dataclass(frozen=True)
class ProxyConfig:
    """Proxy settings resolved from environment variables."""

    url: Optional[str] = None          # e.g. "http://user:pass@host:port"
    username: Optional[str] = None
    password: Optional[str] = None
    rotate: bool = False                # if true, each request gets a new proxy URL (bandwidth-provider-dependent)

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def for_curl_cffi(self) -> Optional[Dict[str, str]]:
        """Return a proxy mapping compatible with curl_cffi/requests."""
        if not self.url:
            return None
        return {"http": self.url, "https": self.url}

    def for_playwright(self) -> Optional[Dict[str, Any]]:
        """Return the `proxy=` dict compatible with playwright's browser launch."""
        if not self.url:
            return None
        parsed = urlparse(self.url)
        server = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            server = f"{server}:{parsed.port}"
        entry: Dict[str, Any] = {"server": server}
        user = self.username or parsed.username
        pw = self.password or parsed.password
        if user:
            entry["username"] = user
        if pw:
            entry["password"] = pw
        return entry


# =============================================================================
# Runtime tool configuration (set by agent context, replaces env vars)
# =============================================================================

_RUNTIME_TOOL_CONFIG: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "scraper_runtime_tool_config", default={}
)


def set_scraper_tool_config(config: dict) -> None:
    """Inject runtime scraper config for the current coroutine context.

    Called by the agent setup code after loading ToolConfig from the DB.
    Overrides environment variables for proxy and camoufox settings so
    operators can configure the scraper through the agent-manager UI
    without touching server environment variables.

    Supported keys:
        camoufox_enabled (bool): enable Tier 3 camoufox scraping
        proxy_url (str): full proxy URL (e.g. http://user:pass@host:port)
        proxy_username (str): proxy username (overrides proxy_url credentials)
        proxy_password (str): proxy password (overrides proxy_url credentials)
        proxy_rotate (bool): rotate proxy identity on each request
    """
    _RUNTIME_TOOL_CONFIG.set(config)


def _get_runtime_config() -> dict:
    return _RUNTIME_TOOL_CONFIG.get()


def get_proxy_config() -> ProxyConfig:
    """Load proxy config from runtime tool config, then environment variables.

    Priority order:
        1. Runtime tool config (set via agent-manager tool configuration)
        2. Environment variables SCRAPER_PROXY_* (legacy/fallback)
    """
    runtime = _get_runtime_config()
    url = runtime.get("proxy_url") or os.getenv("SCRAPER_PROXY_URL") or None
    username = runtime.get("proxy_username") or os.getenv("SCRAPER_PROXY_USERNAME") or None
    password = runtime.get("proxy_password") or os.getenv("SCRAPER_PROXY_PASSWORD") or None
    rotate_env = (os.getenv("SCRAPER_PROXY_ROTATE") or "").lower() in ("1", "true", "yes")
    rotate = runtime.get("proxy_rotate", rotate_env)
    return ProxyConfig(url=url, username=username, password=password, rotate=rotate)


# =============================================================================
# Camoufox gating
# =============================================================================

def camoufox_enabled() -> bool:
    """Whether Tier 3 (Camoufox) is available.

    Priority order:
        1. Runtime tool config key ``camoufox_enabled`` (set via agent-manager)
        2. ENABLE_CAMOUFOX environment variable (legacy/fallback)
    """
    runtime = _get_runtime_config()
    if "camoufox_enabled" in runtime:
        return bool(runtime["camoufox_enabled"])
    return (os.getenv("ENABLE_CAMOUFOX") or "").lower() in ("1", "true", "yes")


# =============================================================================
# Resource blocking patterns for browser tiers
# =============================================================================

# Resource types that Playwright can block via route interception.
# Blocking these cuts bandwidth ~80% on typical pages without affecting text scraping.
BLOCKED_RESOURCE_TYPES: Tuple[str, ...] = (
    "image",
    "media",
    "font",
    "stylesheet",
    "other",
)

# Third-party analytics/tracker hostnames that agents never need to load.
BLOCKED_HOSTNAME_PATTERNS: Tuple[str, ...] = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "googlesyndication.com",
    "google-analytics.l.google.com",
    "facebook.net",
    "facebook.com/tr",
    "hotjar.com",
    "segment.io",
    "mixpanel.com",
    "amplitude.com",
    "fullstory.com",
    "clarity.ms",
    "optimizely.com",
    "newrelic.com",
    "nr-data.net",
    "datadoghq.com",
    "sentry.io",
    "bugsnag.com",
    "cloudflareinsights.com",
    "quantserve.com",
    "scorecardresearch.com",
    "adnxs.com",
    "adsrvr.org",
    "taboola.com",
    "outbrain.com",
)


def should_block_request(resource_type: str, url: str, block_resources: bool) -> bool:
    """Decide whether to abort a Playwright route.

    Always blocks analytics/ad hosts; only blocks images/fonts/CSS when `block_resources=True`.
    """
    low = url.lower()
    for pattern in BLOCKED_HOSTNAME_PATTERNS:
        if pattern in low:
            return True
    if block_resources and resource_type in BLOCKED_RESOURCE_TYPES:
        return True
    return False


# =============================================================================
# Body size caps
# =============================================================================

DEFAULT_MAX_BODY_BYTES = 20 * 1024 * 1024  # 20 MB


def max_body_bytes() -> int:
    return _int_env("SCRAPER_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)


# =============================================================================
# PDF detection
# =============================================================================

def looks_like_pdf(url: str, content_type: Optional[str]) -> bool:
    """Detect whether a response is a PDF, by content-type or URL suffix."""
    if content_type and "application/pdf" in content_type.lower():
        return True
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    return path.endswith(".pdf")
