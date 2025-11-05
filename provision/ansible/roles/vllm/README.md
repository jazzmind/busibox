# vLLM Ansible Role

Install and configure [vLLM](https://docs.vllm.ai/) for high-performance LLM inference with GPU support.

## Description

This role deploys vLLM on Debian/Ubuntu systems with NVIDIA GPUs, configures it as a systemd service with an OpenAI-compatible API, and optionally downloads models from HuggingFace.

## Requirements

- Debian 12 or Ubuntu 22.04+
- **NVIDIA GPU with 16GB+ VRAM** (required)
- NVIDIA drivers 525+ installed
- CUDA 12.1+ support
- Python 3.10+
- 100GB+ free disk space for models

## Key Variables

- `vllm_default_model`: Model to serve (default: `"meta-llama/Meta-Llama-3-8B-Instruct"`)
- `vllm_port`: API port (default: `8000`)
- `vllm_cuda_visible_devices`: GPU device ID (default: `"0"`)
- `vllm_tensor_parallel_size`: GPUs for tensor parallelism (default: `1`)
- `vllm_hf_token`: HuggingFace token for gated models
- `vllm_gpu_memory_utilization`: GPU memory usage (default: `0.9`)

See `defaults/main.yml` for all variables.

## Example Playbook

```yaml
- hosts: vllm
  roles:
    - role: vllm
      vars:
        vllm_default_model: "meta-llama/Meta-Llama-3-70B-Instruct"
        vllm_hf_token: "{{ vault_hf_token }}"
        vllm_cuda_visible_devices: "1"
```

## Deployment

```bash
# Deploy vLLM
ansible-playbook -i inventory/test \
  --limit vllm \
  --tags vllm \
  site.yml

# Skip model download (faster for testing)
ansible-playbook -i inventory/test \
  --limit vllm \
  --skip-tags vllm_models \
  site.yml
```

## Testing

```bash
# Check service
ssh root@10.96.201.209
systemctl status vllm

# Test API
curl http://localhost:8000/health

# Generate completion
curl -X POST http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Meta-Llama-3-8B-Instruct",
    "prompt": "Once upon a time",
    "max_tokens": 50
  }'

# View logs
journalctl -u vllm -f
```

## Model Download

Large models can take 30+ minutes to download. The role uses async execution with 2-hour timeout.

For 70B models, ensure:
- 140GB+ free disk space
- 40GB+ GPU VRAM (or use tensor parallelism across multiple GPUs)

## Troubleshooting

### Out of GPU Memory

Reduce memory utilization or model size:
```yaml
vllm_gpu_memory_utilization: 0.85
vllm_max_model_len: 2048
```

### Model Download Timeout

Increase timeout in tasks/models.yml or pre-download models manually.

### Service Won't Start

Check logs: `journalctl -u vllm -n 100`  
Verify GPU: `nvidia-smi`  
Test PyTorch CUDA: `python -c "import torch; print(torch.cuda.is_available())"`

## API Endpoints

- Health: `GET /health`
- Models: `GET /v1/models`
- Completions: `POST /v1/completions`
- Chat: `POST /v1/chat/completions`

OpenAI-compatible API.

## License

MIT

