# ColPali Visual Embedding Service

Deploys ColPali v1.3 for visual document embeddings using the native `colpali-engine` package.

## Overview

ColPali generates multi-vector embeddings (128 patches × 128 dimensions) for PDF page images, enabling visual search without OCR. This role deploys a standalone FastAPI service that provides an OpenAI-compatible embeddings API.

## Architecture

- **Model**: vidore/colpali-v1.3 (LoRA adapters on PaliGemma-3B)
- **Base Model**: google/paligemma-3b-pt-448 (~11GB)
- **Implementation**: Native colpali-engine package (not vLLM)
- **API**: OpenAI-compatible `/v1/embeddings` endpoint
- **GPU**: Dedicated GPU (default: GPU 2)

## Why Not vLLM?

vLLM's V1 engine doesn't support LoRA adapters for vision-language models like PaliGemma. While V0 engine has better LoRA support, the native `colpali-engine` package provides:
- Direct ColPali implementation
- Better performance for vision embeddings
- Simpler deployment
- Official ColPali support

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

```bash
# Deploy to test environment
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

```python
import requests
import base64

# Read image and encode
with open("page.png", "rb") as f:
    image_b64 = base64.b64encode(f.read()).decode()

# Generate embedding
response = requests.post(
    "http://vllm-lxc:8002/v1/embeddings",
    json={
        "input": [f"data:image/png;base64,{image_b64}"],
        "model": "colpali"
    }
)

embedding = response.json()["data"][0]["embedding"]
print(f"Embedding dimensions: {len(embedding)}")  # 16384 (128*128)
```

## Monitoring

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

