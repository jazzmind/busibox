#!/usr/bin/env python3
"""
Generate LiteLLM model configuration from model_registry.yml and model_config.yml
"""
import json
import os
import sys
import yaml
from pathlib import Path

def main():
    # Load files from environment variables
    config_file = Path(os.environ.get('MODEL_CONFIG_FILE', ''))
    registry_file = Path(os.environ.get('MODEL_REGISTRY_FILE', ''))
    
    models = []
    
    if not config_file.exists():
        print(json.dumps(models))
        return
    
    with open(config_file, 'r') as f:
        model_config_data = yaml.safe_load(f) or {}
    
    with open(registry_file, 'r') as f:
        registry_data = yaml.safe_load(f) or {}
    
    default_purposes = registry_data.get('default_purposes', {})
    registry_purposes = registry_data.get('model_purposes', {})
    available_models = registry_data.get('available_models', {})
    model_configs = model_config_data.get('models', {})

    # model_config.yml may contain CLI-overridden purposes (from the TUI role editor).
    # These take priority over model_registry.yml defaults.
    config_purposes = model_config_data.get('model_purposes', {})

    # Merge: defaults < registry < model_config overrides
    merged = dict(default_purposes)
    merged.update(registry_purposes)
    if config_purposes:
        merged.update(config_purposes)
        print("INFO: Using purpose overrides from model_config.yml ({} entries)".format(len(config_purposes)), file=sys.stderr)

    def resolve_alias(key, purposes, models, depth=0):
        """Follow alias chain until we hit a concrete model key."""
        val = purposes.get(key, key)
        if depth > 10:
            return val
        if val in purposes and val not in models:
            return resolve_alias(val, purposes, models, depth + 1)
        return val

    model_purposes = {}
    for purpose in merged:
        model_purposes[purpose] = resolve_alias(purpose, merged, available_models)
    
    # Debug: Show what's in model_configs
    print("DEBUG: model_configs keys: {}".format(list(model_configs.keys())), file=sys.stderr)
    print("DEBUG: resolved purposes: {}".format(model_purposes), file=sys.stderr)
    
    # Purposes that should NOT be served through LiteLLM
    # These have dedicated services with specialized APIs
    excluded_purposes = {
        'embedding',         # FastEmbed service (dedicated embedding endpoint)
    }
    
    for purpose, model_key in model_purposes.items():
        # Skip non-chat purposes
        if purpose in excluded_purposes:
            print("INFO: Skipping purpose '{}' - served by dedicated service, not LiteLLM".format(purpose), file=sys.stderr)
            continue
        model_entry = available_models.get(model_key, {})
        if not model_entry:
            print("WARNING: No model entry for purpose '{}' with key '{}'".format(purpose, model_key), file=sys.stderr)
            continue
        
        model_name = model_entry.get('model_name', '')
        provider = model_entry.get('provider', '').lower()
        # model_config.yml is keyed by model_key
        config = model_configs.get(model_key, {})
        
        # Fallback: search by model_key field inside entries (backward compat)
        if not config:
            for cfg_entry_key, cfg_entry in model_configs.items():
                if cfg_entry.get('model_key') == model_key:
                    config = cfg_entry
                    print("DEBUG: Matched purpose '{}' to config entry '{}' via model_key '{}'".format(
                        purpose, cfg_entry_key, model_key), file=sys.stderr)
                    break
        
        config_provider = config.get('provider', '').lower()
        
        # Debug logging for chat purposes
        print("DEBUG: Processing purpose '{}' -> model_key='{}', provider='{}'".format(purpose, model_key, provider), file=sys.stderr)
        
        # Use provider from registry as source of truth
        # But skip if config has a conflicting provider (stale data)
        if config_provider and config_provider != provider:
            print("WARNING: Provider mismatch for {}: registry={}, config={}".format(model_name, provider, config_provider), file=sys.stderr)
            continue
        
        if provider == 'bedrock':
            # Bedrock API model - get credentials from environment
            bedrock_key = os.environ.get('AWS_BEARER_TOKEN_BEDROCK', '')
            aws_region = os.environ.get('AWS_REGION_BEDROCK', 'us-east-1')
            aws_access_key = bedrock_key.split(':')[0] if ':' in bedrock_key else ''
            aws_secret_key = bedrock_key.split(':')[1] if ':' in bedrock_key else ''
            
            models.append({
                'model_name': purpose,
                'litellm_params': {
                    'model': 'bedrock/{}'.format(model_name),
                    'aws_bearer_token_bedrock': bedrock_key,
                    'aws_access_key_id': aws_access_key,
                    'aws_secret_access_key': aws_secret_key,
                    'aws_region_name': aws_region
                }
            })
        elif provider == 'vllm' and config.get('assigned', False) and config.get('port'):
            # vLLM model assigned to a port
            vllm_ip = os.environ.get('VLLM_IP', '10.96.200.208')
            # Use served_model_name if set (multiple instances of same HF model),
            # otherwise fall back to the HF model_name
            served_name = config.get('served_model_name', '') or model_name
            models.append({
                'model_name': purpose,
                'litellm_params': {
                    'model': "openai/{}".format(served_name),
                    'api_base': "http://{}:{}/v1".format(vllm_ip, config['port']),
                    'api_key': 'EMPTY'
                }
            })
        elif provider == 'gpu' and model_entry.get('port'):
            # GPU media service (on-demand systemd, OpenAI-compatible API)
            vllm_ip = os.environ.get('VLLM_IP', '10.96.200.208')
            models.append({
                'model_name': purpose,
                'litellm_params': {
                    'model': "openai/{}".format(model_name),
                    'api_base': "http://{}:{}/v1".format(vllm_ip, model_entry['port']),
                    'api_key': 'EMPTY'
                }
            })
        # Skip fastembed, colpali, marker, and other non-LiteLLM providers
    
    print(json.dumps(models))

if __name__ == '__main__':
    main()

