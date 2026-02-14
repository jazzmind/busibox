---
title: "GPU Burst Windows"
category: "developer"
order: 21
description: "Dynamic GPU provisioning on Rackspace Spot for time-sensitive AI workloads"
published: true
---

# GPU Burst Window Architecture

## Overview

GPU burst windows allow Busibox to dynamically provision GPU compute on Rackspace Spot for time-sensitive AI workloads, then deprovision to minimize costs. The architecture uses a "provision-on-demand" pattern where a GPU node is spun up for a defined window (e.g., 1 hour), heavy AI tasks run during that window, and the node is torn down afterward.

## Architecture Diagram

```
                   ┌─────────────────────────────────────────────┐
                   │           Rackspace Spot Cluster             │
                   │                                             │
  ┌──────────┐     │  ┌────────────────────────────────────────┐  │
  │ Terraform │────▶│  │  Base Node (mh.vs1.xlarge, always-on)  │  │
  │ (API)     │     │  │                                        │  │
  └──────────┘     │  │  postgres, redis, minio, milvus         │  │
                   │  │  authz, data, search, agent, litellm     │  │
  ┌──────────┐     │  │  embedding, bridge, docs, nginx          │  │
  │ gpu-burst│────▶│  └────────────────────────────────────────┘  │
  │ script   │     │                                             │
  └──────────┘     │  ┌────────────────────────────────────────┐  │
       │           │  │  GPU Node (gpu.vs1.large, on-demand)    │  │
       │           │  │                                        │  │
       └──────────▶│  │  vLLM (Qwen2.5-7B-Instruct-AWQ)       │  │
                   │  │  ← tainted: nvidia.com/gpu=present     │  │
                   │  └────────────────────────────────────────┘  │
                   │                                             │
                   └─────────────────────────────────────────────┘

  Model Routing (LiteLLM):
  ┌───────────────┐
  │  "gpu-agent"  │──▶ 1st: vLLM (local, GPU, 10s timeout)
  │  model name   │──▶ 2nd: OpenAI gpt-4o-mini (cloud fallback)
  └───────────────┘
```

## How It Works

### Normal Operation (No GPU)

- All services run on the base node
- Model `gpu-agent` routes to cloud provider (OpenAI) via LiteLLM
- Cost: only base node + cloud API costs

### During GPU Burst Window

1. **Provision**: Terraform creates a GPU node pool on Rackspace Spot
2. **Deploy**: vLLM deployment scaled from 0→1 replica
3. **Route**: LiteLLM detects vLLM is available, routes `gpu-agent` to local vLLM (latency-based routing)
4. **Execute**: Agents using `gpu-agent` model get fast, local GPU inference
5. **Shutdown**: vLLM scaled to 0→0, Terraform destroys GPU node pool
6. **Fallback**: LiteLLM seamlessly falls back to cloud for `gpu-agent`

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Tainted GPU nodes | Only vLLM pods schedule on GPU - base workloads stay on base node |
| LiteLLM model routing | Transparent to agents - they just use "gpu-agent" model name |
| Latency-based routing | When vLLM is available, it's faster and gets priority automatically |
| Short timeout (10s) | If vLLM is down, fall back to cloud quickly instead of hanging |
| PVC for model cache | vLLM model persists on SSD between burst windows (faster startup) |

## Commands

```bash
# Start a GPU burst
make k8s-gpu-up                     # Provision GPU + start vLLM

# Stop GPU burst (stop billing!)
make k8s-gpu-down                   # Stop vLLM + deprovision GPU

# Check status
make k8s-gpu-status                 # Show GPU node + vLLM status

# Timed window (auto-shutdown after N minutes)
make k8s-gpu-window MINUTES=60      # 1-hour burst window
```

## Cost Model

| Resource | Cost | When |
|----------|------|------|
| Base node (mh.vs1.xlarge) | ~$0.05/hr (spot bid) | Always |
| GPU node (gpu.vs1.large) | ~$0.50/hr (spot bid) | Only during burst |
| vLLM model cache (20Gi SSD) | ~$1.20/mo | Persistent |
| Cloud API fallback | Per-token pricing | When no GPU |

**Example**: 1-hour daily GPU burst = ~$0.50/day = ~$15/month for GPU compute.

## Task Planning for Burst Windows

### Recommended Pattern

Since GPU time is limited, batch tasks to maximize utilization:

```
Window Start
  ├── Document re-embedding (high-dimension models)
  ├── Batch agent tasks (complex reasoning)
  ├── Search index optimization (GPU reranking)
  └── Model evaluation benchmarks
Window End (auto-shutdown)
```

### Agent Configuration

Agents that should use GPU when available:

```python
# Agent configured with gpu-agent model
agent_config = {
    "model": "gpu-agent",  # Routes to vLLM when GPU is up, cloud when down
    "name": "heavy-reasoning-agent",
    # ... other config
}
```

### Future: Task Queue Integration

Planned enhancement for automated burst windows:

1. Tasks submitted to a "gpu-burst" queue in Redis
2. When queue reaches threshold, auto-provision GPU
3. Tasks drain from queue during burst window
4. When queue empty (or timeout), auto-deprovision
5. Remaining tasks fall back to cloud

## Files

| File | Purpose |
|------|---------|
| `k8s/terraform/main.tf` | Terraform for node pool management |
| `k8s/base/llm/vllm-gpu.yaml` | vLLM Deployment (0 replicas by default) |
| `k8s/base/llm/litellm.yaml` | LiteLLM config with gpu-agent routing |
| `scripts/k8s/gpu-burst.sh` | GPU burst lifecycle management |

## Prerequisites

1. **Terraform** installed (`brew install terraform`)
2. **Rackspace Spot API token** in `k8s/terraform/terraform.tfvars`
3. **GPU server class** available in your region (check with `terraform output gpu_server_classes`)
4. **NVIDIA device plugin** installed in cluster (may need Rackspace Spot support)

## Limitations

- Rackspace Spot GPU nodes are bid-based - they may not be immediately available
- Node provision time: 2-10 minutes depending on availability
- vLLM model loading: 2-5 minutes (first time longer, cached after)
- Total burst startup time: ~5-15 minutes
- Node pre-emption possible (spot instances) - use preemption webhook for alerts
