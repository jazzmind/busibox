# Busibox Quickstart

> **The fastest path to a running Busibox is Docker + a cloud LLM API key.**
> No GPU, no model downloads, no Proxmox needed for a first run.

This guide gets you to a working Portal at `http://localhost:3000` in
~15 minutes. For multi-host (Proxmox / Kubernetes / fleet) deployments, see
[docs/administrators/01-quickstart.md](docs/administrators/01-quickstart.md).

---

## 1. Prerequisites

- **Docker + Docker Compose** (Docker Desktop 4.20+ on macOS / Windows, or
  Docker Engine 24+ on Linux)
- **~16 GB free disk** for images and volumes
- **One LLM provider key**, either:
  - OpenAI: `OPENAI_API_KEY`
  - Anthropic: `ANTHROPIC_API_KEY`
  - AWS Bedrock: `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_REGION_NAME`
- A **GitHub personal access token** with `read:packages` scope, used to
  install the `@jazzmind/busibox-app` shared library
  ([create one here](https://github.com/settings/tokens))

You do **not** need an NVIDIA GPU, an Apple Silicon Mac, vLLM, MLX, or
Ollama for a first run.

---

## 2. Clone and configure

```bash
git clone https://github.com/jazzmind/busibox.git
cd busibox

cp env.local.example .env.local
```

Open `.env.local` and set, at minimum:

```bash
# One of these — whichever provider you have a key for
OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
# AWS_ACCESS_KEY_ID=...
# AWS_SECRET_ACCESS_KEY=...
# AWS_REGION_NAME=us-east-1

# Required to install the @jazzmind/busibox-app library at build time
GITHUB_AUTH_TOKEN=ghp_...

# Optional — first user to sign up with this email becomes admin
ADMIN_EMAIL=admin@localhost
```

The defaults for `POSTGRES_PASSWORD`, `MINIO_SECRET_KEY`,
`AUTHZ_MASTER_KEY`, `BETTER_AUTH_SECRET`, etc. in `env.local.example` are
**only suitable for local evaluation**. Rotate them before exposing the
stack to anyone but yourself. The Busibox CLI does this automatically for
non-local profiles.

---

## 3. Start the stack

```bash
make docker-up
```

This pulls and starts:

- **Infrastructure**: PostgreSQL, Redis, MinIO, Milvus, Neo4j
- **APIs**: AuthZ, Data, Search, Agent, Docs, Embedding, Deploy
- **LLM gateway**: LiteLLM (configured with whichever provider key you set)
- **Frontend**: nginx + the `core-apps` container running the Portal and
  Agents apps

First boot takes 5–15 minutes depending on your network and CPU.

When `docker compose ps` shows everything `healthy`, open
**<http://localhost:3000>** and sign up. The first account that matches
`ADMIN_EMAIL` (or the first account at all if `ADMIN_EMAIL` is unset) is
granted admin.

---

## 4. Verify

```bash
# Watch service health
docker compose ps

# Tail a service log
docker compose logs -f authz-api

# Hit a health endpoint
curl -s http://localhost:8001/health   # AuthZ
curl -s http://localhost:8000/health   # Agent
```

Try the platform end-to-end:

1. Sign up at <http://localhost:3000>.
2. Upload a PDF or text file via the Portal.
3. Wait for ingestion (the Data API extracts, chunks, and embeds it).
4. Ask the Agent a question that should be answered from the file.
5. Confirm citations point back to your document.

---

## 5. Bring your own model (optional)

The first-run path uses your cloud API key through LiteLLM, which means
**Busibox itself is fully local but inference happens at your provider**.
For air-gapped or fully on-prem deployments, install the local-model
add-on pack — it adds Ollama / vLLM / MLX backends behind the same
LiteLLM gateway, so your application code does not change.

See [docs/administrators/local-models-addon.md](docs/administrators/local-models-addon.md)
for the design and supported hardware.

---

## 6. Going further

| You want to… | Go to |
|---|---|
| Configure SSO, custom domain, TLS | `docs/administrators/03-configure.md` |
| Deploy to Proxmox LXC | `docs/administrators/02-install.md` and the `busibox` CLI |
| Deploy to Kubernetes | `docs/administrators/11-kubernetes.md` |
| Build a custom app on Busibox | `docs/administrators/04-apps.md` |
| Add a new bridge channel (Telegram, Slack, …) | `docs/administrators/10-bridge-api-integrations.md` |
| Run the security test suite | `make test-security` (see `tests/security/`) |
| Contribute | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Report a vulnerability | [SECURITY.md](SECURITY.md) |

---

## Troubleshooting

**A service is stuck in `starting`.** First boots are slow because the
PostgreSQL schema, Milvus collections, and Neo4j constraints are all
being created. Give it 5–15 minutes; then check `docker compose logs <svc>`.

**`@jazzmind/busibox-app` install fails.** You need a GitHub PAT with
`read:packages` scope set as `GITHUB_AUTH_TOKEN`. The default
`ghp_your_github_token` placeholder will fail with a 401.

**Agent returns "no LLM provider configured".** Confirm one of
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or the AWS Bedrock variables is set
in `.env.local`, then `make docker-up` again to apply.

**"password authentication failed" against Postgres.** You changed
`POSTGRES_PASSWORD` after the volume was created. Either revert the
password or run `docker compose down -v` to wipe state and start fresh
(this **deletes** uploaded documents and search indexes).

For more, see `docs/administrators/08-troubleshooting.md`.

---

**Version**: 0.1.0
