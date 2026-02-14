# Implementation Tasks: LLM Infrastructure

**Project**: Spec 003 - LLM Infrastructure Deployment  
**Created**: 2025-10-16  
**Status**: In Progress

## Task Status Key
- ✅ Complete
- 🔄 In Progress
- ⏳ Pending
- ❌ Blocked

---

## Phase 1: LXC Container Provisioning

### T001: Update Container Definitions ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 30 minutes

**Description**: Add LiteLLM, Ollama, and vLLM container definitions to provisioning scripts

**Files**:
- `provision/pct/vars.env`
- `provision/pct/test-vars.env`

**Changes**:
```bash
# Production
CT_LITELLM=207
IP_LITELLM=10.96.200.30

CT_OLLAMA=208
IP_OLLAMA=10.96.200.31

CT_VLLM=209
IP_VLLM=10.96.200.32

# Test (offset +100)
CT_LITELLM_TEST=307
IP_LITELLM_TEST=10.96.201.207

CT_OLLAMA_TEST=308
IP_OLLAMA_TEST=10.96.201.208

CT_VLLM_TEST=309
IP_VLLM_TEST=10.96.201.209
```

**Acceptance Criteria**:
- [ ] Variables added to `vars.env`
- [ ] Variables added to `test-vars.env`
- [ ] Print functions updated to show new containers
- [ ] No conflicts with existing container IDs/IPs

---

### T002: Create LXC Containers ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 20 minutes

**Description**: Provision the three new LXC containers on Proxmox host

**Commands**:
```bash
cd /root/busibox/provision/pct
source test-vars.env
bash create_lxc_base.sh test  # Create test containers
```

**Acceptance Criteria**:
- [ ] `TEST-litellm-lxc` (307) created at 10.96.201.207
- [ ] `TEST-ollama-lxc` (308) created at 10.96.201.208
- [ ] `TEST-vllm-lxc` (309) created at 10.96.201.209
- [ ] All containers pingable
- [ ] SSH access working to all containers

---

### T003: Configure GPU Passthrough ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 1 hour

**Description**: Enable GPU passthrough for Ollama and vLLM containers

**Prerequisites**:
- IOMMU enabled in BIOS/GRUB
- NVIDIA drivers installed on host
- vfio modules loaded

**Steps**:
1. Verify host GPU setup
2. Edit LXC configs for Ollama (GPU 0)
3. Edit LXC configs for vLLM (GPU 1)
4. Restart containers
5. Verify GPU visibility with `nvidia-smi`

**Acceptance Criteria**:
- [ ] GPU 0 visible in Ollama container (`nvidia-smi` works)
- [ ] GPU 1 visible in vLLM container (`nvidia-smi` works)
- [ ] No GPU visible in LiteLLM container
- [ ] GPUs isolated (each container sees only its GPU)

---

### T004: Update Ansible Inventory ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 30 minutes

**Description**: Add new containers to Ansible inventory files

**Files**:
- `provision/ansible/inventory/test/hosts.yml`
- `provision/ansible/inventory/production/hosts.yml`
- `provision/ansible/inventory/test/group_vars/all.yml`
- `provision/ansible/inventory/production/group_vars/all.yml`

**Changes**:
Add to `hosts.yml`:
```yaml
  llm_services:
    hosts:
      litellm:
        ansible_host: "{{ litellm_ip }}"
      ollama:
        ansible_host: "{{ ollama_ip }}"
      vllm:
        ansible_host: "{{ vllm_ip }}"
```

Add to `all.yml`:
```yaml
# LLM Service IPs
litellm_ip: 10.96.201.207  # test
ollama_ip: 10.96.201.208
vllm_ip: 10.96.201.209
```

**Acceptance Criteria**:
- [ ] New host group `llm_services` created
- [ ] IP variables defined for test and production
- [ ] Ansible can ping all new hosts

---

## Phase 2: Ollama Server Deployment

### T005: Create Ollama Ansible Role ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 2 hours

**Description**: Create Ansible role to install and configure Ollama

**Directory Structure**:
```
provision/ansible/roles/ollama/
├── tasks/
│   ├── main.yml
│   ├── install.yml
│   ├── configure.yml
│   └── models.yml
├── templates/
│   ├── ollama.service.j2
│   └── modelfile.j2
├── handlers/
│   └── main.yml
├── defaults/
│   └── main.yml
└── README.md
```

**Tasks**:
- Install NVIDIA CUDA toolkit
- Download Ollama binary
- Create ollama user/group
- Configure systemd service
- Set up model storage directory
- Configure firewall rules

**Acceptance Criteria**:
- [ ] Role directory structure created
- [ ] Install tasks complete
- [ ] Service template created
- [ ] Handlers defined
- [ ] README documentation complete

---

### T006: Implement Ollama Installation ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 1.5 hours

**Description**: Implement Ollama installation tasks

**Installation Steps**:
1. Install dependencies (curl, ca-certificates)
2. Install NVIDIA CUDA toolkit 12.1+
3. Download Ollama binary from official source
4. Set proper permissions
5. Create system user
6. Create model storage directory

**Acceptance Criteria**:
- [ ] CUDA installed and verified
- [ ] Ollama binary downloaded to `/usr/local/bin/ollama`
- [ ] User `ollama` created
- [ ] Model directory `/var/lib/ollama/models` exists with correct permissions
- [ ] Ollama version command works

---

### T007: Configure Ollama Service ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 1 hour

**Description**: Create and configure systemd service for Ollama

**Service Configuration**:
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_MODELS=/var/lib/ollama/models"
Environment="OLLAMA_NUM_PARALLEL=4"
Environment="OLLAMA_MAX_LOADED_MODELS=3"
```

**Acceptance Criteria**:
- [ ] Systemd service file deployed
- [ ] Service enabled and started
- [ ] Service auto-restarts on failure
- [ ] Logs visible in journald
- [ ] Health endpoint responds: `curl http://localhost:11434/api/tags`

---

### T008: Pull Initial Ollama Models ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 1 hour (+ download time)

**Description**: Download initial model set for Ollama

**Models**:
- `llama3:8b` - General purpose, fast
- `phi3:3.8b` - Code and reasoning
- `gemma:2b` - Lightweight tasks

**Commands**:
```bash
ollama pull llama3:8b
ollama pull phi3:3.8b
ollama pull gemma:2b
```

**Acceptance Criteria**:
- [ ] All models downloaded successfully
- [ ] Models listed in `ollama list`
- [ ] Test inference works for each model
- [ ] Models accessible via API

---

## Phase 3: vLLM Server Deployment

### T009: Create vLLM Ansible Role ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 2 hours

**Description**: Create Ansible role to install and configure vLLM

**Directory Structure**:
```
provision/ansible/roles/vllm/
├── tasks/
│   ├── main.yml
│   ├── install.yml
│   ├── configure.yml
│   └── models.yml
├── templates/
│   ├── vllm.service.j2
│   └── model-config.json.j2
├── handlers/
│   └── main.yml
├── defaults/
│   └── main.yml
└── README.md
```

**Acceptance Criteria**:
- [ ] Role directory structure created
- [ ] Install tasks complete
- [ ] Service templates created
- [ ] Handlers defined
- [ ] README documentation complete

---

### T010: Implement vLLM Installation ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 2 hours

**Description**: Implement vLLM installation tasks

**Installation Steps**:
1. Install Python 3.10+
2. Install NVIDIA CUDA toolkit 12.1+
3. Create Python virtual environment
4. Install PyTorch with CUDA support
5. Install vLLM package
6. Create system user
7. Set up model cache directory

**Acceptance Criteria**:
- [ ] Python 3.10+ installed
- [ ] CUDA 12.1+ installed
- [ ] vLLM virtual environment created at `/opt/vllm/venv`
- [ ] User `vllm` created
- [ ] HuggingFace cache directory created
- [ ] vLLM version command works

---

### T011: Configure vLLM Service ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 1 hour

**Description**: Create and configure systemd service for vLLM

**Service Configuration**:
```ini
[Service]
Environment="CUDA_VISIBLE_DEVICES=0"
Environment="HF_HOME=/var/lib/vllm/huggingface"
ExecStart=/opt/vllm/venv/bin/python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Meta-Llama-3-70B \
  --host 0.0.0.0 \
  --port 8000 \
  --tensor-parallel-size 1
```

**Acceptance Criteria**:
- [ ] Systemd service file deployed
- [ ] Service enabled
- [ ] Environment variables configured
- [ ] Health endpoint accessible

---

### T012: Download Initial vLLM Models ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 2 hours (+ download time)

**Description**: Download initial model set for vLLM (models are large, 40GB+)

**Models**:
- `meta-llama/Meta-Llama-3-70B` - Large reasoning model

**Prerequisites**:
- HuggingFace token (for gated models)
- Sufficient disk space (100GB+ per model)

**Acceptance Criteria**:
- [ ] HuggingFace token configured
- [ ] Model downloaded to cache
- [ ] Service starts successfully with model loaded
- [ ] GPU memory utilized
- [ ] Test inference completes

---

## Phase 4: LiteLLM Proxy Deployment

### T013: Create LiteLLM Ansible Role ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 2 hours

**Description**: Create Ansible role to install and configure LiteLLM

**Directory Structure**:
```
provision/ansible/roles/litellm/
├── tasks/
│   ├── main.yml
│   ├── install.yml
│   └── configure.yml
├── templates/
│   ├── litellm.service.j2
│   └── config.yaml.j2
├── handlers/
│   └── main.yml
├── defaults/
│   └── main.yml
└── README.md
```

**Acceptance Criteria**:
- [ ] Role directory structure created
- [ ] Install tasks complete
- [ ] Configuration template created
- [ ] Handlers defined
- [ ] README documentation complete

---

### T014: Implement LiteLLM Installation ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 1 hour

**Description**: Implement LiteLLM installation tasks

**Installation Steps**:
1. Install Python 3.11+
2. Create Python virtual environment
3. Install LiteLLM package
4. Create system user
5. Set up configuration directory

**Acceptance Criteria**:
- [ ] Python 3.11+ installed
- [ ] LiteLLM virtual environment created at `/opt/litellm/venv`
- [ ] User `litellm` created
- [ ] Config directory `/etc/litellm` created
- [ ] LiteLLM version command works

---

### T015: Configure LiteLLM Routing ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 1.5 hours

**Description**: Create LiteLLM configuration for routing to Ollama and vLLM

**Configuration**:
- Model definitions for all backend models
- Routing strategy (load balancing)
- Fallback configuration
- Health check settings
- API key management

**Acceptance Criteria**:
- [ ] Configuration file deployed
- [ ] All backend models defined
- [ ] Routing logic configured
- [ ] Secrets integrated (master key from vault)
- [ ] Configuration validated

---

### T016: Configure LiteLLM Service ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 45 minutes

**Description**: Create and configure systemd service for LiteLLM

**Service Configuration**:
```ini
[Service]
Environment="LITELLM_MASTER_KEY=${SECRET}"
ExecStart=/opt/litellm/venv/bin/litellm \
  --config /etc/litellm/config.yaml \
  --port 4000 \
  --host 0.0.0.0
```

**Acceptance Criteria**:
- [ ] Systemd service file deployed
- [ ] Service enabled and started
- [ ] Health endpoint accessible: `http://IP:4000/health`
- [ ] API documentation accessible: `http://IP:4000/docs`

---

## Phase 5: Integration & Testing

### T017: End-to-End API Testing ⏳
**Status**: Pending  
**Priority**: P1  
**Estimate**: 2 hours

**Description**: Test complete request flow through LiteLLM to all backends

**Tests**:
1. Simple completion via Ollama (llama3:8b)
2. Code generation via Ollama (phi3)
3. Complex reasoning via vLLM (llama3-70b)
4. Streaming responses
5. Error handling and fallbacks
6. Invalid requests

**Acceptance Criteria**:
- [ ] All models accessible via LiteLLM API
- [ ] OpenAI SDK compatibility verified
- [ ] Streaming works correctly
- [ ] Error responses are informative
- [ ] Fallback logic triggers correctly

---

### T018: Performance Benchmarking ⏳
**Status**: Pending  
**Priority**: P2  
**Estimate**: 2 hours

**Description**: Benchmark inference performance across all models

**Metrics**:
- First token latency
- Throughput (tokens/sec)
- Request latency (p50, p95, p99)
- GPU utilization
- GPU memory usage
- Concurrent request handling

**Tools**:
- Custom benchmark script
- `nvidia-smi` for GPU monitoring
- Load testing tool (hey, wrk, or locust)

**Acceptance Criteria**:
- [ ] Baseline metrics documented
- [ ] Performance meets NFRs from spec
- [ ] No GPU OOM errors under load
- [ ] Graceful degradation under overload

---

### T019: Failover Testing ⏳
**Status**: Pending  
**Priority**: P2  
**Estimate**: 1 hour

**Description**: Verify failover and retry logic works correctly

**Test Scenarios**:
1. Ollama server down → Request routed to vLLM
2. vLLM server down → Request routed to Ollama
3. Model not loaded → Automatic model pull
4. Timeout on backend → Retry with different model
5. LiteLLM restart → Service recovery

**Acceptance Criteria**:
- [ ] Failover happens within 5 seconds
- [ ] No requests fail if any backend is healthy
- [ ] Health checks detect failures
- [ ] Services auto-restart on crash

---

### T020: Update Agent Server Integration ⏳
**Status**: Pending  
**Priority**: P2  
**Estimate**: 1 hour

**Description**: Update agent-server to use LiteLLM endpoint instead of OpenAI

**Changes**:
- Update environment variables
- Point to LiteLLM endpoint
- Update model names
- Test integration

**Files**:
- `provision/ansible/inventory/*/group_vars/all.yml`
- Agent-server `.env` template

**Acceptance Criteria**:
- [ ] Agent-server configured to use LiteLLM
- [ ] Model names updated
- [ ] Integration tests pass
- [ ] No regression in functionality

---

## Phase 6: Production Deployment

### T021: Production LXC Provisioning ⏳
**Status**: Pending  
**Priority**: P2  
**Estimate**: 30 minutes

**Description**: Create production LXC containers

**Acceptance Criteria**:
- [ ] Production containers created
- [ ] GPU passthrough configured
- [ ] Network connectivity verified

---

### T022: Production Deployment ⏳
**Status**: Pending  
**Priority**: P2  
**Estimate**: 2 hours

**Description**: Deploy LLM stack to production environment

**Steps**:
1. Deploy Ollama role to production
2. Deploy vLLM role to production
3. Deploy LiteLLM role to production
4. Verify all services healthy
5. Run smoke tests

**Acceptance Criteria**:
- [ ] All services running in production
- [ ] Health checks passing
- [ ] Smoke tests successful
- [ ] Monitoring enabled

---

### T023: Documentation & Runbook ⏳
**Status**: Pending  
**Priority**: P2  
**Estimate**: 2 hours

**Description**: Create operational documentation and runbooks

**Documents**:
- Deployment guide
- Troubleshooting guide
- Model management procedures
- Performance tuning guide
- Scaling guide

**Acceptance Criteria**:
- [ ] Deployment guide complete
- [ ] Common issues documented
- [ ] Model update procedure documented
- [ ] GPU troubleshooting guide complete

---

## Summary

**Total Tasks**: 23  
**Completed**: 0 ✅  
**In Progress**: 0 🔄  
**Pending**: 23 ⏳  
**Blocked**: 0 ❌

**Estimated Total Time**: 28-32 hours
**Target Completion**: TBD

**Next Steps**: Begin Phase 1 (T001-T004) - Container provisioning

