---
title: "Platform Capabilities"
category: "overview"
order: 4
description: "Document processing, hybrid search, AI agents, chat, and the application ecosystem"
published: true
---

# Platform Capabilities

[Back to Why Busibox](00-why-busibox.md)

Busibox is not a single tool — it's a platform that integrates document processing, search, AI agents, and custom applications into a unified system. This page describes what the platform can do and how the pieces fit together.

## Document Processing

Upload documents and Busibox handles the rest. The ingestion pipeline converts raw files into searchable, AI-ready content through a series of automated steps:

1. **Upload**: Files are stored in MinIO (S3-compatible object storage). Deduplication by SHA-256 hash prevents redundant processing.
2. **Text Extraction**: Multi-strategy extraction handles different document types — Marker for PDFs, standard parsers for Office documents, OCR fallback for scanned content. An LLM cleanup pass corrects extraction artifacts.
3. **Semantic Chunking**: Documents are split into semantically coherent chunks (400-800 tokens with overlap) that preserve context boundaries rather than cutting at arbitrary character counts.
4. **Embedding**: Each chunk is embedded using FastEmbed (dense vectors) with optional ColPali visual embeddings for diagram-heavy documents. BM25 sparse terms are generated in parallel for keyword matching.
5. **Indexing**: Vectors are stored in Milvus with partition keys tied to the document's visibility settings (personal or shared by role). Metadata goes to PostgreSQL with RLS policies.

**Supported formats**: PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), images (with OCR), Markdown, plain text, and more.

The pipeline runs asynchronously — users can continue working while documents are processed. Real-time status updates stream via server-sent events so users can track progress.

### Schema-Driven Extraction

Beyond raw text, Busibox can extract structured data from documents using schemas. When a document type has an associated schema, the platform uses AI to extract specific fields — dates, names, amounts, categories — into structured records that can be queried, filtered, and visualized. Schemas can be created manually or auto-generated from sample documents.

## Hybrid Search

Search in Busibox combines multiple retrieval strategies to find the most relevant results:

- **Vector search** (semantic): Find content by meaning, even when the query uses different words than the document. Powered by Milvus with dense embeddings.
- **BM25 search** (keyword): Traditional keyword matching for queries where exact terms matter — part numbers, names, codes.
- **Graph-based retrieval**: Entity relationships extracted during ingestion enable graph-aware context expansion, connecting related concepts across documents.
- **LLM reranking**: After initial retrieval, an LLM reranker scores results for relevance, pushing the most useful content to the top.

All search is permission-aware. The search service reads the user's JWT, builds a list of accessible partitions (personal + role-based), and restricts retrieval to those partitions. Users never see results from documents they don't have access to.

## AI Agents

Agents are the conversational interface to the platform. They combine LLM reasoning with tools that access your data and the web:

### Core Agent Capabilities

- **Document search (RAG)**: Agents search your documents using the hybrid search pipeline, retrieve relevant passages, and synthesize answers with citations pointing back to source documents.
- **Web search**: When documents don't have the answer, agents can search the web, scrape relevant pages, and incorporate external information.
- **File attachments**: Upload files directly in chat. Agents analyze attached documents in context.
- **Conversation memory**: Agents remember the conversation history and can build on previous exchanges.

### Agent Architecture

The Agent API orchestrates requests through specialized sub-agents:

- A **dispatch agent** routes incoming queries to the appropriate specialist
- A **RAG search agent** handles document-grounded questions
- A **web search agent** handles external information needs
- An **attachment agent** processes uploaded files

Agents inherit the requesting user's permissions through token exchange. An agent serving a user with access to HR documents can search those documents; the same agent serving a user without HR access cannot.

### Custom Agents

Administrators can define custom agents with specific system prompts, tool configurations, and model assignments. Examples include:

- A **news agent** that scrapes specified sources on a schedule and synthesizes summaries into stored documents
- A **status update agent** that guides users through structured project updates
- A **research assistant** with access to specific document collections and web search

Agents can be assigned to specific roles, so different teams see different agents tailored to their workflows.

### Guardrails and Cost Controls

Agents and workflows operate under configurable guardrails that prevent runaway execution and enforce cost ceilings:

- **Request limits** — cap the number of LLM calls a workflow can make
- **Token limits** — set a maximum total token budget across all requests
- **Tool call limits** — restrict how many tool invocations can occur
- **Cost ceilings** — set a hard dollar limit based on model pricing
- **Timeouts** — enforce wall-clock time limits

These guardrails are defined per workflow and enforced in real-time by the workflow engine. When a limit is hit, execution halts cleanly and the reason is recorded. This makes it safe to run autonomous multi-step agents — they cannot consume unbounded resources.

Agent tiers provide an additional layer of control: `simple` (30-second timeout), `complex` (5-minute timeout), and `batch` (30-minute timeout) set appropriate resource boundaries for different task types.

## Chat and Messaging

Users interact with agents through multiple channels:

- **Web interface**: Full-featured chat in the AI Portal with thinking indicators, streaming responses, citations, file attachments, and conversation history.
- **Bridge channels**: The Bridge service connects agents to external messaging platforms. Users can ask questions and receive AI-powered responses in the tools they already use.

### Supported Messaging Channels

| Channel | Capabilities |
|---------|-------------|
| **Telegram** | Text, markdown formatting, group chats |
| **Signal** | Encrypted messaging |
| **Discord** | Text channels, rich embeds |
| **WhatsApp** | WhatsApp Cloud API, text formatting |
| **Email** | Inbound IMAP polling, outbound replies |

Bridge channels adapt formatting to each platform's capabilities with text fallbacks for channels that don't support rich content. User identity is bound across channels via `channelUserBindings` so the same person gets consistent access and conversation history regardless of which channel they use.

Bridge configuration is managed through the Busibox Portal admin interface (**Admin > Settings > Bridge**).

## Application Ecosystem

Busibox is a platform for building and running applications, not just a standalone tool.

### Core Applications

Every Busibox installation includes:

- **AI Portal**: The main dashboard — document management, search, settings, user administration
- **Agent Manager**: Configure agents, manage conversations, define workflows and scheduled tasks

### Custom Applications

Developers build applications on Busibox using:

- **Next.js** with the `@jazzmind/busibox-app` library, which provides authentication, API clients, theming, and UI components
- **Busibox SSO**: Apps authenticate through the same Zero Trust token exchange — no custom auth implementation needed
- **Data API access**: Apps store and retrieve structured data through the Data API with automatic RLS enforcement
- **AI integration**: Apps can invoke agents, run searches, and call LLMs through standard API endpoints

Applications deploy to the platform and immediately inherit security, data access, and AI capabilities. A developer building a project management app, for example, doesn't need to implement authentication, document search, or AI chat — those are platform services.

### App Builder

An AI-assisted app builder (in development) enables rapid app creation through conversational development. Describe what you need, and the builder generates, deploys, and iterates on applications within the platform — with browser-based testing and log access during development.

### App Library

Applications can be published to a shared library for installation by other Busibox deployments, or kept as private deployments for a single organization.

## Infrastructure and Operations

Busibox includes the operational tooling needed to run in production:

- **Ansible IaC**: Infrastructure defined as code with Ansible roles for every service. Deployments are repeatable and auditable.
- **Unified `make` interface**: A single command interface for deploying, managing, restarting, and monitoring all services. Secrets from Ansible Vault are automatically injected.
- **Multi-environment support**: Run staging and production on the same or separate hosts with isolated configurations.
- **Health monitoring**: Container health checks, service status dashboards, and log aggregation.
- **Dual backend support**: The same Ansible roles deploy to Docker (for development and smaller deployments) or Proxmox LXC containers (for production).

## Further Reading

- [Why Busibox](00-why-busibox.md) — The case for a self-hosted AI platform
- [Data Sovereignty](01-data-sovereignty.md) — How your data stays under your control
- [Security Architecture](02-security-architecture.md) — Zero Trust auth, RLS, and container isolation
- [Hybrid AI](03-hybrid-ai.md) — Local and cloud model routing
- [Use Cases](05-use-cases.md) — Real-world applications across industries
