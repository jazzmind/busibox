# Model Memory Configuration

**Created**: 2025-11-19  
**Status**: Active  
**Category**: configuration

## Overview

This document describes how to configure accurate memory estimates for models, especially when using quantization (int8, int4, GPTQ, AWQ, etc.).

## Model Configuration Database

The memory estimation script (`configure-vllm-model-routing.sh`) uses a model configuration database to provide accurate memory estimates. Each model entry includes:

- **Parameters**: Billions of parameters
- **Precision**: fp32, fp16, bf16, int8, int4
- **Quantization**: none, gptq, awq, bitsandbytes, gguf
- **Actual GPU Size**: Measured/verified GPU memory usage (GB)
- **Notes**: Additional information about the model

## Current Model Configurations

### Microsoft Phi-4 Multimodal Instruct
- **Parameters**: 6B
- **Precision**: FP16/BF16
- **Quantization**: None
- **GPU Size**: ~12GB
- **Notes**: Standard FP16, no quantization

### Qwen3-Embedding-8B
- **Parameters**: 8B
- **Precision**: FP16 (verify if quantized)
- **Quantization**: None (verify actual deployment)
- **GPU Size**: ~16GB
- **Notes**: May be quantized - check vLLM service config

### Qwen3-30B-A3B-Instruct-2507
- **Parameters**: 30B
- **Precision**: FP16 (verify if quantized)
- **Quantization**: None (verify actual deployment)
- **GPU Size**: ~60GB (FP16) or ~15GB (int4) or ~30GB (int8)
- **Notes**: **A3B suggests optimization - verify quantization!**
  - If quantized to int4: ~15GB GPU
  - If quantized to int8: ~30GB GPU
  - If FP16: ~60GB GPU (requires tensor parallelism)

### Qwen3-VL-8B-Instruct
- **Parameters**: 8B
- **Precision**: FP16
- **Quantization**: None
- **GPU Size**: ~16GB
- **Notes**: Vision-language model, standard FP16

### ColPali v1.3
- **Parameters**: 3B (PaliGemma base)
- **Precision**: BF16
- **Quantization**: None
- **GPU Size**: ~15GB
- **Notes**: LoRA adapter on PaliGemma-3B base, BF16

## Updating Model Configuration

To update model configurations in `configure-vllm-model-routing.sh`:

1. **Find the MODEL_CONFIG array** (around line 53)
2. **Add or update entry**:
   ```bash
   ["Model/Path"]="params|precision|quantization|gpu_size_gb|notes"
   ```

### Example: Adding Quantized Qwen3-30B

If you discover Qwen3-30B is quantized to int4:

```bash
["Qwen/Qwen3-30B-A3B-Instruct-2507"]="30|int4|gptq|15|30B params, GPTQ int4 quantized, ~15GB GPU"
```

### Example: Adding Quantized Qwen3-Embedding

If Qwen3-Embedding is quantized to int8:

```bash
["Qwen/Qwen3-Embedding-8B"]="8|int8|awq|8|8B params, AWQ int8 quantized, ~8GB GPU"
```

## Verifying Model Configuration

### Check vLLM Service Configuration

```bash
# SSH to vLLM container
ssh root@10.96.200.208

# Check vLLM service file
cat /etc/systemd/system/vllm.service | grep -E "model|quantization|dtype"

# Check vLLM logs for model loading
journalctl -u vllm -n 100 | grep -i "model\|quantization\|dtype"
```

### Check Model Files

```bash
# Check HuggingFace cache
ls -lh /var/lib/llm-models/huggingface/hub/models--*/snapshots/*/

# Look for quantization files:
# - GPTQ: *.safetensors (check file sizes)
# - AWQ: *.awq files
# - GGUF: *.gguf files
```

### Check GPU Memory Usage

```bash
# Monitor GPU memory while model loads
watch -n 1 nvidia-smi

# Check actual memory usage after model loads
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

## Quantization Impact on Memory

| Precision | Bytes per Parameter | 30B Model Size |
|-----------|-------------------|----------------|
| FP32      | 4 bytes           | 120 GB         |
| FP16/BF16 | 2 bytes           | 60 GB          |
| INT8      | 1 byte            | 30 GB          |
| INT4      | 0.5 bytes         | 15 GB          |

**Note**: Quantization reduces model weights but KV cache remains FP16/BF16.

## Memory Estimation Formula

The script uses:

```
GPU Memory = Model Weights + Hot KV Cache + Activations + Overhead
RAM Memory = Cold KV Cache (offloaded)

Model Weights = Parameters × Bytes per Parameter / Tensor Parallelism
Hot KV Cache = Total KV Cache × 15% (active requests)
Cold KV Cache = Total KV Cache × 85% (offloaded to RAM)
```

## Common Quantization Methods

### GPTQ
- **Format**: Quantized safetensors files
- **Typical**: INT4 or INT8
- **vLLM Support**: Yes (with `--quantization gptq`)

### AWQ
- **Format**: `.awq` files
- **Typical**: INT4 or INT8
- **vLLM Support**: Yes (with `--quantization awq`)

### BitsAndBytes
- **Format**: Runtime quantization
- **Typical**: INT8 or INT4
- **vLLM Support**: Limited (check vLLM version)

### GGUF
- **Format**: `.gguf` files
- **Typical**: INT4, INT5, INT8
- **vLLM Support**: No (use Ollama instead)

## Updating Configuration Script

After verifying your model quantization:

1. **Update MODEL_CONFIG** in `configure-vllm-model-routing.sh`
2. **Test memory estimation**:
   ```bash
   bash configure-vllm-model-routing.sh --interactive
   ```
3. **Verify estimates match actual GPU usage**

## References

- [vLLM Quantization Docs](https://docs.vllm.ai/en/latest/serving/quantization.html)
- [Qwen Quantization Guide](https://qwen.readthedocs.io/en/v2.0/benchmark/quantization_benchmark.html)
- Model registry: `provision/ansible/group_vars/all/model_registry.yml`

