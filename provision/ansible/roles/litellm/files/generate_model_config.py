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
    
    model_purposes = registry_data.get('model_purposes', {})
    available_models = registry_data.get('available_models', {})
    model_configs = model_config_data.get('models', {})
    
    # Debug: Show what's in model_configs
    print("DEBUG: model_configs keys: {}".format(list(model_configs.keys())), file=sys.stderr)
    
    for purpose, model_key in model_purposes.items():
        model_entry = available_models.get(model_key, {})
        if not model_entry:
            print("WARNING: No model entry for purpose '{}' with key '{}'".format(purpose, model_key), file=sys.stderr)
            continue
        
        model_name = model_entry.get('model_name', '')
        provider = model_entry.get('provider', '').lower()
        config = model_configs.get(model_name, {})
        config_provider = config.get('provider', '').lower()
        
        # Debug logging
        if purpose == 'reranking':
            print("DEBUG reranking: model_key={}, model_name={}, provider={}".format(model_key, model_name, provider), file=sys.stderr)
            print("DEBUG reranking: config={}".format(config), file=sys.stderr)
            print("DEBUG reranking: assigned={}, port={}".format(config.get('assigned'), config.get('port')), file=sys.stderr)
        
        # Use provider from registry as source of truth
        # But skip if config has a conflicting provider (stale data)
        if config_provider and config_provider != provider:
            print("WARNING: Provider mismatch for {}: registry={}, config={}".format(model_name, provider, config_provider), file=sys.stderr)
            continue
        
        if provider == 'bedrock':
            # Bedrock API model - get credentials from environment
            bedrock_key = os.environ.get('BEDROCK_API_KEY', '')
            aws_region = os.environ.get('BEDROCK_REGION', 'us-east-1')
            aws_access_key = bedrock_key.split(':')[0] if ':' in bedrock_key else ''
            aws_secret_key = bedrock_key.split(':')[1] if ':' in bedrock_key else bedrock_key
            
            models.append({
                'model_name': purpose,
                'litellm_params': {
                    'model': 'bedrock/{}'.format(model_name),
                    'aws_access_key_id': aws_access_key,
                    'aws_secret_access_key': aws_secret_key,
                    'aws_region_name': aws_region
                }
            })
        elif provider == 'vllm' and config.get('assigned', False) and config.get('port'):
            # vLLM model assigned to a port
            vllm_ip = os.environ.get('VLLM_IP', '10.96.200.208')
            models.append({
                'model_name': purpose,
                'litellm_params': {
                    'model': "openai/{}".format(model_name),
                    'api_base': "http://{}:{}/v1".format(vllm_ip, config['port']),
                    'api_key': 'EMPTY'
                }
            })
        # Skip fastembed, colpali, marker, and other non-LiteLLM providers
    
    print(json.dumps(models))

if __name__ == '__main__':
    main()

