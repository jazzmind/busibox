# LLM Infrastructure - Quick Start Guide

**Status**: Ready for Deployment! 🚀  
**Date**: 2025-10-16

## ✅ What's Been Completed

### Infrastructure (100% Complete)
- ✅ **T001**: Container definitions (vars.env, test-vars.env)
- ✅ **T002**: LXC containers created (litellm-lxc, ollama-lxc, vllm-lxc)
- ✅ **T004**: Ansible inventory configured

### Ansible Roles (100% Complete)
- ✅ **T005-T008**: Ollama role (fast inference, small models)
- ✅ **T009-T012**: vLLM role (high-performance, large models)
- ✅ **T013-T016**: LiteLLM role (unified API gateway)

### What's Left
- ⏳ **T003**: GPU passthrough configuration (manual, guided below)
- ⏳ **T017-T020**: Integration testing & agent-server connection

---

## 🚀 Deployment Steps

### Step 1: GPU Passthrough (15 minutes)

```bash
# SSH to Proxmox host
ssh root@proxmox

# Pull latest code
cd /root/busibox
git pull origin 002-deploy-app-servers

# Run GPU passthrough script
cd provision/pct
bash configure-gpu-passthrough.sh 308 309

# Expected output:
# ==> Configuring GPU passthrough
#     Ollama container: 308 (GPU 0)
#     vLLM container: 309 (GPU 1)
# ==> Restarting containers...
# ==> GPU passthrough configured successfully!
```

**Verify GPUs:**
```bash
# Check Ollama container (GPU 0)
ssh root@10.96.201.208
nvidia-smi  # Should show GPU 0
exit

# Check vLLM container (GPU 1)
ssh root@10.96.201.209
nvidia-smi  # Should show GPU 1
exit
```

---

### Step 2: Deploy LLM Stack (30-60 minutes)

```bash
# On Proxmox or your Ansible control machine
cd /root/busibox/provision/ansible

# Deploy all LLM services
ansible-playbook -i inventory/test \
  --limit llm_services \
  site.yml

# Or deploy individually:
# ansible-playbook -i inventory/test --limit ollama --tags ollama site.yml
# ansible-playbook -i inventory/test --limit vllm --tags vllm site.yml
# ansible-playbook -i inventory/test --limit litellm --tags litellm site.yml
```

**What This Does:**
1. **Ollama** (5-10 min):
   - Installs Ollama binary
   - Configures systemd service
   - Pulls 3 models: llama3:8b, phi3:3.8b, gemma:2b
   
2. **vLLM** (15-30 min):
   - Installs Python, PyTorch, vLLM
   - Downloads Llama-3-8B-Instruct model (~8GB)
   - Starts OpenAI-compatible API server

3. **LiteLLM** (2-5 min):
   - Installs LiteLLM proxy
   - Configures routing to Ollama + vLLM
   - Starts unified API gateway

---

### Step 3: Verify Deployment (5 minutes)

```bash
# Check all services are running
ssh root@10.96.201.208 "systemctl status ollama"
ssh root@10.96.201.209 "systemctl status vllm"
ssh root@10.96.201.207 "systemctl status litellm"

# Test Ollama directly
curl http://10.96.201.208:11434/api/tags

# Test vLLM directly
curl http://10.96.201.209:8000/health

# Test LiteLLM proxy
curl http://10.96.201.207:4000/health

# List models via LiteLLM
curl -H "Authorization: Bearer sk-litellm-master-key-change-me" \
  http://10.96.201.207:4000/models
```

**Expected Models:**
- `llama3-8b` (Ollama)
- `phi3` (Ollama)
- `gemma-2b` (Ollama)
- `llama3-8b-vllm` (vLLM)

---

### Step 4: End-to-End Test (2 minutes)

```bash
# Test chat completion via LiteLLM (routes to Ollama)
curl -X POST http://10.96.201.207:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-litellm-master-key-change-me" \
  -d '{
    "model": "llama3-8b",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "Explain what LiteLLM does in one sentence."}
    ],
    "max_tokens": 100
  }'

# Test via vLLM backend
curl -X POST http://10.96.201.207:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-litellm-master-key-change-me" \
  -d '{
    "model": "llama3-8b-vllm",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "max_tokens": 50
  }'
```

---

## 📊 Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│              LiteLLM Proxy                          │
│              10.96.201.207:4000                     │
│              OpenAI-compatible API                  │
└───────────────┬─────────────────────────────────────┘
                │
        ┌───────┴────────┐
        │                │
┌───────▼────────┐  ┌────▼──────────┐
│ Ollama         │  │ vLLM          │
│ 10.96.201.208  │  │ 10.96.201.209 │
│ GPU 0          │  │ GPU 1         │
│                │  │               │
│ llama3:8b      │  │ llama3-8b     │
│ phi3:3.8b      │  │ (OpenAI API)  │
│ gemma:2b       │  │               │
└────────────────┘  └───────────────┘
```

---

## 🔧 Configuration Files

### LiteLLM Config
**Location**: `/etc/litellm/config.yaml` on litellm-lxc

```yaml
model_list:
  - model_name: llama3-8b
    litellm_params:
      model: ollama/llama3:8b
      api_base: http://10.96.201.208:11434
  # ... more models
```

### Service Ports
- **LiteLLM**: 4000 (main entry point)
- **Ollama**: 11434
- **vLLM**: 8000

### Master Key
Default: `sk-litellm-master-key-change-me`  
**⚠️ CHANGE THIS** for production!

Update in `inventory/test/group_vars/all.yml`:
```yaml
litellm_master_key: "sk-your-secure-key-here"
```

---

## 🐛 Troubleshooting

### Ollama: Models Not Pulled
```bash
ssh root@10.96.201.208
sudo -u ollama ollama pull llama3:8b
sudo -u ollama ollama list
```

### vLLM: Out of GPU Memory
```bash
# Check GPU usage
ssh root@10.96.201.209
nvidia-smi

# Reduce memory usage
# Edit /etc/default/vllm:
# Add: VLLM_GPU_MEMORY_UTILIZATION=0.8
systemctl restart vllm
```

### LiteLLM: Can't Connect to Backends
```bash
# Test backend connectivity
curl http://10.96.201.208:11434/api/tags  # Ollama
curl http://10.96.201.209:8000/health     # vLLM

# Check LiteLLM config
cat /etc/litellm/config.yaml

# View logs
journalctl -u litellm -f
```

### GPU Passthrough Not Working
```bash
# On Proxmox host
cat /etc/pve/lxc/308.conf | grep nvidia
cat /etc/pve/lxc/309.conf | grep nvidia

# Should see:
# lxc.cgroup2.devices.allow: c 195:* rwm
# lxc.mount.entry: /dev/nvidia0 dev/nvidia0 ...

# If missing, run:
cd /root/busibox/provision/pct
bash configure-gpu-passthrough.sh 308 309
```

---

## 📈 Next Steps

### 1. Update Agent Server

Update `agent-server` to use LiteLLM endpoint:

```yaml
# In inventory/test/group_vars/all.yml
applications:
  - name: agent-server
    env:
      OPENAI_API_BASE: "http://{{ litellm_ip }}:{{ litellm_port }}/v1"
      OPENAI_API_KEY: "{{ litellm_master_key }}"
      DEFAULT_MODEL: "llama3-8b"
```

Re-deploy agent-server:
```bash
ansible-playbook -i inventory/test \
  --limit agent \
  --tags app_deployer \
  site.yml
```

### 2. Load Larger Models (Optional)

For vLLM with Llama-3-70B:

```yaml
# In inventory/test/group_vars/all.yml or host_vars
vllm_default_model: "meta-llama/Meta-Llama-3-70B-Instruct"
vllm_tensor_parallel_size: 1  # Or 2 for multi-GPU
vllm_gpu_memory_utilization: 0.95
```

**Requirements**:
- 40GB+ GPU VRAM for 70B models
- 140GB+ disk space
- 1-2 hours download time

### 3. Production Deployment

```bash
# Create production containers
cd /root/busibox/provision/pct
source vars.env
bash create_lxc_base.sh production

# Configure GPU passthrough
bash configure-gpu-passthrough.sh 208 209

# Deploy
cd ../ansible
ansible-playbook -i inventory/production \
  --limit llm_services \
  site.yml
```

---

## 📚 Additional Resources

- **Full Spec**: `specs/003-llm-infrastructure/spec.md`
- **Deployment Guide**: `specs/003-llm-infrastructure/DEPLOYMENT_GUIDE.md`
- **Tasks**: `specs/003-llm-infrastructure/tasks.md`
- **Ollama Docs**: https://github.com/ollama/ollama
- **vLLM Docs**: https://docs.vllm.ai/
- **LiteLLM Docs**: https://docs.litellm.ai/

---

## 🎉 Success Criteria

- ✅ All services running and healthy
- ✅ GPUs detected and utilized
- ✅ Models loaded and responding
- ✅ LiteLLM routing correctly
- ✅ API requests completing successfully
- ✅ Health checks passing

**You're ready to use your local LLM infrastructure!** 🚀

