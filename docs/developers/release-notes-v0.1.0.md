---
title: "Release notes — v0.1.0 (draft)"
category: "developer"
order: 100
description: "Draft release notes for the first public Busibox release. Source for the GitHub release body."
published: false
---

# Busibox v0.1.0 (draft)

**The first public, MIT-licensed release of Busibox.** This is an
early-stage preview suitable for evaluation, lab use, and design-partner
pilots — not a production-ready 1.0.

If you're new to Busibox, start with the
**[Quickstart](https://github.com/jazzmind/busibox/blob/main/QUICKSTART.md)**:
clone the repo, drop an OpenAI / Anthropic / Bedrock key into
`.env.local`, run `make docker-up`, and you've got a working stack on
your laptop in ~15 minutes. No GPU, no model downloads, no Proxmox
required for a first run.

## Highlights

- 🏠 **Self-hosted document + agent platform.** Upload files, hybrid
  search across them, and run AI agents that cite their sources — all
  on your own hardware.
- 🔐 **Zero-Trust auth from day one.** RS256 JWTs verified via JWKS,
  per-service audience-scoped subject token exchange, no shared
  service-to-service secrets.
- 🛡️ **PostgreSQL Row-Level Security** end-to-end. Even an application
  bug can't return rows the user shouldn't see.
- 🤖 **Hybrid LLM routing** through LiteLLM: cloud (OpenAI / Anthropic /
  Bedrock) by default, with vLLM / MLX / Ollama available as opt-in
  local backends.
- 🧰 **Three MCP servers** for AI coding agents (Cursor / Claude Code):
  `mcp-core-dev`, `mcp-app-builder`, `mcp-admin`.
- 🦀 **Busibox CLI** — a Rust workspace that drives multi-host
  deployment across Docker, Proxmox LXC, and Kubernetes.

See the full list in
[CHANGELOG.md](https://github.com/jazzmind/busibox/blob/main/CHANGELOG.md).

## Known limitations

- No independent security audit yet.
- The optional Marker / advanced-PDF processing path pulls AGPL /
  source-available components (PyMuPDF, marker-pdf, surya-ocr); the
  Busibox source is MIT but operators redistributing a Docker image
  with these enabled must comply with their licenses. Disable with
  `MARKER_ENABLED=false` and `COLPALI_ENABLED=false`. See
  [NOTICE](https://github.com/jazzmind/busibox/blob/main/NOTICE).
- Bundled service images (Redis 7, MinIO, Neo4j Community) carry their
  own non-MIT licenses; this affects redistribution of a bundled
  image, not the Busibox source.
- Single-tenant by design — multi-org tenancy is not on the 0.1
  roadmap.
- Schema and API churn expected before 1.0.

## Reporting issues

- Bugs / features → GitHub Issues with the supplied templates.
- Security → see
  [SECURITY.md](https://github.com/jazzmind/busibox/blob/main/SECURITY.md);
  please report privately, not in a public issue.

## Thanks

To the early users running Busibox on Proxmox clusters, Mac Studios,
and Kubernetes spot fleets — your bug reports and "this would be
nicer if…" feedback shaped this release.
