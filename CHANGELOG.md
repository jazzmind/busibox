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
