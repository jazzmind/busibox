# Model Registry Implementation - Cross-Service

## Overview

Implement model purpose registry across all services (busibox, ai-portal, agent-server, agent-client) to:
- Abstract model purposes from actual model names
- Enable easy model swapping via configuration
- Centralize model management at Ansible level
- Support environment-specific model choices

## Status: IN PROGRESS

### Completed ✅
1. **Busibox Ingest Service** ✅
   - Created Ansible vars: `group_vars/all/model_registry.yml`
   - Created deployment task: `roles/ingest/tasks/model_registry.yml`
   - Created template: `roles/ingest/templates/model_registry.json.j2`
   - Updated Python registry: `srv/ingest/src/shared/model_registry.py`
   - Added LLM cleanup processor: `srv/ingest/src/processors/llm_cleanup.py`
   - Updated env template: `roles/ingest/templates/ingest.env.j2`

### Remaining Tasks

## 1. AI Portal Model Registry

**Current State**:
- `src/lib/litellm.ts` - Fetches models from liteLLM dynamically ✅ (Good pattern!)
- `src/app/api/videos/title/route.ts` - Hardcoded `gpt-4o-mini` ❌

**Implementation**:

### A. Create Model Purpose Helper

```typescript
// ai-portal/src/lib/model-purposes.ts

export type ModelPurpose = 
  | 'chat'           // General conversation
  | 'analysis'       // Data analysis
  | 'research'       // Research tasks
  | 'calculation'    // Math/reasoning
  | 'title'          // Title generation
  | 'summary'        // Summarization
  | 'classification' // Classification
  | 'embedding'      // Embeddings
  | 'reranking';     // Search reranking

interface ModelPurposeConfig {
  model: string;
  temperature?: number;
  max_tokens?: number;
  description: string;
}

// Load from environment or fallback to defaults
const MODEL_PURPOSES: Record<ModelPurpose, ModelPurposeConfig> = {
  chat: {
    model: process.env.MODEL_CHAT || 'qwen3-30b',
    temperature: 0.7,
    max_tokens: 4096,
    description: 'General chat and Q&A'
  },
  title: {
    model: process.env.MODEL_TITLE || 'phi-4',
    temperature: 0.7,
    max_tokens: 20,
    description: 'Title generation'
  },
  analysis: {
    model: process.env.MODEL_ANALYSIS || 'qwen-3',
    temperature: 0.5,
    max_tokens: 8192,
    description: 'Data analysis'
  },
  // ... other purposes
};

export function getModelForPurpose(purpose: ModelPurpose): ModelPurposeConfig {
  return MODEL_PURPOSES[purpose];
}
```

### B. Update Video Title Route

```typescript
// ai-portal/src/app/api/videos/title/route.ts

import { getModelForPurpose } from '@/lib/model-purposes';

export async function POST(request: NextRequest) {
  // ...
  const modelConfig = getModelForPurpose('title');
  
  const response = await openai.chat.completions.create({
    model: modelConfig.model,
    temperature: modelConfig.temperature,
    max_tokens: modelConfig.max_tokens,
    // ...
  });
}
```

### C. Add to Ansible Deployment

```yaml
# provision/ansible/inventory/*/group_vars/all/00-main.yml

# AI Portal app config
- name: ai-portal
  # ... existing config ...
  env:
    # ... existing env vars ...
    MODEL_CHAT: "{{ model_purposes.chat.model }}"
    MODEL_TITLE: "{{ model_purposes.title.model }}"
    MODEL_ANALYSIS: "{{ model_purposes.analysis.model }}"
```

## 2. Agent Server Model Registry

**Current State**:
- `src/mastra/config/models.ts` - Hardcoded GPT-5 models ❌
- Uses purpose-based naming (secure, fast, default, best) ✅ (Good pattern!)

**Implementation**:

### A. Update Model Config to Load from Env

```typescript
// agent-server/src/mastra/config/models.ts

export const MODELS = {
  secure: {
    model: process.env.MODEL_SECURE || 'claude-sonnet-4',
    provider: (process.env.MODEL_SECURE_PROVIDER || 'bedrock') as 'openai' | 'anthropic' | 'bedrock' | 'local',
    reasoning: 'minimal' as const,
    text: { verbosity: 'low' }
  } as ModelDef,
  
  fast: {
    model: process.env.MODEL_FAST || 'phi-4',
    provider: (process.env.MODEL_FAST_PROVIDER || 'local') as 'openai' | 'anthropic' | 'bedrock' | 'local',
    reasoning: 'minimal' as const,
    text: { verbosity: 'low' }
  } as ModelDef,
  
  default: {
    model: process.env.MODEL_DEFAULT || 'qwen-2.5-32b',
    provider: (process.env.MODEL_DEFAULT_PROVIDER || 'local') as 'openai' | 'anthropic' | 'bedrock' | 'local',
    reasoning: 'minimal' as const,
    text: { verbosity: 'medium' }
  } as ModelDef,
  
  best: {
    model: process.env.MODEL_BEST || 'qwen-2.5-72b',
    provider: (process.env.MODEL_BEST_PROVIDER || 'local') as 'openai' | 'anthropic' | 'bedrock' | 'local',
    reasoning: 'medium' as const,
    text: { verbosity: 'high' }
  } as ModelDef,
  
  smartest: {
    model: process.env.MODEL_SMARTEST || 'deepseek-r1',
    provider: (process.env.MODEL_SMARTEST_PROVIDER || 'local') as 'openai' | 'anthropic' | 'bedrock' | 'local',
    reasoning: 'high' as const,
    text: { verbosity: 'high' }
  } as ModelDef,
};
```

### B. Add to Ansible Deployment

```yaml
# provision/ansible/group_vars/all/model_registry.yml

# Agent Server model purposes
agent_model_purposes:
  secure:
    model: "claude-sonnet-4"
    provider: "bedrock"
  fast:
    model: "phi-4"
    provider: "local"
  default:
    model: "qwen-2.5-32b"
    provider: "local"
  best:
    model: "qwen-2.5-72b"
    provider: "local"
  smartest:
    model: "deepseek-r1"
    provider: "local"
```

```yaml
# provision/ansible/roles/agent/templates/agent.env.j2

# Model configuration
MODEL_SECURE={{ agent_model_purposes.secure.model }}
MODEL_SECURE_PROVIDER={{ agent_model_purposes.secure.provider }}
MODEL_FAST={{ agent_model_purposes.fast.model }}
MODEL_FAST_PROVIDER={{ agent_model_purposes.fast.provider }}
MODEL_DEFAULT={{ agent_model_purposes.default.model }}
MODEL_DEFAULT_PROVIDER={{ agent_model_purposes.default.provider }}
MODEL_BEST={{ agent_model_purposes.best.model }}
MODEL_BEST_PROVIDER={{ agent_model_purposes.best.provider }}
MODEL_SMARTEST={{ agent_model_purposes.smartest.model }}
MODEL_SMARTEST_PROVIDER={{ agent_model_purposes.smartest.provider }}
```

## 3. Agent Client Model Registry

**Current State**: TBD (need to check for hardcoded models)

**Action**: Search for hardcoded model references and update similar to AI Portal

## 4. LiteLLM Config Integration

**Goal**: Ensure all models referenced in registries are configured in liteLLM

**Implementation**:

### A. Update liteLLM Config Template

```yaml
# provision/ansible/roles/litellm/templates/config.yaml.j2

model_list:
{% for purpose, config in model_purposes.items() %}
  # {{ config.description }}
  - model_name: {{ config.model }}
    litellm_params:
      model: {{ config.provider }}/{{ config.model }}
      api_base: {{ litellm_base_url }}
{% endfor %}

{% for purpose, config in agent_model_purposes.items() %}
  # Agent: {{ purpose }}
  - model_name: {{ config.model }}
    litellm_params:
      model: {{ config.provider }}/{{ config.model }}
      api_base: {{ litellm_base_url }}
{% endfor %}
```

## 5. Environment-Specific Overrides

**Goal**: Allow test/production to use different models

**Implementation**:

```yaml
# provision/ansible/inventory/test/group_vars/all/model_overrides.yml

# Test environment uses smaller/faster models
model_purposes:
  chat:
    model: "phi-4"  # Override: Use smaller model in test
  cleanup:
    model: "qwen-2.5-7b"  # Override: Smaller cleanup model
```

```yaml
# provision/ansible/inventory/production/group_vars/all/model_overrides.yml

# Production uses full-size models (no overrides, use defaults)
```

## Implementation Order

1. ✅ **Busibox Ingest** - Complete
2. **AI Portal** - Update video title route, add model-purposes.ts
3. **Agent Server** - Update models.ts to load from env
4. **LiteLLM Config** - Ensure all models configured
5. **Documentation** - Update deployment docs
6. **Testing** - Verify model swapping works

## Testing Strategy

### Test 1: Model Swapping
```bash
# Change model in Ansible vars
vim provision/ansible/group_vars/all/model_registry.yml
# Change chat model from qwen-2.5-72b to phi-4

# Deploy
make ingest

# Verify new model is used
ssh root@ingest-lxc
cat /etc/ingest/model_registry.json | jq '.purposes.chat.model'
# Should show: "phi-4"
```

### Test 2: Environment-Specific Models
```bash
# Deploy to test (should use smaller models)
make test

# Deploy to production (should use full models)
make production

# Verify different models in each environment
```

### Test 3: Service Integration
```bash
# Test AI Portal title generation
curl -X POST http://ai-portal/api/videos/title \
  -d '{"prompt": "Test video about AI"}' \
  -H "Content-Type: application/json"

# Check logs to verify correct model used
```

## Benefits

1. **Easy Model Swapping**: Change models in one place (Ansible vars)
2. **Environment-Specific**: Test uses smaller models, production uses best
3. **Cost Control**: Easily switch to cheaper models
4. **Future-Proof**: New models just need Ansible config update
5. **Consistency**: All services use same model purpose definitions
6. **Documentation**: Model purposes are self-documenting

## Documentation Updates Needed

- [ ] Update deployment docs with model registry info
- [ ] Document how to add new model purposes
- [ ] Document environment-specific overrides
- [ ] Add troubleshooting guide for model issues

## Next Steps

1. Finish AI Portal implementation
2. Update Agent Server
3. Test model swapping
4. Deploy to test environment
5. Validate all services use registry
6. Deploy to production

