# ColPali Visual Embedding Service

Deploys ColPali v1.3 for visual document embeddings.

## Overview

ColPali generates multi-vector embeddings (128 patches × 128 dimensions) for PDF page images, enabling visual search without OCR.

## Deployment Options

### Option 1: vLLM with LoRA Adapters (Recommended)

**Status**: Now supported! ColPali is served via vLLM on GPU 0, port 8000.

- **Model**: vidore/colpali-v1.3 (LoRA adapters)
- **Base Model**: google/paligemma-3b-pt-448 (~11GB)
- **Implementation**: vLLM with LoRA adapter support
- **GPU**: GPU 0 (reserved for visual embeddings)
- **Port**: 8000
- **Deployment**: Use `vllm_8000` role (auto-configured via model_registry.yml)

To deploy via vLLM:
```bash
cd provision/ansible
# Configure model routing (auto-assigns ColPali to GPU 0)
bash ../../provision/pct/host/configure-vllm-model-routing.sh --interactive
# Deploy
make test
```

### Option 2: Standalone Service (Legacy)

**Status**: Available as alternative, but vLLM is now preferred.

- **Implementation**: Native colpali-engine package
- **API**: OpenAI-compatible `/v1/embeddings` endpoint
- **GPU**: Configurable (default: GPU 2)
- **Deployment**: Use this role (`colpali`)

The standalone service provides:
- Direct ColPali implementation
- Simpler debugging
- Independent from vLLM
- Official colpali-engine support

## Requirements

- CUDA-capable GPU
- Python 3.11+
- HuggingFace token (for gated models)
- Models pre-cached on Proxmox host (recommended)

## Configuration

Key variables in `defaults/main.yml`:

```yaml
colpali_model: "vidore/colpali-v1.3"
colpali_device: "cuda:2"
colpali_port: 8002
colpali_hf_token: "{{ secrets.huggingface.token }}"
```

## Deployment

### vLLM Deployment (Recommended)

```bash
# 1. Pre-cache models on Proxmox host
cd /root/busibox/provision/pct/host
bash setup-llm-models.sh

# 2. Configure model routing (auto-assigns ColPali to GPU 0, port 8000)
bash configure-vllm-model-routing.sh --interactive

# 3. Deploy vLLM
cd ../../provision/ansible
make test  # or: ansible-playbook -i inventory/test/hosts.yml site.yml --tags vllm_8000
```

### Standalone Deployment (Legacy)

```bash
# Deploy standalone ColPali service
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags colpali

# Or use Makefile
make colpali
```

## Pre-cache Models

To avoid downloading ~11GB on first startup:

```bash
# On Proxmox host
cd /root/busibox/provision/pct/host
bash setup-llm-models.sh
```

This downloads:
- `google/paligemma-3b-pt-448` (~11GB)
- `vidore/colpali-v1.3` (~20MB LoRA adapters)

## API Usage

### vLLM Endpoint (Port 8000)

```python
import requests
import base64

# Read image and encode
with open("page.png", "rb") as f:
    image_b64 = base64.b64encode(f.read()).decode()

# Generate embedding via vLLM
response = requests.post(
    "http://vllm-lxc:8000/v1/embeddings",
    json={
        "input": [f"data:image/png;base64,{image_b64}"],
        "model": "vidore/colpali-v1.3"  # Or use LoRA adapter name
    }
)

embedding = response.json()["data"][0]["embedding"]
print(f"Embedding dimensions: {len(embedding)}")  # 16384 (128*128)
```

### Standalone Endpoint (Port 8002, Legacy)

```python
# Generate embedding via standalone service
response = requests.post(
    "http://vllm-lxc:8002/v1/embeddings",
    json={
        "input": [f"data:image/png;base64,{image_b64}"],
        "model": "colpali"
    }
)
```

## Monitoring

### vLLM Deployment

```bash
# Check vLLM service status
systemctl status vllm-8000

# View logs
journalctl -u vllm-8000 -f

# Test health
curl http://localhost:8000/health

# List models
curl http://localhost:8000/v1/models
```

### Standalone Deployment

```bash
# Check service status
systemctl status colpali

# View logs
journalctl -u colpali -f

# Test health
curl http://localhost:8002/health
```

## Troubleshooting

### Model Not Cached
If models aren't pre-cached, first startup downloads ~11GB:
```bash
journalctl -u colpali -f  # Watch download progress
```

### HuggingFace Authentication
PaliGemma is a gated model requiring authentication:
1. Get token: https://huggingface.co/settings/tokens
2. Accept license: https://huggingface.co/google/paligemma-3b-pt-448
3. Add to vault: `secrets.huggingface.token`

### GPU Memory
ColPali + PaliGemma requires ~8-10GB VRAM:
```bash
nvidia-smi  # Check GPU memory usage
```

## References

- ColPali: https://huggingface.co/vidore/colpali-v1.3
- colpali-engine: https://github.com/illuin-tech/colpali
- PaliGemma: https://huggingface.co/google/paligemma-3b-pt-448

