---
title: Roadmap
category: developers
order: 0
description: Development roadmap and priorities for Busibox
published: true
---

# Busibox Roadmap

Priorities are rated 1 (highest) to 6 (lowest). Items marked with checkboxes are trackable sub-tasks.

---

## P1 - Data Processing & Ingestion

The ingestion pipeline is core to the platform. These items improve reliability, intelligence, and coverage of document processing.

### Ingestion Fixes
- [ ] Fix document tagging — tags are not being applied properly during ingestion
- [ ] Skip redundant conversions (e.g. markdown files should not be re-converted to markdown)
- [ ] Long documents — split into sections before embedding
- [ ] Trigger re-ingestion when a document is moved into a new folder

### Schema-Driven Processing
- [ ] Tag schema fields with processing directives: `index`, `embed`, `graph`
- [ ] Only run entity extraction when a schema is associated with the document type
- [ ] Auto-schema generation should pre-tag fields with appropriate directives

### Folder & Classification
- [ ] Folders carry sensitivity classification (e.g. "local LLM only")
- [ ] Folder-level tags — documents matching folder criteria can be auto-moved or prompted
- [ ] Upload all files to personal library first, then prompt via chat to move to shared libraries

### New Data Types
- [ ] Tabular data ingestion
- [ ] Visual-heavy documents — evaluate ColPali for diagram/chart-heavy PDFs
- [ ] Investigate Outline as a collaborative document source

---

## P1 - Chat UX

### Thinking Indicators
- [ ] Consistent behavior between FullChat and SimpleChat: thinking toggle opens immediately when the dispatcher is processing, closes (but remains visible) when streaming begins
- [ ] Preserve thinking history with the message so it renders on conversation reload
- [ ] Fix FullChat: toggle currently appears late, is closed by default, and disappears after response completes

### Multi-Step Response Streaming
- [ ] For complex queries (e.g. cross-referencing docs against the web), stream intermediate progress messages rather than going silent during long processing
- [ ] Pattern: "Found relevant documents... summarizing" -> "Summary of docs... searching online" -> "Web results... combining" -> final response

### Search Strategy
- [ ] Chat agent should check document search first, retrieve and evaluate highly relevant docs before resorting to web search
- [ ] Web search should scrape and evaluate results for relevance, not just return links

### Hybrid RAG (Graph + Vector)
- [x] **Tier 1** (implemented): Graph context expansion in agent search, entity type normalization, richer graph context output
- [ ] **Tier 2**: Query-side entity extraction, entity embeddings in Neo4j, graph-informed reranking. See [Hybrid RAG Design](hybrid-rag-design.md)
- [ ] **Tier 3**: Unified search orchestrator, classification-driven retrieval routing, entity-centric search mode. See [Hybrid RAG Design](hybrid-rag-design.md)

---

## P2 - Installation & Onboarding

### Minimum Requirements
- Currently: Apple Silicon M4 (24 GB) or NVIDIA 3090 (24 GB VRAM)
- Document clearly and validate at install time

### Installer Improvements
- [ ] Dependency checker — validate Docker, Python, and other prerequisites; guide user through installation
- [ ] Consider a dedicated install container that bootstraps the environment
- [ ] Investigate a compiled installer/manager CLI (e.g. Rust) for better UX
- [ ] Prevent component timeouts during initial deployment

### Installation Modes
- **Basic mode**: Use-case-optimized defaults — selects models, memory allocation, and hot-reload settings based on profile:
  - Production deploy
  - App development (hot reload on user-apps only)
  - Core system development (hot reload on all services)
- **Advanced mode**: Manual selection of models, vector DB engine, and optional components

---

## P2 - Bridge Channels

The bridge service already supports Signal, Telegram, Discord, WhatsApp, and email. These items improve output quality and interaction capability.

- [ ] Channel-specific message formatting (Telegram markdown, WhatsApp formatting, SMS length limits)
- [ ] Reply-to-email support (inbound email -> agent response -> outbound reply)
- [ ] Interactive elements with text fallback for channels that don't support rich UI

---

## P2 - App Builder

- [ ] AI-assisted app development using Claude Code: build, deploy, and iterate on apps within `user-apps`
- [ ] Browser-use and log access for automated testing during development
- [ ] Apps can be published to GitHub or kept as private deployments

---

## P3 - Dispatcher & Routing

- [ ] Improve routing accuracy — better matching of user intent to appropriate agents and tools
- [ ] Model-capability profiles — tune dispatcher behavior based on which LLM is handling the request

### Interactive Chat Components
- [ ] Create interactive UI components for common chat interactions: folder selectors, yes/no confirmations, option lists, action buttons
- [ ] Dispatcher can recommend tool activation and present it as a clickable choice (e.g. "Should I create an agent task for this? Yes / No")
- [ ] Bridge services declare which interactive components they can render; channels that can't render rich UI get a text fallback
- [ ] Chat agent should auto-create agent tasks when appropriate (e.g. "send me a videogame news summary via email every hour" should route to the news agent and create a recurring task)

---

## P3 - Feedback & Learning

- [ ] User feedback improves assistant behavior dynamically via an insights system
- [ ] Insights can include tool-use suggestions (e.g. "users asking about X tend to want tool Y")

---

## P4 - Scraper Tool

- [ ] Convert all scraped HTML to markdown using [Cloudflare's approach](https://blog.cloudflare.com/markdown-for-agents/) before downstream processing

---

## P5 - AI Model Improvements

### Frontier Model Fallback
- [ ] Automatic fallback to cloud/frontier models for long-context queries
- [ ] Fallback for vision tasks when local models lack multimodal capabilities
- [ ] Respect document sensitivity classification when routing to external models

---

## P5 - Security

- [ ] Run security scanners across the codebase (e.g. Claude Code security audit)
- [ ] Add a security validation section to the test suite that interactively proves the data security model (RLS, RBAC, token isolation)

---

## P6 - Voice Agent

Voice agent service exists (`srv/voice-agent`) with speech synthesis and transcription. Needs further development for production readiness.

---

## P6 - App Library & Marketplace

- [ ] Integrate security scanners for user-submitted apps (e.g. dependency audit, code analysis)

---

## Add-on Apps & Agents

These are standalone applications built on the Busibox platform. Some are in active development, others are planned.

| App | Status | Description |
|-----|--------|-------------|
| **Project Manager** | In development (`busibox-projects`) | Track AI initiatives with intelligent status updates via conversational agents |
| **Recruiter** | In development (`busibox-recruiter`) | Recruitment campaigns with candidate tracking, interview prep, and analytics |
| **Data Analysis** | Planned | Interactive data analysis with local LLM support, report views, and visualizations |
| **Paralegal** | Planned | Contract review (flag issues, compare against reference contracts, draft new contracts) |
| **Marketer** | Planned | Research successful posts on relevant topics/platforms, generate optimized social media content (Substack, LinkedIn) |
| **Compliance** | Planned | Compliance monitoring and reporting |
| **Researcher** | Planned | NotebookLM-style research assistant with deep document analysis |
