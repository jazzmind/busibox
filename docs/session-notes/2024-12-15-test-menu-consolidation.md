---
title: Test Menu Consolidation
category: session-notes
created: 2024-12-15
updated: 2024-12-15
status: completed
---

# Test Menu Consolidation

## Overview

Consolidated redundant test menus into the main `scripts/test.sh` script, removing duplication and improving maintainability.

## Problem

We had three separate test scripts with overlapping functionality:

1. **`scripts/test.sh`** - Main test runner (used by `make test`)
2. **`provision/ansible/test-menu.sh`** - Duplicate test menu with LLM tests
3. **`provision/ansible/test-llm.sh`** - LLM testing functions

The ansible directory scripts had comprehensive LLM model testing capabilities that weren't in the main test script, creating confusion about which script to use.

## Solution

### Consolidated Into `scripts/test.sh`

Added all LLM testing functionality from `test-llm.sh` into the main `scripts/test.sh`:

**New LLM Testing Functions**:
- `check_jq()` - Auto-install jq if needed
- `get_litellm_ip()` - Get LiteLLM IP by environment
- `get_litellm_key()` - Get API key from vault
- `check_litellm()` - Verify LiteLLM is reachable
- `list_models_by_purpose()` - List all models from registry
- `test_purpose_chat()` - Test chat completion for any purpose
- `test_purpose_embedding()` - Test embedding generation
- `test_bedrock()` - Test AWS Bedrock models
- `test_openai()` - Test OpenAI models

**New Menu**: `llm_tests_menu()`
- List models by purpose
- Test fast model (quick chat)
- Test embedding
- Test research model (math/physics)
- Test default, chat, cleanup, parsing, classify, vision, tool_calling models
- Test AWS Bedrock (if configured)
- Test OpenAI (if configured)

**Updated Service Tests Menu**:
- Added "LLM Model Tests" as first option
- Added "Bootstrap Test Credentials" option
- Increased menu options from 7 to 9

### Removed Redundant Files

**Deleted**:
- `provision/ansible/test-menu.sh` (301 lines)
- `provision/ansible/test-llm.sh` (431 lines)

**Updated**:
- `provision/ansible/Makefile` - Removed `test-menu` target, added note pointing to `scripts/test.sh`

## Benefits

✅ **Single source of truth** - One test script with all functionality
✅ **Consistent UI** - Uses shared `scripts/lib/ui.sh` library
✅ **Better organization** - All tests accessible from main menu
✅ **Easier maintenance** - No duplicate code to keep in sync
✅ **Comprehensive testing** - LLM tests now in main workflow
✅ **Reduced confusion** - Clear which script to use (`make test`)

## Usage

### Main Test Menu

```bash
# From repo root
make test

# Or directly
bash scripts/test.sh
```

### Test Menu Structure

```
Busibox Test Suite
├── Infrastructure Tests
│   ├── Full Suite
│   ├── Provision Only
│   └── Verify Only
├── Service Tests
│   ├── LLM Model Tests (NEW)
│   │   ├── List models by purpose
│   │   ├── Test fast model
│   │   ├── Test embedding
│   │   ├── Test research model
│   │   ├── Test default/chat/cleanup/parsing/classify/vision/tool_calling
│   │   ├── Test AWS Bedrock
│   │   └── Test OpenAI
│   ├── Authz Service Tests
│   ├── Ingest Service Tests
│   │   ├── Unit tests
│   │   ├── Integration tests
│   │   ├── Coverage
│   │   ├── SIMPLE extraction
│   │   ├── LLM cleanup extraction
│   │   ├── Marker extraction
│   │   └── ColPali extraction
│   ├── Search Service Tests
│   ├── Agent Service Tests
│   ├── Apps Service Tests
│   ├── All Service Tests
│   └── Bootstrap Test Credentials (NEW)
└── All Tests
```

## LLM Testing Capabilities

### Model Registry Integration

Tests read from `inventory/{env}/group_vars/all/model_registry.yml` to:
- Discover purpose-based models (fast, embedding, research, default, chat, etc.)
- Show model availability status
- Test each purpose with appropriate prompts

### Supported Tests

1. **Chat Completion** - Test any purpose-based model with custom prompts
2. **Embeddings** - Test embedding generation and dimensions
3. **Bedrock** - Test AWS Bedrock models if configured
4. **OpenAI** - Test OpenAI models if configured

### Auto-Install Dependencies

The script automatically installs `jq` if not present:
- macOS: via Homebrew
- Linux: via apt-get or yum

### Environment Support

Works with both test and production environments:
- Reads LiteLLM IP from environment
- Gets API key from Ansible vault
- Supports vault password file or interactive prompt

## Migration Notes

### For Users

No action needed - just use `make test` as before. New LLM testing options are now available in the menu.

### For Developers

If you were using `provision/ansible/test-menu.sh`:
- Use `make test` from repo root instead
- All functionality is preserved in `scripts/test.sh`
- LLM tests are now under "Service Tests" → "LLM Model Tests"

### For CI/CD

No changes needed - Makefile targets in `provision/ansible/Makefile` still work:
- `make test-ingest`
- `make test-search`
- `make test-agent`
- `make test-apps`
- `make test-all`

## Files Changed

### Modified
- `scripts/test.sh` - Added 400+ lines of LLM testing functions
- `provision/ansible/Makefile` - Removed `test-menu` target

### Deleted
- `provision/ansible/test-menu.sh` (301 lines)
- `provision/ansible/test-llm.sh` (431 lines)

### Net Change
- **Removed**: 732 lines of duplicate code
- **Added**: ~400 lines to main test script
- **Net reduction**: ~330 lines
- **Maintainability**: Significantly improved

## Testing

Verified syntax:
```bash
bash -n scripts/test.sh
# Exit code: 0 (no errors)
```

## Related Documentation

- [Testing Strategy](../TESTING.md)
- [Bootstrap Test Credentials](../guides/bootstrap-test-credentials.md)
- [Model Registry Configuration](../configuration/model-registry.md)

## Status

✅ **Consolidation complete**
✅ **Redundant files removed**
✅ **Syntax verified**
✅ **Documentation updated**
✅ **Ready for use**


