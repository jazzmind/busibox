# Spec 003: LLM Infrastructure Deployment

**Status**: In Progress  
**Created**: 2025-10-16  
**Priority**: P1 (High)

## Overview

Deploy a scalable LLM infrastructure using LiteLLM as a unified API gateway to multiple local model servers (Ollama and vLLM), each with dedicated GPU resources.

## Architecture

### Container Layout

```
┌─────────────────────────────────────────────────────────┐
│                    Proxmox Host                         │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐        │
│  │ GPU 0      │  │ GPU 1      │  │ GPU 2      │        │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘        │
│        │               │               │                │
│  ┌─────▼──────────┐  ┌─▼─────────────┐ ┌▼────────────┐ │
│  │  ollama-lxc    │  │  vllm-lxc     │ │ (future)    │ │
│  │  10.96.200.31  │  │  10.96.200.32 │ │             │ │
│  │  Port: 11434   │  │  Port: 8000   │ │             │ │
│  │                │  │               │ │             │ │
│  │  Llama 3 8B    │  │  Llama 3 70B  │ │             │ │
│  │  Phi-3         │  │  Mixtral 8x7B │ │             │ │
│  └────────────────┘  └───────────────┘ └─────────────┘ │
│         ▲                    ▲                          │
│         │                    │                          │
│         └────────┬───────────┘                          │
│                  │                                      │
│         ┌────────▼──────────┐                          │
│         │  litellm-lxc      │                          │
│         │  10.96.200.30     │                          │
│         │  Port: 4000       │                          │
│         │                   │                          │
│         │  Unified API      │                          │
│         │  + Routing Logic  │                          │
│         └────────┬──────────┘                          │
│                  │                                      │
└──────────────────┼──────────────────────────────────────┘
                   │
          ┌────────▼──────────┐
          │  agent-server     │
          │  10.96.201.202    │
          │                   │
          │  Consumes LLM API │
          └───────────────────┘
```

### Component Responsibilities

#### 1. LiteLLM Proxy (`litellm-lxc`)
- **Purpose**: Unified API gateway for all LLM requests
- **No GPU**: CPU-only container
- **Port**: 4000 (OpenAI-compatible API)
- **Features**:
  - OpenAI API compatibility
  - Request routing to appropriate model server
  - Load balancing across models
  - Fallback handling
  - Request/response logging
  - Cost tracking
  - Rate limiting

#### 2. Ollama Server (`ollama-lxc`)
- **Purpose**: Fast inference for smaller models
- **GPU**: GPU 0 (passthrough)
- **Port**: 11434
- **Models**:
  - Llama 3 8B (fast, general purpose)
  - Phi-3 (code, reasoning)
  - Gemma 2B (lightweight tasks)
- **Use Cases**:
  - Quick queries
  - Code completion
  - Simple classification
  - High-throughput workloads

#### 3. vLLM Server (`vllm-lxc`)
- **Purpose**: High-performance inference for large models
- **GPU**: GPU 1 (passthrough)
- **Port**: 8000
- **Models**:
  - Llama 3 70B (complex reasoning)
  - Mixtral 8x7B (MoE, versatile)
  - CodeLlama 34B (advanced coding)
- **Use Cases**:
  - Complex reasoning
  - Long-context analysis
  - Advanced code generation
  - RAG-heavy workloads

## Requirements

### Functional Requirements

**FR-101**: LiteLLM shall provide an OpenAI-compatible API endpoint  
**FR-102**: LiteLLM shall route requests to appropriate backend servers based on model name  
**FR-103**: LiteLLM shall support fallback to alternative models on failure  
**FR-104**: Ollama shall support multiple concurrent model loading  
**FR-105**: vLLM shall support efficient batching and continuous batching  
**FR-106**: All model servers shall expose health check endpoints  
**FR-107**: GPU passthrough shall be configured for Ollama and vLLM containers  

### Non-Functional Requirements

**NFR-101**: Model switching latency < 30 seconds for Ollama  
**NFR-102**: First-token latency < 1 second for 8B models on Ollama  
**NFR-103**: Throughput > 100 tokens/sec for 70B models on vLLM  
**NFR-104**: LiteLLM response time overhead < 50ms  
**NFR-105**: GPU memory utilization > 90% under load  
**NFR-106**: All services shall auto-restart on failure  
**NFR-107**: Configuration changes shall not require container rebuild  

## Implementation Phases

### Phase 1: LXC Container Provisioning
- Update `vars.env` and `test-vars.env` with new container definitions
- Create LXC containers on Proxmox host
- Configure GPU passthrough for Ollama and vLLM
- Verify GPU availability in containers

### Phase 2: Ollama Server Deployment
- Create `ollama` Ansible role
- Install Ollama binary and dependencies
- Configure systemd service
- Pull initial models (Llama 3 8B, Phi-3)
- Implement health checks
- Deploy to test environment

### Phase 3: vLLM Server Deployment
- Create `vllm` Ansible role
- Install Python, CUDA, and vLLM
- Configure model serving
- Download initial models (Llama 3 70B)
- Implement health checks
- Deploy to test environment

### Phase 4: LiteLLM Proxy Deployment
- Create `litellm` Ansible role
- Install LiteLLM and dependencies
- Configure routing to Ollama and vLLM
- Set up OpenAI-compatible endpoints
- Implement health checks
- Deploy to test environment

### Phase 5: Integration & Testing
- End-to-end API testing
- Performance benchmarking
- GPU utilization verification
- Failover testing
- Load testing

### Phase 6: Production Deployment
- Deploy to production environment
- Update agent-server to use LiteLLM endpoint
- Monitoring and alerting setup
- Documentation

## Configuration

### LiteLLM Configuration (`config.yaml`)

```yaml
model_list:
  # Ollama models (fast, small)
  - model_name: llama3-8b
    litellm_params:
      model: ollama/llama3:8b
      api_base: http://10.96.200.31:11434
      
  - model_name: phi3
    litellm_params:
      model: ollama/phi3
      api_base: http://10.96.200.31:11434
      
  # vLLM models (powerful, large)
  - model_name: llama3-70b
    litellm_params:
      model: openai/meta-llama/Meta-Llama-3-70B
      api_base: http://10.96.200.32:8000/v1
      
  - model_name: mixtral-8x7b
    litellm_params:
      model: openai/mistralai/Mixtral-8x7B-Instruct-v0.1
      api_base: http://10.96.200.32:8000/v1

router_settings:
  routing_strategy: simple-shuffle  # Load balance across replicas
  allowed_fails: 3
  num_retries: 2
  timeout: 60
  
litellm_settings:
  drop_params: true  # Drop unsupported params
  add_function_to_prompt: true
  
general_settings:
  master_key: "${LITELLM_MASTER_KEY}"  # From secrets
```

### Ollama Systemd Service

```ini
[Unit]
Description=Ollama LLM Server
After=network.target

[Service]
Type=simple
User=ollama
Group=ollama
WorkingDirectory=/opt/ollama
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_MODELS=/var/lib/ollama/models"
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### vLLM Systemd Service

```ini
[Unit]
Description=vLLM Model Server
After=network.target

[Service]
Type=simple
User=vllm
Group=vllm
WorkingDirectory=/opt/vllm
Environment="CUDA_VISIBLE_DEVICES=0"
Environment="HF_HOME=/var/lib/vllm/huggingface"
ExecStart=/opt/vllm/venv/bin/python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Meta-Llama-3-70B \
  --host 0.0.0.0 \
  --port 8000 \
  --tensor-parallel-size 1 \
  --trust-remote-code
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## GPU Passthrough Configuration

### Proxmox Host Setup

```bash
# Enable IOMMU in GRUB
# Edit /etc/default/grub:
GRUB_CMDLINE_LINUX_DEFAULT="quiet intel_iommu=on iommu=pt"

# Update GRUB
update-grub

# Load vfio modules
echo "vfio" >> /etc/modules
echo "vfio_iommu_type1" >> /etc/modules
echo "vfio_pci" >> /etc/modules
echo "vfio_virqfd" >> /etc/modules

# Reboot
reboot
```

### LXC GPU Configuration

For Ollama (GPU 0):
```bash
# Find GPU device numbers
ls -l /dev/nvidia*

# Add to LXC config (/etc/pve/lxc/[CTID].conf)
lxc.cgroup2.devices.allow: c 195:* rwm  # nvidia control
lxc.cgroup2.devices.allow: c 234:* rwm  # nvidia device
lxc.cgroup2.devices.allow: c 237:* rwm  # nvidiactl
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
```

For vLLM (GPU 1):
```bash
# Same as above but with nvidia1
lxc.mount.entry: /dev/nvidia1 dev/nvidia1 none bind,optional,create=file
```

## Security Considerations

1. **API Key Management**: LiteLLM master key stored in Ansible Vault
2. **Network Isolation**: Model servers only accessible from LiteLLM proxy
3. **Resource Limits**: cgroups limits on CPU/RAM per container
4. **Model Access Control**: LiteLLM API keys for different applications
5. **GPU Isolation**: Dedicated GPU per container prevents resource conflicts

## Monitoring & Observability

### Metrics to Track

- **LiteLLM**:
  - Requests per second
  - Request latency (p50, p95, p99)
  - Error rate
  - Model routing distribution
  - Cost per request

- **Ollama**:
  - GPU utilization
  - GPU memory usage
  - Model load time
  - Inference latency
  - Concurrent requests

- **vLLM**:
  - GPU utilization
  - GPU memory usage
  - Batch size
  - Throughput (tokens/sec)
  - Queue depth

### Health Checks

All services expose health endpoints:
- LiteLLM: `http://10.96.200.30:4000/health`
- Ollama: `http://10.96.200.31:11434/api/tags`
- vLLM: `http://10.96.200.32:8000/health`

## Dependencies

### LiteLLM Dependencies
- Python 3.11+
- `litellm` package
- Redis (optional, for caching)

### Ollama Dependencies
- CUDA 12.1+
- NVIDIA Driver 525+
- Docker (optional, not using)

### vLLM Dependencies
- Python 3.10+
- CUDA 12.1+
- PyTorch 2.1+
- `vllm` package
- HuggingFace transformers

## Rollout Plan

1. ✅ Test environment LXC creation
2. ✅ GPU passthrough verification
3. 🔄 Ollama deployment and model pull (Phase 2)
4. 🔄 vLLM deployment and model pull (Phase 3)
5. 🔄 LiteLLM deployment and configuration (Phase 4)
6. 🔄 Integration testing (Phase 5)
7. ⏳ Production deployment (Phase 6)

## Success Criteria

- [ ] All containers running and healthy
- [ ] GPUs visible and utilized in Ollama and vLLM containers
- [ ] LiteLLM successfully routes to all backend models
- [ ] End-to-end inference latency < 2 seconds for 8B models
- [ ] End-to-end inference latency < 5 seconds for 70B models
- [ ] No GPU memory errors under normal load
- [ ] Automatic failover working between models
- [ ] Health checks passing for all services

## References

- [LiteLLM Documentation](https://docs.litellm.ai/)
- [Ollama Documentation](https://github.com/ollama/ollama)
- [vLLM Documentation](https://docs.vllm.ai/)
- [Proxmox GPU Passthrough Guide](https://pve.proxmox.com/wiki/PCI_Passthrough)

