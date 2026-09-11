# Changelog

All notable changes to Busibox will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it reaches 1.0.0. While in 0.x, minor versions may include breaking
changes — see release notes per version.

## [Unreleased]

### Added

- **`skip_indexing` PDF upload option for data-api** (off by default).
  `POST /upload`'s `processing_config` now accepts `"skip_indexing": true`
  for PDFs: Pass 1 still extracts text and reaches stage `available` with
  real progress, but skips chunking/embedding/indexing for callers that
  only need the extracted text (e.g. as an LLM prompt) and have no use for
  the document being made searchable. Pass 2 (OCR) is unaffected. See
  `srv/data/src/worker/pipeline.py`'s `_process_pdf_progressive` docstring.
- **Semantic router fast path for chat intent routing** (off by default).
  Embeds queries against example utterances in
  `srv/agent/config/routes.yaml` and skips the fast-ack LLM call on
  confident matches, falling through to the existing classifier
  otherwise. New settings: `SEMANTIC_ROUTER_ENABLED`,
  `SEMANTIC_ROUTER_MODE` (`shadow`/`live`), `SEMANTIC_ROUTER_THRESHOLD`,
  `SEMANTIC_ROUTER_CONFIG_PATH`. See
  `docs/developers/guides/semantic-router.md`.
- **Tavily deep research, extract and map tools for chat.** `web_search` now
  calls Tavily with `search_depth=advanced` and three snippets per source,
  and accepts `topic` (news/finance), `time_range` and `include_domains`.
  New chat tools: `web_extract` (clean markdown for given URLs, scraper
  fallback without a key), `web_map` (site URL discovery) and
  `deep_research` (Tavily Research: multi-search cited report, polled up to
  `TAVILY_RESEARCH_TIMEOUT_SECONDS`). The planner is told to reserve
  `deep_research` for explicit report/deep-dive requests. A `deep_research`
  semantic-router route (plus a regex fallback guard) detects "write a
  report / deep dive / market analysis" phrasing, replies immediately that
  a multi-source research pass will take a few minutes, forces the
  `deep_research` step in the plan, and posts a progress note when the
  step starts; without a Tavily key it downgrades to a normal web search.
- **Tiered grounding policy for synthesis** (`app/services/grounding.py`).
  The synthesis prompt now carries a tier chosen from the evidence —
  attachment, documents, web, estimate, knowledge — plus absence, recency
  (document age), conflict and tool-failure rules, so answers say where
  they come from and never refuse a figure outright. `document_search`
  hits carry `document_date` when the search API provides one. Settings:
  `GROUNDING_STRONG_DOC_SCORE`, `GROUNDING_STALE_AFTER_MONTHS`.
- **Clarify decisions get a second opinion from a larger model.** The 0.8B
  fast-ack classifier judges ambiguity from the query text alone, so a
  well-formed question it cannot answer itself ("who should I ask about IT
  questions?") looked identical to a genuinely ambiguous one. Any `clarify`
  from the classifier is now re-read by `CLARIFY_REVIEW_MODEL` (default
  `tool_calling` — the local 35B, no marginal cost, `CLARIFY_REVIEW_TIMEOUT_SECONDS`
  6 s) which either overturns it to a search or upholds it with a more
  specific question. Any failure leaves the original decision untouched. The
  factual guard now also covers `clarify`, not just `direct`, as a backstop
  when the review is unavailable.
- **The attachment context budget follows the synthesis model.**
  `AttachmentResolver` assumed a 12,000-token window for every turn, so a
  document that Claude Sonnet (200k) could have read whole was cut down to
  RAG chunks — and on a "summarize this" objective the chunk ranker has
  nothing meaningful to match against, so the answer came from a fraction of
  the file. The window is now resolved per turn from the alias that will
  actually write the answer, after any frontier upgrade. Attachments carrying
  pre-parsed text with no `file_id` were injected verbatim at any size and are
  now capped too. Settings: `MODEL_CONTEXT_WINDOWS`,
  `DEFAULT_CONTEXT_WINDOW_TOKENS`, `ATTACHMENT_INLINE_MAX_TOKENS`. Unknown
  aliases and malformed maps fall back to the old 12,000, so a
  misconfiguration can only shrink the budget, never overrun the model.
- **Deep research asks before it runs.** When a request is routed to
  `deep_research`, the turn now stops at an offer — "…it usually takes a few
  minutes. Would you like me to run it?" — rendered with Yes/No buttons. "Yes"
  on the next turn resumes the *original* question (carried as
  `pending_research` on the persisted routing decision, with a text-match
  fallback) straight into a forced `deep_research` plan; "no" closes
  politely; anything else is routed normally. `DEEP_RESEARCH_CONFIRM=false`
  restores announce-and-run.
- **Expanded `config/routes.yaml`.** New routes `who_to_contact`,
  `company_news` and `industry_research`; `hr_policy`, `document_lookup` and
  `company_info` gained utterances mined from real production queries.
  `industry_research` deliberately sits between the two so a plain market
  question cannot be captured by `deep_research` and spend credits.
- **Anti-loop and escalation guards** (`app/services/routing_guards.py`):
  a second clarifying question in a row is replaced by a search; "yes"/"no"
  after an offer becomes the offer (or a polite close) instead of a fresh
  classification; a no-tools answer to a company-fact question (policy,
  rates, holidays, glossary terms) is forced through retrieval; failed
  document/web searches are retried once; a generic fallback plan on a
  complex request escalates to model-driven tool use; per-turn caps
  `CHAT_MAX_TOOL_STEPS` (6) and `CHAT_TURN_BUDGET_SECONDS` (120,
  `deep_research` exempt). Earlier assistant turns in the history now
  carry their routing `action_type`, and every guard emits a `Guard: ...`
  thought.

### Fixed

- **Chat attachments** (September 9 production incident): a message that
  carries a file now always goes to the deep pass (`routing_source =
  attachment_rule`) instead of asking the 0.8B classifier, which had
  answered the last line of its own prompt ("What are the missing profile
  fields you need me to gather?"); profile follow-ups and missing-field
  hints were removed from the classifier prompt and any classifier output
  that echoes prompt scaffolding is discarded; a file sent without a
  question gets a default "summarize the attached document" objective;
  attachments from the previous turn are carried forward and history is
  annotated with `[Attached: ...]`, so "what's the attached?" resolves to
  the file sent last turn; the attachment-only fallback plan no longer runs
  an unrelated web search; a processed PDF with no extractable text
  (scanned) is described as such instead of a bare `[Attachment]`
  placeholder the model could invent contents for.
- **`citations` is always present in `routing_decision`** (empty list when
  no document sources), and is included in the `message_complete` event, so
  the chat UI no longer shows "Sources pending" indefinitely.
- **Agent-api JSON logs keep `extra={...}` fields.** `structlog.stdlib.ExtraAdder`
  was missing from the formatter's `foreign_pre_chain`, so routing and
  planner diagnostics (`action_type`, `needs_tools`, timings) were dropped
  before reaching journald.
- **Chat agent pipeline fixes** (September production review):
  the planner now accepts loosely-typed model output instead of
  discarding every plan (multi-step plans and web search run again);
  synthesized answers can no longer contain tool-call syntax; vLLM-only
  request parameters are suppressed for cloud-routed model aliases (new
  setting `CLOUD_ROUTED_ALIASES`, default
  `agent,default,chat,research,frontier,frontier-fast,fallback`); cloud
  aliases also receive LiteLLM `reasoning_effort` (`medium` for agent/
  default/chat, `high` for research/frontier); replying "yes" to an
  offer no longer crashes the clarify path.
- **Chat fast-path fixes** from the August production review:
  short conversational turns ("hi", "yes") no longer persist
  "No response generated."; the fast classifier no longer streams
  speculative answers as acknowledgments when tools are about to run;
  few-shot examples added to the intent classifier so policy questions
  reach document retrieval. Full findings with evidence in
  `docs/developers/chat-qa-findings-2026-08.md`.

## [0.1.0] — 2026-05-04

Initial public, MIT-licensed release of Busibox. This is an **early-stage
preview** intended for evaluation, lab use, and design-partner pilots. It
has not yet had an independent security audit and APIs / schemas are
expected to evolve before 1.0.

### Added

- **MIT license** (`LICENSE`) and accompanying `NOTICE` listing the
  per-component license obligations for bundled and optional third-party
  components.
- **OSS governance files**: `SECURITY.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, GitHub issue / PR templates under `.github/`.
- **Docker + cloud-key first-run path** as the recommended evaluation
  flow: bring an OpenAI, Anthropic, or AWS Bedrock key, run
  `make docker-up`, and skip GPU / local-inference setup. Documented in
  `README.md`, `QUICKSTART.md`, and
  `docs/administrators/01-quickstart.md`.
- **Local model add-on pack design doc** at
  `docs/administrators/local-models-addon.md` describing the proposed
  Ollama / vLLM / MLX profile layout, install UX, and acceptance criteria.
- **Release checklist** at `docs/developers/release-checklist.md`.

### Existing capabilities (carried forward into 0.1.0)

These were already present in the codebase prior to this tag and are
listed here so the first public changelog gives a complete picture.

- **Self-hosted document platform** with PDF / Office / image ingest,
  chunking, embeddings, and hybrid search (vector + BM25 + graph + LLM
  rerank).
- **Agent API** (FastAPI) with streaming chat, RAG, tool use, attachments,
  and configurable per-agent guardrails (request limits, token / cost
  budgets, timeouts).
- **LiteLLM gateway** routing across OpenAI, Anthropic, AWS Bedrock,
  vLLM (NVIDIA), MLX (Apple Silicon), and Ollama.
- **Zero-Trust auth**: AuthZ service issues RS256 JWTs verified via JWKS;
  per-service audience-scoped subject token exchange. No shared service
  secrets.
- **Passwordless auth** (passkeys, TOTP, magic links) with optional SSO
  via EntraID / SAML.
- **PostgreSQL Row-Level Security** end-to-end for tenant isolation.
- **Envelope encryption** for object storage (Master Key → KEK → DEK).
- **Three-mode document sharing** (private / shared / team) via
  self-service roles.
- **Bridge channels** for Telegram, Signal, Discord, WhatsApp, email.
- **Busibox CLI** (Rust workspace: `busibox-core`, `busibox-providers`,
  `busibox`, `busibox-quick`) for interactive setup, deployment, and
  fleet management across Docker, Proxmox LXC, and Kubernetes.
- **Three MCP servers** (`mcp-core-dev`, `mcp-app-builder`, `mcp-admin`)
  for AI-coding-agent workflows.
- **Ansible-vault-backed secrets** with AES-256-GCM-encrypted vault keys
  and Argon2id key derivation; SSH-piped vault password delivery.
- **OWASP API Security Top 10 test suite** under `tests/security/`.

### Known limitations

- **No independent security audit yet.** The architecture is designed for
  isolation, but the implementation has not been third-party reviewed.
- **`data` worker pulls AGPL / source-available components** (PyMuPDF,
  marker-pdf, surya-ocr) when the optional Marker / advanced-PDF path is
  enabled. The project source is MIT, but operators redistributing a
  Docker image with these components must comply with their licenses.
  See `NOTICE`. They can be disabled via `MARKER_ENABLED=false` and
  `COLPALI_ENABLED=false`.
- **Bundled service images carry their own licenses.** The default
  compose file uses Redis 7 (SSPL/RSALv2), MinIO (AGPL-3.0), and Neo4j
  Community (GPL-3.0). These do not affect the Busibox source license,
  but they affect redistribution of a bundled image. Tracked for a future
  release.
- **First-run defaults are insecure.** `env.local.example` contains
  placeholder credentials (`devpassword`, `local-master-key-change-in-production`,
  etc.) suitable only for local evaluation. Production deployments must
  rotate these — the CLI does this automatically via the Ansible vault.
- **Single-tenant by design.** Busibox isolates users within an
  installation, not organisations. Multi-org tenancy is not on the 0.1
  roadmap.
- **Schema and API churn expected.** Until 1.0, expect breaking changes
  across minor versions.

### Removed

- Nothing — this is the first public release.

### Security

- Reporting process documented in `SECURITY.md`.
- Default `.env` values are clearly marked as insecure and intended for
  local evaluation only.

[Unreleased]: https://github.com/jazzmind/busibox/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jazzmind/busibox/releases/tag/v0.1.0
