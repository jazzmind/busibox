# vLLM ColPali Role

Deploys ColPali v1.3 vision model on a dedicated GPU for visual document embeddings.

## Overview

ColPali is a vision-language model that generates multi-vector embeddings for PDF page images using a ColBERT-style approach. It's based on PaliGemma-3B with LoRA adapters.

- **Model**: `vidore/colpali-v1.3`
- **Base Model**: `google/paligemma-3b-pt-448` (required)
- **Architecture**: PaliGemma-3B + LoRA adapters
- **Output**: 128 patch embeddings per page (128 dims each)
- **GPU**: Dedicated GPU (default: GPU 2)
- **Port**: 8002

## Model Architecture

ColPali v1.3 uses:
- **Base**: PaliGemma-3B (~11GB)
- **Adapters**: LoRA adapters (~20MB)
- **Total Size**: ~11GB

The model must download both the base model and adapters. The base model is shared with any other PaliGemma-based models.

## Pre-Download Models

Before deploying, pre-download models on the Proxmox host:

```bash
# On Proxmox host as root
cd /root/busibox/provision/pct/host
bash setup-llm-models.sh
```

This downloads:
1. `google/paligemma-3b-pt-448` (base model)
2. `vidore/colpali-v1.3` (LoRA adapters)

## Deployment

```bash
cd provision/ansible

# Deploy ColPali
make vllm-colpali

# Or with specific inventory
ansible-playbook -i inventory/production/hosts.yml site.yml --tags vllm_colpali
```

## Configuration

Default configuration in `defaults/main.yml`:

- **GPU**: `CUDA_VISIBLE_DEVICES=2`
- **Port**: 8002
- **Model**: `vidore/colpali-v1.3`
- **Memory**: 90% GPU memory utilization
- **Context**: 4096 tokens
- **Batch Size**: 128 sequences

## Usage

ColPali provides an OpenAI-compatible API endpoint:

```bash
# Health check
curl http://10.96.200.208:8002/health

# Get embeddings for an image
curl http://10.96.200.208:8002/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "colpali",
    "input": "image_data_here"
  }'
```

## Integration

The ingest worker uses ColPali for visual document embeddings:

```bash
# Configure in ingest worker
COLPALI_BASE_URL=http://10.96.200.208:8002/v1
COLPALI_API_KEY=EMPTY
COLPALI_ENABLED=true
```

## Troubleshooting

### Model not loading

Check if base model is cached:
```bash
ls -lh /var/lib/llm-models/huggingface/hub/models--google--paligemma-3b-pt-448
```

### GPU memory issues

Reduce `vllm_colpali_gpu_memory_utilization` in defaults:
```yaml
vllm_colpali_gpu_memory_utilization: 0.80  # Reduce from 0.90
```

### Service status

```bash
# Check service
systemctl status vllm-colpali

# View logs
journalctl -u vllm-colpali -n 100 --no-pager
```

## References

- [ColPali v1.3 Model Card](https://huggingface.co/vidore/colpali-v1.3)
- [ColPali Paper](https://arxiv.org/abs/2407.01449)
- [PaliGemma Base Model](https://huggingface.co/google/paligemma-3b-pt-448)

