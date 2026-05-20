# AGENTS.md — using Busibox from an automated agent

This file is for coding agents and CI scripts that drive Busibox without a
human at the terminal. It describes the deterministic, non-interactive
entrypoints the Rust CLI exposes and the contracts each one promises.

The `busibox` binary is a single executable that runs as either a CLI or a
TUI depending on how it's invoked:

| Invocation | Mode | Notes |
|---|---|---|
| `busibox <subcommand> ...` | CLI | Scriptable. Exit codes documented below. |
| `busibox` (interactive TTY) | TUI | Full ratatui UI. Not for agents. |
| `busibox` (non-TTY / piped) | CLI help, exits 2 | Will not hang. |

Agents should always pass an explicit subcommand. Never invoke the bare
`busibox` from CI.

## Read-only entrypoints (safe to call repeatedly)

These commands do not touch the network, the Ansible vault, or any
container. They are safe for an agent to call before deciding what to do
next.

### `busibox version`

Prints the binary version. Exit `0`.

### `busibox profile list`

Lists the available preset profiles (`lite`, `standard`, `full`) and the
known add-on packs. Packs flagged `[placeholder — not yet wired in this
tree]` deploy nothing. Exit `0`.

### `busibox profile show <name> [--pack <p>]...`

Prints the resolved service list for a profile plus any add-on packs.
Useful for asserting that a profile change actually changed what would
deploy. Exit `0` on success, `2` on unknown profile or pack name.

### `busibox verify [--profile <name>] [--pack <p>]...`

Deterministic, non-destructive sanity check. Verifies:

- the current working directory (or `--root`) looks like a Busibox checkout
- every service named by the profile is recognized by the service registry

Exit `0` on pass, `1` on failure, `2` on bad arguments. Intended for use in
CI smoke tests:

```bash
busibox verify --profile lite || exit 1
```

### `busibox doctor [--profile <name>] [--pack <p>]...`

Quick read-only environment summary: OS, RAM, Docker availability, the
profile's service list, and warnings about anything missing (e.g.
"Docker is not available", "this profile needs a cloud LLM API key").
Exit `0` if Docker is present, `1` if a hard prerequisite is missing.
Warnings do not change the exit code.

## Side-effecting entrypoints

### `busibox up --profile <name> [--pack <p>]...`

Prints the deploy plan and the equivalent `make install SERVICE=...`
invocation for the chosen profile. **As of this PR, `busibox up` does not
actually deploy** — it prints the plan so the agent can decide whether to
proceed. The actual deploy still happens via `make install`. Wiring `up`
to call the deploy backend directly is a follow-up.

### `busibox import <file>`

Imports a profile from a `.busibox-export` bundle. Interactive prompts; not
recommended for agents.

## Recommended agent flow

```bash
# 1. Sanity check
busibox version
busibox verify --profile lite

# 2. Plan
busibox doctor   --profile lite
busibox up      --profile lite

# 3. If doctor reported no errors and the plan looks right, deploy via
#    the make target it printed. This is the only deploy step today.
make install SERVICE=$(busibox up --profile lite | grep '^  make install' | sed 's/.*SERVICE=//')
```

## Profiles

| Profile | Use it when |
|---|---|
| `lite` *(default)* | First-run evaluation. Docker-local, cloud-key. No GPU, no local models, no Milvus, no Neo4j. |
| `standard` | Lite plus local Milvus-backed RAG (embedding + search). Still cloud-LLM. |
| `full` | Everything in the service registry. Equivalent to `make install SERVICE=all`. |

## Add-on packs

| Pack | Status | What it enables |
|---|---|---|
| `local-models` | wired | Local model serving via vLLM. Requires GPU. |
| `graph` | wired | Neo4j graph database. |
| `rag-milvus` | wired | Milvus + embedding + search (same as `standard`). |
| `media` | **placeholder** | Reserved; no services in this tree yet. |
| `fleet` | **placeholder** | Reserved; no services in this tree yet. |
| `rag-qdrant` | **placeholder** | Reserved; no Qdrant backend implemented. |

Placeholder packs are intentionally exposed so the CLI can warn instead of
silently doing nothing if a user (or agent) requests them.

## Exit code summary

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Operation failed (e.g. hard prerequisite missing, verify mismatch). |
| `2` | Bad arguments (unknown profile or pack name, non-TTY without a subcommand). |
