from functools import lru_cache
from typing import List, Optional

from pydantic import AnyHttpUrl, Field, ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Central application settings, loaded from environment variables.
    """

    app_name: str = "agent-server"
    environment: str = Field("development", description="environment name (development/test/prod)")
    debug: bool = False
    log_level: str = Field("INFO", description="Logging level (DEBUG/INFO/WARNING/ERROR)")

    # Model/provider configuration
    default_model: str = Field(
        "agent",
        description="Default model purpose for LiteLLM (e.g., agent, fast, frontier, etc.)",
    )
    fast_model: str = Field(
        "fast",
        description="Fast/cheap model for simple tasks like query optimization",
    )
    frontier_model: str = Field(
        "frontier",
        description="Most capable model for complex tasks (research, analysis, reports)",
    )
    fallback_model: str = Field(
        "fallback",
        description="Fast cloud model used when local models are at capacity under load",
    )
    load_fallback_threshold: int = Field(
        6,
        description="Active LLM request count at which to switch to the fallback model (should be below vLLM max_num_seqs)",
    )
    data_api_max_concurrent: int = Field(
        20,
        description="Max concurrent outbound HTTP requests to data-api",
    )
    tool_parallel_limit: int = Field(
        5,
        description="Max concurrent tool calls per agent run in parallel mode",
    )
    litellm_base_url: AnyHttpUrl = Field(
        "http://10.96.200.207:4000/v1",
        description="Base URL for LiteLLM proxy (OpenAI-compatible endpoint)",
    )
    litellm_api_key: Optional[str] = Field(
        None,
        description="API key for LiteLLM proxy (if authentication is enabled)",
    )

    # Busibox service endpoints
    search_api_url: AnyHttpUrl = Field(
        "http://10.96.200.204:8003",
        description="Base URL for Busibox search API",
    )
    data_api_url: AnyHttpUrl = Field(
        "http://10.96.200.206:8002",
        description="Base URL for Busibox data API",
    )
    rag_api_url: AnyHttpUrl = Field(
        "http://10.96.200.204:8003",
        description="Base URL for RAG/vector database API",
    )
    
    # Embedding API (dedicated embedding service - no auth required)
    embedding_api_url: str = Field(
        "http://embedding-api:8005",
        description="Dedicated embedding service URL (port 8005). No auth required for internal services.",
    )

    # Semantic router (embedding-based intent routing fast path)
    semantic_router_enabled: bool = Field(
        False,
        description="Enable the semantic router in front of the fast-ack LLM classifier",
    )
    semantic_router_mode: str = Field(
        "shadow",
        description="'shadow' = log router decisions without acting on them; 'live' = confident matches skip the fast-ack LLM call",
    )
    semantic_router_threshold: float = Field(
        0.82,
        description="Global cosine-similarity threshold for a route match (per-route override in routes.yaml)",
    )
    semantic_router_config_path: Optional[str] = Field(
        None,
        description="Path to routes.yaml (defaults to <service root>/config/routes.yaml)",
    )

    # Model aliases that route to cloud providers (Bedrock/OpenAI) via LiteLLM.
    # Local-only params (vLLM/MLX extra_body like chat_template_kwargs) must not
    # be sent to these — cloud providers reject unknown params with a 400.
    # Keep in sync with model purpose mappings when re-pointing aliases.
    cloud_routed_aliases: str = Field(
        "agent,default,chat,research,frontier,frontier-fast,fallback",
        description="Comma-separated model aliases served by cloud providers; vLLM/MLX-only request params are suppressed for these",
    )

    # Attachment context budget. Resolved per turn from the model that will
    # actually synthesize the answer: a 1M-window cloud model can read a whole
    # document where a local 4B model needs retrieved chunks.
    #
    # Deliberately empty here. The alias -> model binding lives in
    # model_registry.yml and differs per backend — "chat" is a 16k MLX model in
    # dev and a 65k vLLM model in production — so any value hardcoded in the
    # service would be wrong somewhere. Ansible renders the real map into the
    # env from the registry (roles/agent_api/templates/agent-api.env.j2).
    # Unset means every alias falls back to `default_context_window_tokens`,
    # which is the conservative pre-existing behaviour.
    model_context_windows: str = Field(
        "",
        description="Comma-separated alias:token-window pairs sizing the attachment budget (rendered by Ansible from model_registry.yml)",
    )
    default_context_window_tokens: int = Field(
        12000,
        description="Context window assumed for model aliases absent from MODEL_CONTEXT_WINDOWS",
    )
    attachment_inline_max_tokens: int = Field(
        24000,
        description="Ceiling on pre-parsed attachment text injected verbatim (no file_id, parsed_content only)",
    )

    # Grounding policy (synthesis): tier selection thresholds
    grounding_strong_doc_score: float = Field(
        0.65,
        description="document_search score at/above which the answer is grounded in documents only (tier 'documents')",
    )
    grounding_stale_after_months: int = Field(
        12,
        description="Documents older than this are flagged as possibly outdated in the synthesis prompt",
    )

    # Clarify review: the fast-ack classifier (0.8B) decides whether a query is
    # ambiguous, but it sees only the query text. When it says "clarify" the
    # decision is re-checked by a larger model before the user is asked
    # anything. Set to "" to disable the second opinion.
    clarify_review_model: str = Field(
        "tool_calling",
        description="Model alias used to confirm or overturn a 'clarify' routing decision (empty disables)",
    )
    clarify_review_timeout_seconds: float = Field(
        6.0,
        description="Max seconds to wait for the clarify review before keeping the original decision",
    )

    # Chat turn budget (escalation guards)
    chat_max_tool_steps: int = Field(
        6,
        description="Maximum planned tool steps executed per chat turn (deep_research is never dropped)",
    )
    chat_turn_budget_seconds: int = Field(
        120,
        description="After this many seconds in a turn, remaining slow tool steps are skipped (deep_research exempt)",
    )

    # Milvus configuration (for insights)
    milvus_host: str = Field(
        "milvus",
        description="Milvus host for insights storage",
    )
    milvus_port: int = Field(
        19530,
        description="Milvus port",
    )

    # Auth configuration
    auth_issuer: Optional[str] = Field(
        None, description="Expected issuer for Busibox JWT tokens (string identifier, not URL)"
    )
    auth_audience: Optional[str] = Field(
        None, description="Expected audience for Busibox JWT tokens"
    )
    auth_jwks_url: Optional[AnyHttpUrl] = Field(
        None, description="JWKS endpoint for Busibox auth (authz)"
    )
    auth_token_url: AnyHttpUrl = Field(
        "http://10.96.200.210:8010/oauth/token",
        description="Token endpoint for OAuth2 token exchange",
    )

    # Database configuration
    database_url: str = Field(
        ...,
        description="SQLAlchemy connection URL (required - no default)",
    )
    
    # LiteLLM database for spend tracking queries (read-only access)
    # Uses asyncpg directly (not SQLAlchemy) for raw queries against LiteLLM's Prisma schema
    litellm_database_url: Optional[str] = Field(
        None,
        description="PostgreSQL connection URL for LiteLLM spend database (e.g., postgresql://user:pass@host:5432/litellm)",
    )
    
    # Test mode configuration
    # When enabled, requests with X-Test-Mode: true header will use test database
    test_mode_enabled: bool = Field(
        False,
        description="Enable test mode support (routes test requests to test database)",
    )
    test_database_url: str = Field(
        "postgresql+asyncpg://busibox_test_user:testpassword@localhost:5432/test_agent",
        description="SQLAlchemy connection URL for test database",
    )

    # Redis/background tasks
    redis_url: str = Field("redis://localhost:6379/0", description="Redis URL for queues/locks")

    # Web Search Provider Configuration
    search_duckduckgo_enabled: bool = Field(True, description="Enable DuckDuckGo search (free)")
    search_tavily_enabled: bool = Field(False, description="Enable Tavily search")
    tavily_api_key: Optional[str] = Field(None, description="Tavily API key")
    tavily_research_timeout_seconds: int = Field(
        240,
        description="Max seconds to wait for a Tavily deep_research task before returning what is available",
    )
    tavily_research_default_model: str = Field(
        "auto",
        description="Tavily research agent model: mini (narrow questions), pro (multi-topic), auto",
    )
    deep_research_confirm: bool = Field(
        True,
        description=(
            "Ask the user (Yes/No) before running a multi-minute deep_research pass. "
            "False announces the expected wait and runs immediately."
        ),
    )
    search_perplexity_enabled: bool = Field(False, description="Enable Perplexity search")
    perplexity_api_key: Optional[str] = Field(None, description="Perplexity API key")
    search_brave_enabled: bool = Field(False, description="Enable Brave search")
    brave_api_key: Optional[str] = Field(None, description="Brave API key")

    # Portal/UI URLs (for notification links)
    portal_base_url: str = Field(
        "https://localhost",
        description="Base URL for portal links in notifications",
    )
    portal_name: str = Field(
        "Busibox",
        description="Display name used in notification subjects (e.g. 'Dredging News from Busibox'). Overridden at runtime from deploy-api config store.",
    )
    deploy_api_url: Optional[str] = Field(
        None,
        description="Deploy API URL for reading runtime config (portal name, etc.)",
    )
    config_api_url: Optional[str] = Field(
        None,
        description="Config API URL for persistent encrypted config storage (e.g., http://config-api:8012)",
    )
    
    # Email configuration
    # Bridge API is the preferred email provider (handles SMTP/Resend internally)
    bridge_api_url: Optional[str] = Field(
        None,
        description="Bridge API URL for sending emails (e.g., http://bridge-api:8081). Preferred over direct SMTP.",
    )
    
    # Legacy SMTP configuration (used only if bridge_api_url is not set)
    smtp_host: Optional[str] = Field(None, description="SMTP server host")
    smtp_port: int = Field(587, description="SMTP server port")
    smtp_username: Optional[str] = Field(None, description="SMTP username")
    smtp_password: Optional[str] = Field(None, description="SMTP password")
    email_from: str = Field(
        "noreply@busibox.local",
        description="Default from address for emails",
    )

    # LLM backend (mlx, vllm, cloud) – set by installer to indicate hardware
    llm_backend: str = Field(
        "",
        description="LLM backend type (mlx, vllm, cloud). Determines whether grammar-level structured output enforcement is available.",
    )

    # CORS
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])

    # OpenTelemetry configuration
    otlp_endpoint: Optional[AnyHttpUrl] = Field(
        None,
        description="OTLP endpoint for trace export (e.g., http://localhost:4317)",
    )
    otel_service_name: Optional[str] = Field(
        None,
        description="Override service name for traces (defaults to app_name)",
    )

    # Skills system (AgentSkills-compatible SKILL.md loader)
    skills_enabled: bool = Field(
        False,
        description="Enable loading SKILL.md skills and injecting them into agent prompts",
    )
    skills_dirs: str = Field(
        "/srv/skills,~/.openclaw/skills,/skills",
        description="Comma-separated directories to scan recursively for SKILL.md files",
    )
    skills_cache_ttl_seconds: int = Field(
        60,
        description="Seconds to cache loaded skills before re-scan",
    )
    skills_allowed_roles: str = Field(
        "",
        description="Optional comma-separated RBAC roles allowed to use skills globally",
    )
    skills_clawhub_enabled: bool = Field(
        False,
        description="Enable ClawHub integration hints for loaded skills",
    )

    def get_model_context_window(self, alias: Optional[str]) -> int:
        """Context window in tokens for a model alias.

        Unknown or unparseable aliases fall back to
        ``default_context_window_tokens`` so a misconfigured map can only make
        the budget smaller, never wrongly large.
        """
        wanted = (alias or "").strip().lower()
        if not wanted:
            return self.default_context_window_tokens
        for pair in (self.model_context_windows or "").split(","):
            name, _, size = pair.partition(":")
            if name.strip().lower() != wanted:
                continue
            try:
                window = int(size.strip())
            except ValueError:
                return self.default_context_window_tokens
            return window if window > 0 else self.default_context_window_tokens
        return self.default_context_window_tokens

    def get_skill_dirs(self) -> List[str]:
        raw = self.skills_dirs or ""
        if not raw.strip():
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def get_skills_allowed_roles(self) -> List[str]:
        raw = self.skills_allowed_roles or ""
        if not raw.strip():
            return []
        return [role.strip().lower() for role in raw.split(",") if role.strip()]

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra fields from .env that aren't in the model
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
