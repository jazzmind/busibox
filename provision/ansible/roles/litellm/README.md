# LiteLLM Ansible Role

Install and configure [LiteLLM](https://docs.litellm.ai/) as a unified API gateway for multiple LLM backends.

## Description

This role deploys LiteLLM as an OpenAI-compatible proxy that routes requests to multiple backend LLM servers (Ollama, vLLM, etc). It provides load balancing, fallback handling, and a unified API interface.

## Requirements

- Debian 12 or Ubuntu 22.04+
- Python 3.11+
- Backend LLM servers configured (Ollama, vLLM)

## Key Variables

- `litellm_port`: API port (default: `4000`)
- `litellm_master_key`: API authentication key
- `litellm_models`: List of models to expose
- `litellm_backends`: Backend server configuration
- `litellm_routing_strategy`: Load balancing strategy (default: `"simple-shuffle"`)

See `defaults/main.yml` for all variables.

## Example Playbook

```yaml
- hosts: litellm
  roles:
    - role: litellm
      vars:
        litellm_master_key: "{{ vault_litellm_key }}"
```

## Deployment

```bash
# Deploy LiteLLM
ansible-playbook -i inventory/test \
  --limit litellm \
  --tags litellm \
  site.yml
```

## Testing

```bash
# Health check
curl http://10.96.201.207:4000/health

# List models
curl -H "Authorization: Bearer sk-litellm-master-key-change-me" \
  http://10.96.201.207:4000/models

# Test completion (via Ollama)
curl -X POST http://10.96.201.207:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-litellm-master-key-change-me" \
  -d '{
    "model": "llama3-8b",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# View logs
journalctl -u litellm -f
```

## API Endpoints

- Health: `GET /health`
- Models: `GET /models`
- Chat: `POST /v1/chat/completions`
- Completions: `POST /v1/completions`
- Embeddings: `POST /v1/embeddings`
- Swagger UI: `GET /` (if enabled)

## License

MIT

