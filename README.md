# Busibox

**A self-hosted AI platform that keeps your data on your infrastructure.**

Busibox integrates document processing, semantic search, AI agents, and custom applications into a single platform — running on Docker or Proxmox LXC containers with enterprise-grade security baked in.

Think of it as a Linux distribution for AI: install it on your hardware, and you get a complete stack for building AI-powered workflows without sending a single byte to the cloud (unless you choose to).

---

## Why Busibox?

**You shouldn't have to choose between powerful AI and data sovereignty.**

Most AI platforms require uploading your documents to third-party servers. Busibox runs entirely on infrastructure you control — a local server, a Proxmox cluster, or Docker on your laptop. Your documents, embeddings, conversations, and search indexes never leave your network.

| Problem | How Busibox Solves It |
|---------|----------------------|
| Sensitive data can't go to cloud AI | Everything runs locally — LLMs, embeddings, vector search |
| AI tools are fragmented | One platform: documents, search, agents, and apps share auth and data |
| Building AI apps is slow | App template + shared library + deploy in minutes, not weeks |
| Access control is an afterthought | Zero Trust auth, RBAC, and PostgreSQL Row-Level Security from day one |
| Infra is painful to manage | Interactive CLI, fleet management across Docker/Proxmox/K8s |

---

## What You Get

### Document Processing
Upload PDFs, Word, Excel, PowerPoint, images (with OCR), or Markdown. Busibox automatically extracts text, chunks it, generates embeddings, and indexes everything for search. Schema-driven extraction pulls structured fields (dates, names, amounts) from unstructured documents.

### Hybrid Search
Natural language queries against your document library. Combines vector search (semantic), BM25 (keyword), graph-based retrieval, and LLM reranking — all filtered by the user's permissions. Ask "What are the budget assumptions for Q3?" instead of guessing keywords.

### AI Agents with Guardrails
Conversational agents that search your documents (RAG), browse the web, accept file attachments, remember context, and stream responses with source citations. Configure agents with custom instructions, tools, and model routing per task. Built-in guardrails enforce request limits, token budgets, cost ceilings, and timeouts — so autonomous agents can't consume unbounded resources.

### Hybrid LLM Routing
LiteLLM gateway routes requests to local models (vLLM on NVIDIA GPUs, MLX on Apple Silicon) or cloud providers (OpenAI, Anthropic, AWS Bedrock) — per agent, per task. Use a fast local model for extraction and a frontier model for complex reasoning, all through one API.

### Custom Applications
Build and deploy Next.js apps that inherit Busibox auth, data access, and AI capabilities. A shared library (`@jazzmind/busibox-app`) provides SSO, data API clients, chat components, and search — so you write domain logic, not plumbing.

### Bridge Channels
Connect agents to Telegram, Signal, Discord, WhatsApp, and email. Users interact with AI in the tools they already use.

### Fleet Management
Manage multiple Busibox installations from a single workstation. The CLI supports deployment profiles for Docker, Proxmox LXC, and Kubernetes backends — self-hosted or cloud. Deploy to staging, test, then promote to production with separate vault keys, SSH configs, and environment settings per profile.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                       Browser                           │
└────────────────────────┬────────────────────────────────┘
                         │
                    ┌────▼────┐
                    │  nginx  │  reverse proxy + SSL
                    └────┬────┘
            ┌────────────┼────────────────┐
            ▼            ▼                ▼
      ┌──────────┐ ┌──────────┐    ┌───────────┐
      │  Portal  │ │  Agents  │    │ User Apps │
      └────┬─────┘ └────┬─────┘    └─────┬─────┘
           └─────────────┼────────────────┘
                         ▼
        ┌────────────────────────────────┐
        │         API Layer              │
        │  AuthZ · Data · Agent · Search │
        │  Docs · Deploy · Embedding     │
        └───────────────┬────────────────┘
                        ▼
        ┌────────────────────────────────┐
        │       Infrastructure           │
        │  PostgreSQL · Milvus · MinIO   │
        │  Redis · LiteLLM · vLLM       │
        └────────────────────────────────┘
```

Each service runs in its own isolated container. Compromise in one container does not grant access to another. All inter-service communication uses audience-scoped RS256 JWTs verified via JWKS — no shared secrets, no static tokens.

---

## Security Model

Busibox is single-tenant and multi-user — each installation serves one organization with strong isolation between users. Security is architecture, not a feature bolted on later.

- **Zero Trust Authentication** — OAuth2-based AuthZ service is the sole token authority. RS256-signed JWTs verified via JWKS; subject token exchange scopes tokens per service. No shared secrets, no static API keys.
- **Passwordless Auth** — Passkeys (biometrics/security keys), TOTP, or magic links. No passwords by design. SSO via EntraID, SAML, and other identity providers.
- **Row-Level Security** — PostgreSQL RLS enforces access at the database level. Even with an application bug, the database won't return unauthorized rows.
- **RBAC Everywhere** — Documents, agents, and apps are assigned to roles. Users see only what their roles permit. Agents inherit the calling user's permissions.
- **Envelope Encryption** — Files encrypted at rest with Master Key → Key Encryption Keys → Data Encryption Keys, per file. TLS for all inter-service communication.
- **Audit Trail** — Auth events, token exchanges, and admin actions logged with timestamps, user IDs, and IP addresses.
- **Built-in Security Testing** — OWASP API Security Top 10 test suite covering auth bypass, injection, fuzzing, and endpoint coverage. Run with the CLI or `make test-security`.

---

## Who It's For

- **Enterprise teams** that need AI but can't send sensitive data to third parties
- **Consultancies** building AI solutions for clients on shared infrastructure
- **Regulated industries** (legal, finance, healthcare, government) with data residency and audit requirements
- **AI-native organizations** that want control without stitching together a dozen tools

---

## Quick Start

The **Busibox CLI** is an interactive terminal UI that walks you through setup, deployment, and ongoing management. It handles SSH connectivity, encrypted vault passwords, model selection, and service health — all from one interface.

```bash
cd cli/busibox
cargo build --release
./target/release/busibox
```

The CLI guides you through:

1. **Profile setup** — configure Docker, Proxmox, or Kubernetes targets
2. **Hardware profiling** — detect GPUs and available resources
3. **Model selection** — choose and download AI models for your hardware
4. **Deployment** — install all services with proper secrets injection
5. **Management** — restart, monitor, redeploy, and view logs

Manage multiple Busibox installations (Docker, Proxmox, Kubernetes — self-hosted or cloud) from a single workstation through deployment profiles.

See [docs/administrators/](docs/administrators/) for full deployment and configuration guides.

---

## For Developers

### Build Apps on Busibox

The app template and `@jazzmind/busibox-app` library give you:

- **SSO out of the box** — `SessionProvider` handles auth, token refresh, and 401 retry
- **Data API client** — structured CRUD with automatic RLS enforcement
- **Chat components** — `SimpleChatInterface` for agent-powered UIs
- **Search client** — hybrid search with permission filtering
- **Deploy in one command** — deploy from the CLI or via MCP

```bash
# Start from the template
git clone <template-repo> my-app
cd my-app && npm install && npm run dev
```

Apps are cloned and built at runtime — code changes deploy without rebuilding containers.

### MCP Servers for AI Agents

Three MCP servers provide structured access for AI coding agents (Cursor, Claude Code):

| Server | For | What It Does |
|--------|-----|-------------|
| `mcp-core-dev` | Core developers | Docs, scripts, testing, container logs |
| `mcp-app-builder` | App developers | Auth patterns, template reference, service endpoints |
| `mcp-admin` | Operators / agents | Deployment, SSH, container management (handles vault auth) |

AI agents should use MCP tools rather than `make` targets directly. The `make` interface is an internal implementation detail used by the CLI and MCP servers — it requires vault password setup that the CLI handles automatically.

```bash
make mcp   # Build all servers and write Cursor config
```

---

## Technology Stack

| Layer | Components |
|-------|-----------|
| **Compute** | Proxmox VE (LXC) or Docker |
| **Provisioning** | Ansible, Bash |
| **APIs** | FastAPI (Python 3.11+) |
| **Apps** | Next.js 16, React 19, TypeScript 5 |
| **Database** | PostgreSQL 15+ with RLS |
| **Vector Search** | Milvus 2.3+ |
| **Object Storage** | MinIO (S3-compatible) |
| **Queue** | Redis Streams |
| **LLM Gateway** | LiteLLM → vLLM, Ollama, OpenAI, Anthropic, Bedrock |
| **Reverse Proxy** | nginx with SSL |
| **Auth** | RS256 JWTs, JWKS, Zero Trust token exchange |

---

## Documentation

| Audience | Location | Content |
|----------|----------|---------|
| **Administrators** | [docs/administrators/](docs/administrators/) | Deployment, configuration, troubleshooting |
| **Developers** | [docs/developers/](docs/developers/) | Architecture, APIs, security, app development |
| **Users** | [docs/users/](docs/users/) | Feature guides, document management, chat, search |

---

## Project Structure

```
busibox/                        # This repo — infrastructure, APIs, provisioning
├── docs/                       #   Documentation (by audience)
├── srv/                        #   Service source code
│   ├── agent/                  #     Agent API (FastAPI)
│   ├── data/                   #     Data API + Ingest Worker
│   ├── docs/                   #     Docs API
│   └── deploy/                 #     Deploy API
├── provision/
│   ├── ansible/                #     Ansible roles and inventory
│   └── pct/                    #     Proxmox container scripts
├── scripts/                    #   Admin workstation scripts
├── tools/                      #   MCP servers and utilities
└── specs/                      #   Project specifications
```

**Related repositories:**

| Repo | What It Contains |
|------|-----------------|
| [busibox-frontend](https://github.com/jazzmind/busibox-frontend) | All frontend apps (Portal, Agents, Admin, Chat, App Builder, Media, Documents) and the `@jazzmind/busibox-app` shared library |
| [busibox-template](https://github.com/jazzmind/busibox-template) | Starter template for building new apps on the Busibox platform |

---

## Contributing

1. Read the architecture docs in [docs/developers/architecture/](docs/developers/architecture/)
2. Set up a local development environment with Docker: `make install SERVICE=all`
3. Run tests: `make test-docker SERVICE=<service>`
4. Follow the organization rules in `.cursor/rules/` for file placement and naming

See [CLAUDE.md](CLAUDE.md) for detailed development workflow and conventions.
