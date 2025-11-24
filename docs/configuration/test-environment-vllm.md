# Test Environment vLLM Configuration

**Created**: 2025-01-27  
**Purpose**: Guide for configuring vLLM routing in test environment

## Overview

The test environment is configured to use production vLLM by default to conserve GPU memory. Since vLLM in production uses all available GPU memory, running a separate test vLLM instance would require additional GPUs. The test environment can be configured to use either production or test vLLM instances.

## Default Behavior

By default, the test environment connects to **production vLLM** (`10.96.200.208`). This is controlled by the `use_production_vllm` flag in the test environment configuration.

**Configuration Location**: `provision/ansible/inventory/test/group_vars/all/00-main.yml`

```yaml
use_production_vllm: true  # Default: use production vLLM
vllm_ip: "{{ (use_production_vllm | default(true)) | ternary(network_base_octets_production + '.208', network_base_octets + '.208') }}"
```

## Why Use Production vLLM?

1. **GPU Memory Constraints**: vLLM requires significant GPU memory to load models
2. **Resource Efficiency**: Sharing production vLLM avoids duplicate GPU allocation
3. **Cost Savings**: No need for additional GPUs dedicated to test environment
4. **Consistency**: Test and production use the same model instances

## Configuration Options

### Option 1: Use Production vLLM (Default)

**Configuration**: `use_production_vllm: true`

- Test LiteLLM connects to production vLLM at `10.96.200.208`
- No test vLLM container is deployed
- Test services access production vLLM models
- **Network Requirement**: Test containers must be able to reach production network (`10.96.200.0/21`)

**When to Use**:
- Normal test environment operations
- When GPU resources are limited
- When testing application logic rather than vLLM itself

### Option 2: Use Test vLLM

**Configuration**: `use_production_vllm: false`

- Test LiteLLM connects to test vLLM at `10.96.201.208`
- Test vLLM container must be manually deployed
- Test services use isolated vLLM instance
- Requires dedicated GPU resources

**When to Use**:
- Testing vLLM configuration changes
- Testing vLLM model routing
- When isolation from production is required
- When testing GPU allocation strategies

## Switching Between Modes

### Switch to Production vLLM (Default)

1. Edit `provision/ansible/inventory/test/group_vars/all/00-main.yml`:
   ```yaml
   use_production_vllm: true
   ```

2. Redeploy LiteLLM:
   ```bash
   cd provision/ansible
   ansible-playbook -i inventory/test/hosts.yml site.yml --tags litellm_configure
   ```

3. Verify connection:
   ```bash
   # Check LiteLLM config uses production vLLM IP
   ssh root@<test-litellm-ip>
   cat /etc/litellm/config.yaml | grep api_base
   ```

### Switch to Test vLLM

1. **Deploy Test vLLM Container** (if not already created):
   ```bash
   # On Proxmox host
   cd /root/busibox/provision/pct
   # Follow container creation process for container ID 308
   ```

2. **Add Test vLLM to Inventory**:
   Edit `provision/ansible/inventory/test/hosts.yml`:
   ```yaml
   vllm:
     hosts:
       TEST-vllm-lxc:
         ansible_host: "{{ vllm_ip }}"
         container_id: 308
         gpu_device: all
   ```

3. **Update Configuration**:
   Edit `provision/ansible/inventory/test/group_vars/all/00-main.yml`:
   ```yaml
   use_production_vllm: false
   ```

4. **Deploy Test vLLM**:
   ```bash
   cd provision/ansible
   ansible-playbook -i inventory/test/hosts.yml site.yml --tags vllm
   ```

5. **Redeploy LiteLLM**:
   ```bash
   ansible-playbook -i inventory/test/hosts.yml site.yml --tags litellm_configure
   ```

6. **Verify Connection**:
   ```bash
   # Check LiteLLM config uses test vLLM IP
   ssh root@<test-litellm-ip>
   cat /etc/litellm/config.yaml | grep api_base
   # Should show: api_base: http://10.96.201.208:<port>/v1
   ```

## Network Connectivity

### Production vLLM Mode

When `use_production_vllm: true`, test containers must be able to reach production network:

- **Production vLLM IP**: `10.96.200.208`
- **Network**: `10.96.200.0/21`
- **Test Network**: `10.96.201.0/21`

**Verification**:
```bash
# From test container
ping 10.96.200.208
curl http://10.96.200.208:8000/health
```

### Test vLLM Mode

When `use_production_vllm: false`, test containers use local test vLLM:

- **Test vLLM IP**: `10.96.201.208`
- **Network**: `10.96.201.0/21`

## Container Management

### Test vLLM Container

- **Container ID**: 308 (reserved)
- **IP Address**: `10.96.201.208` (when deployed)
- **Status**: Not deployed by default
- **GPU Allocation**: All GPUs (when deployed)

### Checking Container Status

```bash
# On Proxmox host
pct status 308

# If container exists but stopped
pct start 308

# If container doesn't exist, follow container creation process
```

## Troubleshooting

### Test Services Cannot Reach Production vLLM

**Symptoms**: Connection timeouts or network errors when accessing vLLM models

**Solutions**:
1. Verify network connectivity:
   ```bash
   # From test container
   ping 10.96.200.208
   ```

2. Check firewall rules (if applicable)

3. Verify production vLLM is running:
   ```bash
   # From production vLLM container
   systemctl status vllm-8000
   ```

4. Check LiteLLM configuration:
   ```bash
   # From test LiteLLM container
   cat /etc/litellm/config.yaml | grep -A 5 api_base
   ```

### Test vLLM Not Starting

**Symptoms**: Test vLLM container fails to start or crashes

**Solutions**:
1. Check GPU availability:
   ```bash
   # On Proxmox host
   nvidia-smi
   ```

2. Check container logs:
   ```bash
   pct enter 308
   journalctl -u vllm-8000 -n 50
   ```

3. Verify GPU passthrough configuration

4. Check model configuration in `model_config.yml`

## Related Documentation

- [Ansible Configuration Guide](ansible-configuration.md) - Overall configuration structure
- [vLLM CPU Offload](vllm-cpu-offload.md) - GPU memory management
- [Model Memory Configuration](model-memory-config.md) - Model resource requirements

## Summary

- **Default**: Test environment uses production vLLM (`use_production_vllm: true`)
- **Benefit**: Saves GPU memory and resources
- **Flexibility**: Can switch to test vLLM when needed
- **Network**: Test containers must reach production network in default mode
- **Container**: Test vLLM container (ID 308) reserved but not deployed by default


