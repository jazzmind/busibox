# Ollama Ansible Role

Install and configure [Ollama](https://ollama.com/) for fast LLM inference with GPU support.

## Description

This role deploys Ollama on Debian/Ubuntu systems, configures it as a systemd service, and optionally pulls initial models. It automatically detects GPU availability and configures GPU acceleration when available.

## Requirements

- Debian 12 or Ubuntu 22.04+
- (Optional) NVIDIA GPU with drivers installed for GPU acceleration
- Python 3.8+
- Sufficient disk space for models (8GB+ recommended)

## Role Variables

### Installation

- `ollama_version`: Version to install (default: `"latest"`)
- `ollama_install_dir`: Installation directory (default: `/usr/local/bin`)
- `ollama_binary_url`: Download URL (default: official Ollama Linux binary)

### Service Configuration

- `ollama_user`: Service user (default: `ollama`)
- `ollama_group`: Service group (default: `ollama`)
- `ollama_home`: Home directory (default: `/var/lib/ollama`)
- `ollama_models_dir`: Model storage directory (default: `/var/lib/ollama/models`)

### Network

- `ollama_host`: Bind address (default: `"0.0.0.0"`)
- `ollama_port`: Listen port (default: `11434`)

### Performance

- `ollama_num_parallel`: Parallel requests (default: `4`)
- `ollama_max_loaded_models`: Max models in memory (default: `3`)
- `ollama_max_queue`: Max queued requests (default: `512`)

### GPU

- `ollama_gpu_enabled`: Enable GPU support (default: `true`)
- `ollama_gpu_layers`: GPU layers to use, -1 = all (default: `-1`)

### Models

```yaml
ollama_initial_models:
  - name: "llama3:8b"
    description: "Fast general-purpose model"
  - name: "phi3:3.8b"
    description: "Code and reasoning model"
```

### Resource Limits

- `ollama_memory_limit`: Systemd memory limit (default: `"16G"`)
- `ollama_cpu_limit`: CPU cores limit (default: `"4"`)

## Dependencies

None.

## Example Playbook

### Basic Usage

```yaml
- hosts: ollama
  roles:
    - role: ollama
```

### Custom Configuration

```yaml
- hosts: ollama
  roles:
    - role: ollama
      vars:
        ollama_port: 11435
        ollama_num_parallel: 8
        ollama_initial_models:
          - name: "llama3:8b"
            description: "Primary model"
          - name: "codellama:13b"
            description: "Code generation"
```

### GPU-Specific Configuration

```yaml
- hosts: ollama
  roles:
    - role: ollama
      vars:
        ollama_gpu_enabled: true
        ollama_gpu_layers: -1
        ollama_max_loaded_models: 2  # Fewer models with large GPU memory
```

## Usage

### Deploy Ollama

```bash
# Deploy to test environment
ansible-playbook -i inventory/test \
  --limit ollama \
  --tags ollama \
  site.yml

# Deploy to production
ansible-playbook -i inventory/production \
  --limit ollama \
  site.yml
```

### Deploy Specific Components

```bash
# Only install, skip configuration
ansible-playbook -i inventory/test \
  --limit ollama \
  --tags ollama_install \
  site.yml

# Only pull models
ansible-playbook -i inventory/test \
  --limit ollama \
  --tags ollama_models \
  site.yml
```

### Testing

```bash
# SSH to Ollama container
ssh root@10.96.201.208

# Check service status
systemctl status ollama

# List installed models
ollama list

# Test inference
ollama run llama3:8b "Hello, how are you?"

# Check API
curl http://localhost:11434/api/tags

# View logs
journalctl -u ollama -f
```

## API Endpoints

- **Health Check**: `GET http://host:11434/api/tags`
- **List Models**: `GET http://host:11434/api/tags`
- **Generate**: `POST http://host:11434/api/generate`
- **Chat**: `POST http://host:11434/api/chat`

Example:

```bash
curl -X POST http://10.96.201.208:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3:8b",
    "prompt": "Why is the sky blue?",
    "stream": false
  }'
```

## Troubleshooting

### Service Won't Start

```bash
# Check service status
systemctl status ollama

# Check logs
journalctl -u ollama -n 100 --no-pager

# Verify binary
/usr/local/bin/ollama --version

# Check permissions
ls -la /var/lib/ollama
```

### GPU Not Detected

```bash
# Check if GPU is available
nvidia-smi

# Check Ollama GPU status
ollama run llama3:8b "test"
# Look for "using GPU" in logs

# Check environment
cat /etc/default/ollama | grep GPU
```

### Model Download Fails

```bash
# Check disk space
df -h /var/lib/ollama

# Manually pull model
sudo -u ollama ollama pull llama3:8b

# Check network connectivity
curl -I https://ollama.com
```

### Out of Memory

```bash
# Check current resource usage
systemctl show ollama | grep Memory

# Reduce max loaded models
# Edit /etc/default/ollama:
OLLAMA_MAX_LOADED_MODELS=1

# Restart service
systemctl restart ollama
```

## Performance Tuning

### For High-Throughput Workloads

```yaml
ollama_num_parallel: 16
ollama_max_queue: 1024
ollama_memory_limit: "32G"
```

### For Large Models (70B+)

```yaml
ollama_max_loaded_models: 1
ollama_gpu_layers: -1  # Use all GPU layers
ollama_memory_limit: "64G"
```

### For Multiple Small Models

```yaml
ollama_max_loaded_models: 5
ollama_num_parallel: 8
ollama_initial_models:
  - name: "gemma:2b"
  - name: "phi3:3.8b"
  - name: "llama3:8b"
  - name: "mistral:7b"
```

## Security Considerations

- Service runs as dedicated `ollama` user (not root)
- Systemd security hardening enabled (`NoNewPrivileges`, `PrivateTmp`, etc.)
- Home directory is read-only except for model storage
- Firewall rules not included (configure separately)

## License

MIT

## Author Information

Created for the Busibox LLM Infrastructure project.

