---
title: "Local Models Add-on Pack (design)"
category: "administrator"
order: 12
description: "Design for an optional add-on pack that adds local inference (Ollama / vLLM / MLX) to a Busibox install. Status: proposed."
published: true
---

# Local Models Add-on Pack — Design

> **Status:** proposed for v0.2.x. Not yet implemented as a single command.
> The underlying backends (vLLM, Ollama, MLX) already exist in the
> repository today; this document describes how we intend to package them
> as a coherent, opt-in "add-on pack" so that the **default** Busibox
> install does not require a GPU.

---

## Why an add-on pack?

The default Busibox first-run path (see [QUICKSTART.md](../../QUICKSTART.md))
uses LiteLLM with a cloud provider key (OpenAI / Anthropic / AWS Bedrock).
This is deliberate:

- **Faster evaluation.** No GPU drivers, model downloads, or
  quantisation tradeoffs to worry about before you've decided whether
  Busibox does what you need.
- **Predictable hardware story.** The Docker stack runs on any laptop;
  local inference adds real hardware constraints (CUDA driver versions,
  Apple Silicon minimums, RAM headroom for KV cache).
- **Cleaner OSS bundle.** Local-inference toolchains pull in larger
  images and, in some cases, model weights with non-permissive licenses.
  Keeping them out of the default install means the default install is
  easier to redistribute.

Users who need air-gapped operation or want to avoid per-token cloud
costs add the local-models pack on top of a working Busibox install.

---

## Goals

1. **One-command install.** `busibox addon install local-models` (or
   `make addon-local-models`) brings up the appropriate backend for the
   detected hardware and registers it with LiteLLM.
2. **No application changes.** Apps continue to call LiteLLM by name;
   the pack is invisible to consumers of the Agent API.
3. **Hardware-aware defaults.** The pack auto-selects vLLM on NVIDIA,
   MLX on Apple Silicon, and Ollama as the universal fallback.
4. **Reversible.** `busibox addon remove local-models` cleanly tears
   down containers, leaves model files behind by default, and offers
   `--purge` to delete them.
5. **Coexists with cloud.** Users can mix-and-match — e.g. local model
   for embeddings and routine extraction, cloud model for the agent's
   reasoning step.

---

## Proposed profiles

LiteLLM is the single point of routing. The pack contributes one or more
*profiles* to `litellm-config.yaml`. Each profile is a named model that
agents can target.

| Profile name           | Backend     | Hardware                    | Default model                     | Use case |
|------------------------|-------------|-----------------------------|------------------------------------|----------|
| `local-fast`           | Ollama      | CPU or any GPU              | `qwen2.5:7b-instruct`             | Quick extraction, classification |
| `local-quality`        | vLLM        | NVIDIA (24 GB+)             | `Qwen/Qwen2.5-32B-Instruct-AWQ`   | Reasoning, RAG synthesis |
| `local-mlx`            | MLX         | Apple Silicon (32 GB+)      | `mlx-community/Qwen2.5-32B-Instruct-4bit` | Mac Studio / M-series |
| `local-embed`          | FastEmbed   | CPU                          | `BAAI/bge-large-en-v1.5`          | Already shipped — listed for reference |
| `local-vision`         | vLLM        | NVIDIA (40 GB+)             | `Qwen/Qwen2.5-VL-7B-Instruct`     | Optional: ColPali / vision |
| `cloud-openai`         | LiteLLM     | n/a                          | `gpt-4o-mini`                     | Already shipped — for reference |
| `cloud-anthropic`      | LiteLLM     | n/a                          | `claude-3-5-haiku-latest`         | Already shipped — for reference |
| `cloud-bedrock`        | LiteLLM     | n/a                          | `bedrock/us.anthropic.claude-3-5-haiku-v1:0` | Already shipped — for reference |

The `cloud-*` rows above already work today; they're listed to make the
routing story explicit. The add-on pack contributes the `local-*` rows.

### Routing recommendations

Agents declare the profile they want, not the underlying model. Sensible
defaults out of the pack:

- **Agent reasoning** → `local-quality` if available, fall back to
  `cloud-anthropic`
- **Schema extraction** → `local-fast`
- **Embeddings** → `local-embed` (already default)
- **Vision** → `local-vision` if `COLPALI_ENABLED=true`, otherwise
  cloud

Per-agent overrides remain available through the existing agent config.

---

## Install UX

```bash
# From a working Busibox install
busibox addon list                       # discover available add-ons
busibox addon install local-models       # detects hardware, prompts to confirm
busibox addon install local-models \
    --backend vllm \
    --model Qwen/Qwen2.5-32B-Instruct-AWQ
busibox addon status local-models        # shows what's registered
busibox addon remove local-models        # tear down (keeps model files)
busibox addon remove local-models --purge
```

What `install` does:

1. **Hardware detection.** Reuse `busibox-providers::GpuProvider` to
   identify NVIDIA / Apple Silicon / CPU-only.
2. **Backend selection.** Pick vLLM, MLX, or Ollama based on hardware
   and any `--backend` override.
3. **Model selection.** Offer a small curated list per backend; a
   `--model` flag lets advanced users override.
4. **Disk-space check.** Refuse to start if free space is below the
   selected model size + 20% headroom.
5. **Container / service start.** For Docker installs: bring up an
   additional compose profile (`docker compose --profile local-models
   up -d`). For Proxmox: deploy a new role into the existing LLM
   container. For K8s: apply a Helm chart.
6. **LiteLLM registration.** Append the profile(s) to
   `litellm-config.yaml`, reload LiteLLM.
7. **Smoke test.** Send a 1-token completion through the new profile;
   refuse to mark the install successful otherwise.

For the Docker case, the add-on is implementable today as
`docker-compose.local-models.yml` overlay activated via a make target.

---

## Hardware caveats

- **NVIDIA / vLLM.** Requires CUDA 12.x driver compatible with the
  selected vLLM build. Multi-GPU works but tensor-parallel sizing
  requires manual tuning. KV-cache requirements grow with sequence
  length — long-context models can OOM on 24 GB cards.
- **Apple Silicon / MLX.** Requires an M-series Mac with at least
  32 GB unified memory for 32B-class models at 4-bit. Throughput is
  good for single-user; not a substitute for a GPU in a multi-user
  deployment.
- **CPU / Ollama.** Works everywhere but slow on anything bigger than
  ~7B. Best for embedding work, classification, and dev environments.
- **Disk.** Plan for 5–80 GB per model depending on size and
  quantisation. Default model files live under
  `/var/lib/busibox/models` (configurable).
- **Networking.** First model pull is bandwidth-heavy. The pack
  supports a pre-staged model cache so air-gapped installs can copy
  model files in manually.

---

## Acceptance criteria

The pack is "done" for a 0.2.x release when:

1. `busibox addon install local-models` succeeds end-to-end on **all
   four** of: Linux + NVIDIA, macOS + Apple Silicon, Linux + CPU-only,
   and a fresh Proxmox LXC profile.
2. After install, an agent configured to use `local-quality` (or the
   chosen backend's profile) returns an answer to a known RAG query
   that matches a baseline answer from `cloud-anthropic` within an
   evaluator tolerance.
3. `busibox addon status local-models` reports correct backend, model,
   GPU utilisation, and last-call latency.
4. `busibox addon remove local-models` leaves the rest of the platform
   functional and removes the LiteLLM profile entries.
5. The default Docker install (no add-on, cloud key only) **continues
   to work** without any local-inference dependencies on disk or in
   the running image set.
6. NOTICE/CHANGELOG updated with any new licenses pulled in by the
   pack (e.g. model weights with non-commercial clauses must be opt-in
   only and clearly flagged).

---

## Open questions

- **Model weight licensing.** Several strong open-weight models
  (Llama, Gemma, some Qwen variants) ship under custom licenses with
  acceptable-use clauses. We will not pre-select any weight whose
  license is non-permissive without an explicit `--accept-license`
  flag and a one-line summary at install time.
- **Model registry.** Curate inside the repo, or fetch a JSON catalog
  at runtime so we can add models without a new release? Lean toward
  the former for v0.2.x to keep the supply chain simple.
- **Multi-tenant routing.** Today, a single LiteLLM is shared across
  agents. Per-tenant rate limits and budgets exist. Per-tenant
  *backends* (e.g. one tenant's traffic goes to local, another's to
  Bedrock) is out of scope for v0.2.x.

---

## Tracking

- This design lives in [docs/administrators/local-models-addon.md](./local-models-addon.md).
- Implementation work will be tracked under the v0.2 milestone.
- Until the pack ships, the existing per-service knobs
  (`VLLM_BASE_URL`, `MARKER_USE_GPU`, etc. in `env.local.example`)
  remain the supported way to enable local inference manually.
