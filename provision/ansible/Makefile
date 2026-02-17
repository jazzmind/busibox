.PHONY: ping all files pg milvus neo4j nginx search search-api agent data data-api data-worker data-update-tests docs apps user-apps openwebui deploy-apps deploy-busibox-portal deploy-busibox-agents deploy-app-ref verify verify-health verify-smoke service-start service-stop service-restart service-status service-logs service-health test test-menu test-data test-data-all test-data-coverage test-extraction-simple test-extraction-llm test-extraction-marker test-extraction-colpali test-search test-search-unit test-search-integration test-search-coverage test-agent test-authz test-apps test-security test-security-auth test-security-injection test-security-fuzz test-all configure generate-token-keys bootstrap-test-creds bridge tailscale test-bridge check-embeddings migrate-embeddings migrate-embeddings-force staging production env core core-nginx core-storage core-database core-vectorstore core-graph llm llm-vllm llm-litellm llm-colpali apis apis-authz apis-data apis-search apis-agent apis-docs apis-bridge apps-frontend

# ============================================================================
# ENVIRONMENT SELECTION (persists between runs via .busibox-state)
# ============================================================================
# Usage:
#   make staging      - Switch to staging environment (persisted)
#   make production   - Switch to production environment (persisted)
#   make env          - Show current environment
#   make <target> INV=inventory/staging  - One-time override (not persisted)
#
# Environment is shared with the interactive menu (scripts/make/menu.sh)
# via the .busibox-state file in the repository root.

# Read environment from .busibox-state (shared with menu UI)
# Format in .busibox-state: ENVIRONMENT=staging or ENVIRONMENT=production
BUSIBOX_STATE_FILE := $(shell cd .. && pwd)/.busibox-state
SAVED_ENV := $(shell grep '^ENVIRONMENT=' "$(BUSIBOX_STATE_FILE)" 2>/dev/null | cut -d= -f2)

# Convert saved environment to inventory path
ifeq ($(SAVED_ENV),staging)
  INV ?= inventory/staging
else ifeq ($(SAVED_ENV),production)
  INV ?= inventory/production
else
  # Default to production if no saved environment
  INV ?= inventory/production
endif

# Vault password file - environment-specific
# Automatically uses the correct password file based on inventory
# Expected locations:
#   ~/.busibox-vault-pass-prod for production
#   ~/.busibox-vault-pass-staging for staging
#   ~/.busibox-vault-pass-demo for demo
#   ~/.busibox-vault-pass-dev for development
# Fallback: ~/.vault_pass (legacy)
ENV_NAME=$(shell basename $(INV))
ifeq ($(ENV_NAME),production)
  VAULT_PASS_FILE=~/.busibox-vault-pass-prod
else ifeq ($(ENV_NAME),staging)
  VAULT_PASS_FILE=~/.busibox-vault-pass-staging
else ifeq ($(ENV_NAME),demo)
  VAULT_PASS_FILE=~/.busibox-vault-pass-demo
else
  VAULT_PASS_FILE=~/.busibox-vault-pass-dev
endif

# Check if vault password file exists, fallback to legacy location
VAULT_FLAGS=$(shell \
  if [ -f $(VAULT_PASS_FILE) ]; then \
    echo "--vault-password-file $(VAULT_PASS_FILE)"; \
  elif [ -f ~/.vault_pass ]; then \
    echo "--vault-password-file ~/.vault_pass"; \
  else \
    echo "ERROR: Vault password file not found at $(VAULT_PASS_FILE) or ~/.vault_pass" >&2; \
    echo "--vault-password-file $(VAULT_PASS_FILE)"; \
  fi \
)

# Extra arguments (can override with: make vllm-embedding EXTRA_ARGS="-e skip_model_check=true")
EXTRA_ARGS=

# Ansible output formatting
# Use default callback with result_format=yaml for readable output
export ANSIBLE_STDOUT_CALLBACK=default
export ANSIBLE_CALLBACK_RESULT_FORMAT=yaml
# Don't show skipped hosts - reduces noise
export ANSIBLE_DISPLAY_SKIPPED_HOSTS=false

# Container IPs - production uses 10.96.200.x, staging uses 10.96.201.x
# For production, IPs are static and defined in group_vars
# For staging, they use the TEST network (10.96.201.x)
ifeq ($(INV),inventory/staging)
  # Staging IPs (10.96.201.0/21 network)
  PROXY_IP = 10.96.201.200
  DATA_IP = 10.96.201.206
  AGENT_IP = 10.96.201.202
  CORE_APPS_IP = 10.96.201.201
  USER_APPS_IP = 10.96.201.212
  MILVUS_IP = 10.96.201.204
  AUTHZ_IP = 10.96.201.210
else
  # Production IPs (10.96.200.0/21 network)
  PROXY_IP = 10.96.200.200
  DATA_IP = 10.96.200.206
  AGENT_IP = 10.96.200.202
  CORE_APPS_IP = 10.96.200.201
  USER_APPS_IP = 10.96.200.212
  MILVUS_IP = 10.96.200.204
  AUTHZ_IP = 10.96.200.210
endif

# Environment switching targets (updates .busibox-state shared with menu UI)
staging:
	@if [ -f "$(BUSIBOX_STATE_FILE)" ]; then \
		if grep -q '^ENVIRONMENT=' "$(BUSIBOX_STATE_FILE)"; then \
			sed -i.bak 's/^ENVIRONMENT=.*/ENVIRONMENT=staging/' "$(BUSIBOX_STATE_FILE)" && rm -f "$(BUSIBOX_STATE_FILE).bak"; \
		else \
			echo "ENVIRONMENT=staging" >> "$(BUSIBOX_STATE_FILE)"; \
		fi; \
	else \
		echo "ENVIRONMENT=staging" > "$(BUSIBOX_STATE_FILE)"; \
	fi
	@echo "Switched to STAGING environment (10.96.201.x network)"
	@echo "This setting is now persisted. Run 'make env' to verify."

production:
	@if [ -f "$(BUSIBOX_STATE_FILE)" ]; then \
		if grep -q '^ENVIRONMENT=' "$(BUSIBOX_STATE_FILE)"; then \
			sed -i.bak 's/^ENVIRONMENT=.*/ENVIRONMENT=production/' "$(BUSIBOX_STATE_FILE)" && rm -f "$(BUSIBOX_STATE_FILE).bak"; \
		else \
			echo "ENVIRONMENT=production" >> "$(BUSIBOX_STATE_FILE)"; \
		fi; \
	else \
		echo "ENVIRONMENT=production" > "$(BUSIBOX_STATE_FILE)"; \
	fi
	@echo "Switched to PRODUCTION environment (10.96.200.x network)"
	@echo "This setting is now persisted. Run 'make env' to verify."

env:
	@echo "============================================"
	@echo "Current Environment Configuration"
	@echo "============================================"
	@echo "Inventory: $(INV)"
	@if [ "$(INV)" = "inventory/staging" ]; then \
		echo "Network:   STAGING (10.96.201.x)"; \
		echo ""; \
		echo "  Data:     $(DATA_IP)"; \
		echo "  Agent:      $(AGENT_IP)"; \
		echo "  Core Apps:  $(CORE_APPS_IP)"; \
		echo "  User Apps:  $(USER_APPS_IP)"; \
		echo "  Milvus:     $(MILVUS_IP)"; \
		echo "  AuthZ:      $(AUTHZ_IP)"; \
	else \
		echo "Network:   PRODUCTION (10.96.200.x)"; \
		echo ""; \
		echo "  Data:     $(DATA_IP)"; \
		echo "  Agent:      $(AGENT_IP)"; \
		echo "  Core Apps:  $(CORE_APPS_IP)"; \
		echo "  User Apps:  $(USER_APPS_IP)"; \
		echo "  Milvus:     $(MILVUS_IP)"; \
		echo "  AuthZ:      $(AUTHZ_IP)"; \
	fi
	@echo "============================================"
	@echo "State file: $(BUSIBOX_STATE_FILE)"
	@if [ -f "$(BUSIBOX_STATE_FILE)" ]; then \
		echo "Saved env:  $(SAVED_ENV)"; \
	else \
		echo "Saved env:  (none - using default)"; \
	fi

# ============================================================================
# FULL DEPLOYMENT
# ============================================================================

ping:
	ansible -i $(INV) all -m ping

all:
	ansible-playbook -i $(INV) site.yml $(VAULT_FLAGS)

# ============================================================================
# GROUP DEPLOYMENTS
# ============================================================================
# Deploy by group - running groups in order is equivalent to 'make all':
#   make core && make llm && make apis && make apps-frontend
#
# Group 1: CORE (nginx, minio, postgres, milvus) - infrastructure
# Group 2: LLM (vllm, litellm, colpali) - AI/ML services  
# Group 3: APIs (authz, data, search, agent, docs, bridge) - application services
# Group 4: APPS (busibox-portal, busibox-agents, openwebui) - frontend applications

# ----------------------------------------------------------------------------
# Group 1: Core Services (infrastructure)
# Deploy first - nginx enables web-driven install recovery
# ----------------------------------------------------------------------------

core:
	@echo "=============================================="
	@echo "Deploying CORE services (nginx, minio, pg, milvus)"
	@echo "=============================================="
	ansible-playbook -i $(INV) site.yml --tags core $(VAULT_FLAGS)

core-nginx:
	@echo "Deploying nginx (web proxy)..."
	ansible-playbook -i $(INV) -l proxy site.yml --tags core_nginx $(VAULT_FLAGS)

core-storage:
	@echo "Deploying MinIO (object storage)..."
	ansible-playbook -i $(INV) -l files site.yml --tags core_storage $(VAULT_FLAGS)

core-database:
	@echo "Deploying PostgreSQL (database)..."
	ansible-playbook -i $(INV) -l pg site.yml --tags core_database $(VAULT_FLAGS)

core-vectorstore:
	@echo "Deploying Milvus (vector database)..."
	ansible-playbook -i $(INV) -l milvus site.yml --tags core_vectorstore $(VAULT_FLAGS)

core-graph:
	@echo "Deploying Neo4j (graph database)..."
	ansible-playbook -i $(INV) -l neo4j site.yml --tags core_graph $(VAULT_FLAGS)

# ----------------------------------------------------------------------------
# Group 2: LLM Services (AI/ML)
# GPU inference, embedding, and LLM gateway
# ----------------------------------------------------------------------------

llm:
	@echo "=============================================="
	@echo "Deploying LLM services (vllm, litellm, colpali)"
	@echo "=============================================="
	ansible-playbook -i $(INV) site.yml --tags llm $(VAULT_FLAGS) $(EXTRA_ARGS)

llm-vllm:
	@echo "Deploying vLLM (GPU inference)..."
	ansible-playbook -i $(INV) -l vllm site.yml --tags llm_vllm $(VAULT_FLAGS) $(EXTRA_ARGS)

llm-litellm:
	@echo "Deploying LiteLLM (LLM gateway)..."
	ansible-playbook -i $(INV) -l litellm site.yml --tags llm_litellm $(VAULT_FLAGS)

llm-colpali:
	@echo "Deploying ColPali (visual embeddings)..."
	ansible-playbook -i $(INV) -l vllm site.yml --tags llm_colpali $(VAULT_FLAGS) $(EXTRA_ARGS)

# ----------------------------------------------------------------------------
# Group 3: APIs (application services)
# Application services that depend on core + llm
# ----------------------------------------------------------------------------

apis:
	@echo "=============================================="
	@echo "Deploying API services (authz, data, search, agent, docs)"
	@echo "=============================================="
	ansible-playbook -i $(INV) site.yml --tags apis $(VAULT_FLAGS)

apis-authz:
	@echo "Deploying AuthZ (authentication)..."
	ansible-playbook -i $(INV) -l authz site.yml --tags apis_authz $(VAULT_FLAGS)

apis-data:
	@echo "Deploying Data (file processing)..."
	ansible-playbook -i $(INV) -l data site.yml --tags apis_data $(VAULT_FLAGS)

apis-search:
	@echo "Deploying Search API..."
	ansible-playbook -i $(INV) -l milvus site.yml --tags apis_search $(VAULT_FLAGS)

apis-agent:
	@echo "Deploying Agent API..."
	ansible-playbook -i $(INV) -l agent site.yml --tags apis_agent $(VAULT_FLAGS)

apis-docs:
	@echo "Deploying Docs API..."
	ansible-playbook -i $(INV) -l agent site.yml --tags docs_api $(VAULT_FLAGS)

apis-bridge:
	@echo "Deploying Bridge (multi-channel communication)..."
	ansible-playbook -i $(INV) -l bridge site.yml --tags apis_bridge $(VAULT_FLAGS) || true

# ----------------------------------------------------------------------------
# Group 4: Apps (frontend applications)
# Frontend applications (busibox-portal, busibox-agents)
# ----------------------------------------------------------------------------

apps-frontend:
	@echo "=============================================="
	@echo "Deploying frontend apps (busibox-portal, busibox-agents)"
	@echo "=============================================="
	ansible-playbook -i $(INV) site.yml --tags apps $(VAULT_FLAGS)

# ============================================================================
# INDIVIDUAL SERVICE TARGETS (backward compatible)
# ============================================================================
# These targets use the new group tags internally for consistency

files:
	ansible-playbook -i $(INV) -l files site.yml --tags core_storage $(VAULT_FLAGS)

pg:
	ansible-playbook -i $(INV) -l pg site.yml --tags core_database $(VAULT_FLAGS)

authz:
	ansible-playbook -i $(INV) -l authz site.yml --tags authz $(VAULT_FLAGS)

deploy-api:
	ansible-playbook -i $(INV) -l authz site.yml --tags deploy_api $(VAULT_FLAGS)

litellm:
	ansible-playbook -i $(INV) -l litellm site.yml --tags llm_litellm $(VAULT_FLAGS)

vllm:
	ansible-playbook -i $(INV) -l vllm site.yml --tags llm_vllm $(VAULT_FLAGS) $(EXTRA_ARGS)

vllm-embedding:
	ansible-playbook -i $(INV) -l vllm site.yml --tags vllm_embedding $(VAULT_FLAGS) $(EXTRA_ARGS)

colpali:
	ansible-playbook -i $(INV) -l vllm site.yml --tags llm_colpali $(VAULT_FLAGS) $(EXTRA_ARGS)

milvus:
	ansible-playbook -i $(INV) -l milvus site.yml --tags core_vectorstore $(VAULT_FLAGS)

neo4j:
	ansible-playbook -i $(INV) -l neo4j site.yml --tags core_graph $(VAULT_FLAGS)

nginx:
	ansible-playbook -i $(INV) -l proxy site.yml --tags core_nginx $(VAULT_FLAGS)

search: milvus search-api
	@echo "================================"
	@echo "Search services deployed!"
	@echo "  - Milvus (vector database)"
	@echo "  - Search API (hybrid search)"
	@echo "================================"

search-api:
	ansible-playbook -i $(INV) -l milvus site.yml --tags apis_search $(VAULT_FLAGS)

agent:
	ansible-playbook -i $(INV) -l agent site.yml --tags apis_agent $(VAULT_FLAGS)

data:
	ansible-playbook -i $(INV) -l data site.yml --tags apis_data $(VAULT_FLAGS)

# Redis - Install Redis on data container (prerequisite for data services)
redis:
	@echo "Installing Redis on data container..."
	ansible-playbook -i $(INV) -l data site.yml --tags redis $(VAULT_FLAGS)

embedding-api:
	ansible-playbook -i $(INV) -l data site.yml --tags embedding_api $(VAULT_FLAGS)

data-api:
	ansible-playbook -i $(INV) -l data site.yml --tags data_api $(VAULT_FLAGS)

data-worker:
	ansible-playbook -i $(INV) -l data site.yml --tags data_worker $(VAULT_FLAGS)

data-update-tests:
	@echo "Updating data test files..."
	ansible-playbook -i $(INV) -l data site.yml --tags data_update_tests $(VAULT_FLAGS)
	@echo "✓ Test files updated"

# Docs API - Documentation service (runs on authz container)
docs:
	ansible-playbook -i $(INV) -l agent site.yml --tags docs_api $(VAULT_FLAGS)

apps:
	ansible-playbook -i $(INV) -l apps site.yml --tags apps $(VAULT_FLAGS)

# User Apps container - provisions user-apps-lxc with Node.js, git, etc.
# for runtime deployment of external/user apps via deploy-api
user-apps:
	ansible-playbook -i $(INV) -l user_apps site.yml --tags user_apps $(VAULT_FLAGS)

openwebui:
	ansible-playbook -i $(INV) -l openwebui site.yml --tags apps_openwebui $(VAULT_FLAGS)

# Bridge Service (multi-channel communication: Signal, Email, WhatsApp, Webhooks)
# Note: This is optional and non-blocking - won't fail if bridge host doesn't exist
bridge:
	ansible-playbook -i $(INV) -l bridge site.yml --tags apis_bridge $(VAULT_FLAGS) || true

# Internal DNS - Updates /etc/hosts on all containers
# Run this after adding new services or changing IPs
internal-dns:
	@echo "Updating internal DNS (/etc/hosts) on all containers..."
	ansible-playbook -i $(INV) site.yml --tags internal_dns $(VAULT_FLAGS)

# Tailscale VPN (for secure remote access)
# NOTE: This is a CONFIGURATION step, not part of core deployment
# Run after initial deployment: make tailscale
tailscale:
	ansible-playbook -i $(INV) -l proxy site.yml --tags configure,tailscale $(VAULT_FLAGS)

# Test Bridge service integration
test-bridge:
	@echo "Testing Bridge service integration..."
	@if [ -n "$(BRIDGE_IP)" ]; then \
		ssh root@$(BRIDGE_IP) 'bash -s' < scripts/test-bridge.sh; \
	else \
		echo "Bridge host not configured (BRIDGE_IP not set). Skipping test."; \
	fi

# T029: Deploy applications (runs app_deployer + secrets roles)
deploy-apps:
	ansible-playbook -i $(INV) -l agent,apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS)

# Deploy individual applications (apps container)
deploy-busibox-portal:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=busibox-portal"

deploy-busibox-agents:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=busibox-agents"

# Deploy a specific app from an explicit ref (branch or tag) in Proxmox/LXC environments.
# Uses app_deployer legacy branch flow, which supports arbitrary Git refs via GitHub tarball API.
# Usage:
#   make deploy-app-ref APP=busibox-portal REF=main INV=inventory/staging
#   make deploy-app-ref APP=busibox-agents REF=v1.2.3 INV=inventory/production
deploy-app-ref:
ifndef APP
	@echo "Error: APP is required"
	@echo "Usage: make deploy-app-ref APP=<busibox-portal|busibox-agents> REF=<branch-or-tag> [INV=inventory/staging]"
	@exit 1
endif
ifndef REF
	@echo "Error: REF is required"
	@echo "Usage: make deploy-app-ref APP=<busibox-portal|busibox-agents> REF=<branch-or-tag> [INV=inventory/staging]"
	@exit 1
endif
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=$(APP) deploy_from_branch=true deploy_branch=$(REF) use_deploy_api=false"

# Deploy agent-server (internal API service)
# Usage:
# Note: agent-server (Python) now uses dedicated agent_api role
# Deploy with: make agent INV=inventory/staging

# Configuration targets
configure:
	@bash ../../scripts/make/configure.sh

generate-token-keys:
	@bash scripts/generate-token-service-keys.sh

# Bootstrap test credentials for local integration testing
bootstrap-test-creds:
	@echo "Bootstrapping test credentials for $(INV)..."
	@bash scripts/bootstrap-test-credentials.sh $(shell echo $(INV) | grep -q staging && echo staging || echo production)

# Verification targets
verify: verify-health verify-smoke
	@echo "================================"
	@echo "All verification checks passed!"
	@echo "================================"

verify-health:
	@echo "Running health checks on all services..."
	@echo ""
	@echo "=== Agent API ==="
	@if curl -f -s http://10.96.200.202:8000/health > /dev/null 2>&1; then \
		echo "✓ Agent API is healthy"; \
	else \
		echo "⚠ Agent API is not responding (may not be deployed yet)"; \
	fi
	@echo ""
	@echo "=== Docs API ==="
	@if curl -f -s http://$(AUTHZ_IP):8004/health/live > /dev/null 2>&1; then \
		echo "✓ Docs API is healthy"; \
	else \
		echo "⚠ Docs API is not responding (may not be deployed yet)"; \
	fi
	@echo ""
	@echo "=== PostgreSQL ==="
	@if ssh root@10.96.200.203 'su - postgres -c "psql -c \"SELECT version();\""' > /dev/null 2>&1; then \
		echo "✓ PostgreSQL is healthy"; \
	else \
		echo "✗ PostgreSQL health check FAILED"; \
		exit 1; \
	fi
	@echo ""
	@echo "=== Milvus ==="
	@if curl -f -s http://10.96.200.204:9091/healthz > /dev/null 2>&1; then \
		echo "✓ Milvus is healthy"; \
	else \
		echo "✗ Milvus health check FAILED"; \
		exit 1; \
	fi
	@echo ""
	@echo "=== MinIO ==="
	@if curl -f -s http://10.96.200.205:9000/minio/health/live > /dev/null 2>&1; then \
		echo "✓ MinIO is healthy"; \
	else \
		echo "✗ MinIO health check FAILED"; \
		exit 1; \
	fi
	@echo ""

verify-smoke:
	@echo "Running smoke tests..."
	@echo ""
	@echo "=== Database Schema ==="
	@ssh root@10.96.200.203 'su - postgres -c "psql -d busibox -c \"SELECT tablename FROM pg_tables WHERE schemaname = '"'"'public'"'"' ORDER BY tablename;\""' || (echo "Schema verification FAILED" && exit 1)
	@echo "✓ Database schema verified"
	@echo ""
	@echo "=== AuthZ Migrations ==="
	@ssh root@10.96.200.203 'su - postgres -c "psql -d authz -c \"SELECT version, name, applied_at FROM schema_migrations ORDER BY version;\""' 2>/dev/null || (echo "⚠ AuthZ migrations table not found (service may not be deployed)" && exit 0)
	@echo "✓ AuthZ migrations verified"
	@echo ""

# ============================================================================
# Service Management
# ============================================================================
# Manage services on remote containers (similar to docker commands but uses systemctl)
# Usage:
#   make service-start SERVICE=authz              # Start service
#   make service-stop SERVICE=authz               # Stop service
#   make service-restart SERVICE=authz            # Restart service
#   make service-status SERVICE=authz             # Show status
#   make service-logs SERVICE=authz               # Show logs
#   make service-health SERVICE=authz             # Check health endpoint
#
# Service mapping (automatically determines container IP based on service name)

# Map service names to container IPs and service names
SERVICE_MAP := \
  authz=authz@$(AUTHZ_IP) \
  data-api=data-api@$(DATA_IP) \
  data-worker=data-worker@$(DATA_IP) \
  search-api=search-api@$(MILVUS_IP) \
  agent-api=agent@$(AGENT_IP) \
  milvus=milvus@$(MILVUS_IP) \
  nginx=nginx@$(PROXY_IP) \
  postgresql=postgresql@10.96.200.203 \
  redis=redis@$(DATA_IP)

service-start:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make service-start SERVICE=<service>"
	@echo ""
	@echo "Available services: authz, data-api, data-worker, search-api, agent-api, milvus, nginx, postgresql, redis"
	@exit 1
endif
	$(eval SVC_INFO := $(filter $(SERVICE)=%,$(SERVICE_MAP)))
	$(eval SVC_NAME := $(word 2,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	$(eval SVC_IP := $(word 3,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	@echo "Starting $(SVC_NAME) on $(SVC_IP)..."
	@ssh root@$(SVC_IP) 'systemctl start $(SVC_NAME)'
	@echo "✓ Service started"

service-stop:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make service-stop SERVICE=<service>"
	@exit 1
endif
	$(eval SVC_INFO := $(filter $(SERVICE)=%,$(SERVICE_MAP)))
	$(eval SVC_NAME := $(word 2,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	$(eval SVC_IP := $(word 3,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	@echo "Stopping $(SVC_NAME) on $(SVC_IP)..."
	@ssh root@$(SVC_IP) 'systemctl stop $(SVC_NAME)'
	@echo "✓ Service stopped"

service-restart:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make service-restart SERVICE=<service>"
	@exit 1
endif
	$(eval SVC_INFO := $(filter $(SERVICE)=%,$(SERVICE_MAP)))
	$(eval SVC_NAME := $(word 2,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	$(eval SVC_IP := $(word 3,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	@echo "Restarting $(SVC_NAME) on $(SVC_IP)..."
	@ssh root@$(SVC_IP) 'systemctl restart $(SVC_NAME)'
	@echo "✓ Service restarted"

service-status:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make service-status SERVICE=<service>"
	@exit 1
endif
	$(eval SVC_INFO := $(filter $(SERVICE)=%,$(SERVICE_MAP)))
	$(eval SVC_NAME := $(word 2,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	$(eval SVC_IP := $(word 3,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	@echo "Status for $(SVC_NAME) on $(SVC_IP):"
	@ssh root@$(SVC_IP) 'systemctl status $(SVC_NAME) --no-pager'

service-logs:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make service-logs SERVICE=<service> [LINES=50]"
	@exit 1
endif
	$(eval SVC_INFO := $(filter $(SERVICE)=%,$(SERVICE_MAP)))
	$(eval SVC_NAME := $(word 2,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	$(eval SVC_IP := $(word 3,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	$(eval LOG_LINES := $(if $(LINES),$(LINES),50))
	@echo "Logs for $(SVC_NAME) on $(SVC_IP) (last $(LOG_LINES) lines):"
	@ssh root@$(SVC_IP) 'journalctl -u $(SVC_NAME) -n $(LOG_LINES) --no-pager'

service-health:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make service-health SERVICE=<service>"
	@exit 1
endif
	$(eval SVC_INFO := $(filter $(SERVICE)=%,$(SERVICE_MAP)))
	$(eval SVC_IP := $(word 3,$(subst @, ,$(subst =, ,$(SVC_INFO)))))
	@if [ "$(SERVICE)" = "authz" ]; then \
		curl -f -s http://$(SVC_IP):8010/health/live && echo "✓ $(SERVICE) is healthy"; \
	elif [ "$(SERVICE)" = "data-api" ]; then \
		curl -f -s http://$(SVC_IP):8000/health && echo "✓ $(SERVICE) is healthy"; \
	elif [ "$(SERVICE)" = "search-api" ]; then \
		curl -f -s http://$(SVC_IP):8003/health && echo "✓ $(SERVICE) is healthy"; \
	elif [ "$(SERVICE)" = "agent-api" ]; then \
		curl -f -s http://$(SVC_IP):8000/health && echo "✓ $(SERVICE) is healthy"; \
	elif [ "$(SERVICE)" = "milvus" ]; then \
		curl -f -s http://$(SVC_IP):9091/healthz && echo "✓ $(SERVICE) is healthy"; \
	else \
		echo "Health check not available for $(SERVICE)"; \
	fi

# ============================================================================
# Testing
# ============================================================================
# Test targets - run tests on remote containers
# Note: Interactive test menu is now in scripts/test.sh (run via 'make test' from repo root)

test-all-targets: test-data test-search test-agent-unit test-apps
	@echo "================================"
	@echo "All tests passed!"
	@echo "================================"

test-data:
	@echo "Running data service tests (unit + integration)..."
	@echo "Container: data-lxc ($(DATA_IP))"
	@echo ""
	@if [ -z "$(DATA_IP)" ] || [ "$(DATA_IP)" = " " ]; then \
		echo "ERROR: Could not resolve data-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@echo "=== Data Tests (unit + integration) ==="
	@ssh root@$(DATA_IP) 'data-test all' || (echo "Data tests FAILED" && exit 1)
	@echo "✓ Data tests passed"
	@echo ""

test-data-all:
	@echo "Running all data service tests (including integration)..."
	@echo "Container: data-lxc ($(DATA_IP))"
	@echo ""
	@ssh root@$(DATA_IP) 'data-test all' || (echo "Data integration tests FAILED" && exit 1)
	@echo "✓ All data tests passed"
	@echo ""

test-data-coverage:
	@echo "Running data tests with coverage..."
	@echo "Container: data-lxc ($(DATA_IP))"
	@echo ""
	@ssh root@$(DATA_IP) 'data-test coverage' || (echo "Data tests FAILED" && exit 1)
	@echo "✓ Data tests passed with coverage"
	@echo ""
	@echo "Coverage report: ssh root@$(DATA_IP) and view /srv/data/htmlcov/index.html"
	@echo ""

# Testdocs repository - cloned to server via git (more efficient than copying large files)
# Uses GitHub token from ~/.github_token on the server (deployed by Ansible)
TESTDOCS_REPO = jazzmind/busibox-testdocs
TESTDOCS_DIR = /tmp/testdocs
SAMPLES_DIR = $(TESTDOCS_DIR)/pdf/general

# Helper target to ensure testdocs are available on data server
.PHONY: ensure-testdocs
ensure-testdocs:
	@echo "📦 Checking test documents on server..."
	@if ssh root@$(DATA_IP) '[ -d $(SAMPLES_DIR) ] && [ -f $(SAMPLES_DIR)/doc01_rfp_project_management/source.pdf ]' 2>/dev/null; then \
		echo "✓ Test documents already available, updating..."; \
		ssh root@$(DATA_IP) 'cd $(TESTDOCS_DIR) && git pull --quiet' || true; \
	else \
		echo "📥 Cloning testdocs repository to server..."; \
		echo "   Repo: $(TESTDOCS_REPO)"; \
		echo "   Destination: $(TESTDOCS_DIR)"; \
		if ! ssh root@$(DATA_IP) '\
			TOKEN=""; \
			if [ -f ~/.github_token ]; then \
				TOKEN=$$(cat ~/.github_token); \
				REPO_URL="https://$${TOKEN}@github.com/$(TESTDOCS_REPO).git"; \
			else \
				REPO_URL="https://github.com/$(TESTDOCS_REPO).git"; \
			fi; \
			rm -rf $(TESTDOCS_DIR) && git clone --depth 1 "$${REPO_URL}" $(TESTDOCS_DIR) \
		'; then \
			echo ""; \
			echo "ERROR: Failed to clone testdocs repository"; \
			echo ""; \
			echo "Ensure GitHub token is deployed to data server:"; \
			echo "  1. Run: make data  (deploys ~/.github_token from vault)"; \
			echo "  2. Or manually: ssh root@$(DATA_IP) and create ~/.github_token"; \
			echo ""; \
			exit 1; \
		fi; \
		echo "✓ Test documents cloned successfully"; \
	fi

# Extraction strategy tests
test-extraction-simple: ensure-testdocs
	@echo "=== Testing SIMPLE Extraction Strategy ==="
	@echo "Running basic PDF extraction without LLM cleanup"
	@echo "Container: data-lxc ($(DATA_IP))"
	@echo ""
	@if [ -z "$(DATA_IP)" ] || [ "$(DATA_IP)" = " " ]; then \
		echo "ERROR: Could not resolve data-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@echo ""
	@ssh root@$(DATA_IP) 'cd /srv/data && source venv/bin/activate && SAMPLES_DIR=$(SAMPLES_DIR) python -m pytest tests/test_pdf_extraction_simple.py -v --tb=short --capture=no' || (echo "SIMPLE extraction tests FAILED" && exit 1)
	@echo "✓ SIMPLE extraction tests passed"
	@echo ""

test-extraction-llm: ensure-testdocs
	@echo "=== Testing SIMPLE + LLM Cleanup ==="
	@echo "Running PDF extraction WITH LLM cleanup"
	@echo "Container: data-lxc ($(DATA_IP))"
	@echo ""
	@echo "⚠️  Requires: LiteLLM service running on litellm-lxc"
	@echo ""
	@if [ -z "$(DATA_IP)" ] || [ "$(DATA_IP)" = " " ]; then \
		echo "ERROR: Could not resolve data-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@ssh root@$(DATA_IP) 'cd /srv/data && source venv/bin/activate && SAMPLES_DIR=$(SAMPLES_DIR) python -m pytest tests/test_llm_cleanup.py -v --tb=short --capture=no' || (echo "LLM cleanup tests FAILED" && exit 1)
	@echo "✓ LLM cleanup tests passed"
	@echo ""

test-extraction-marker: ensure-testdocs
	@echo "=== Testing MARKER Extraction Strategy ==="
	@echo "Running Marker PDF extraction (requires GPU/CPU resources)"
	@echo "Container: data-lxc ($(DATA_IP))"
	@echo ""
	@echo "⚠️  Warning: Marker requires significant resources"
	@echo "   - 3.2GB model cache"
	@echo "   - High memory/CPU during processing"
	@echo "   - May fail on resource-constrained environments"
	@echo ""
	@if [ -z "$(DATA_IP)" ] || [ "$(DATA_IP)" = " " ]; then \
		echo "ERROR: Could not resolve data-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@ssh root@$(DATA_IP) 'cd /srv/data && source venv/bin/activate && SAMPLES_DIR=$(SAMPLES_DIR) python -m pytest tests/test_pdf_extraction_marker.py -v --tb=short --capture=no' || (echo "Marker extraction tests FAILED (may be expected on low-resource systems)" && exit 1)
	@echo "✓ Marker extraction tests passed"
	@echo ""

test-extraction-colpali: ensure-testdocs
	@echo "=== Testing ColPali Visual Extraction ==="
	@echo "Running ColPali visual embeddings extraction"
	@echo "Container: data-lxc ($(DATA_IP))"
	@echo ""
	@echo "⚠️  Requires: ColPali service running on vllm-lxc (GPU required)"
	@echo ""
	@if [ -z "$(DATA_IP)" ] || [ "$(DATA_IP)" = " " ]; then \
		echo "ERROR: Could not resolve data-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@ssh root@$(DATA_IP) 'cd /srv/data && source venv/bin/activate && SAMPLES_DIR=$(SAMPLES_DIR) python -m pytest tests/test_colpali.py -v --tb=short --capture=no' || (echo "ColPali extraction tests FAILED" && exit 1)
	@echo "✓ ColPali extraction tests passed"
	@echo ""

test-agent:
	@echo "Running agent service tests..."
	@echo "Container: agent-lxc ($(AGENT_IP))"
	@echo ""
	@echo "=== Agent API Tests (pytest) ==="
	@ssh root@$(AGENT_IP) 'cd /srv/agent && bash scripts/run-tests.sh all' || (echo "Agent tests FAILED" && exit 1)
	@echo "✓ Agent tests passed"
	@echo ""

test-agent-unit:
	@echo "Running agent unit tests only..."
	@echo "Container: agent-lxc ($(AGENT_IP))"
	@echo ""
	@ssh root@$(AGENT_IP) 'cd /srv/agent && bash scripts/run-tests.sh unit' || (echo "Agent unit tests FAILED" && exit 1)
	@echo "✓ Agent unit tests passed"
	@echo ""

test-agent-integration:
	@echo "Running agent integration tests..."
	@echo "Container: agent-lxc ($(AGENT_IP))"
	@echo ""
	@echo "⚠️  Warning: Requires auth, search, data services running"
	@echo ""
	@ssh root@$(AGENT_IP) 'cd /srv/agent && bash scripts/run-tests.sh integration' || (echo "Agent integration tests FAILED" && exit 1)
	@echo "✓ Agent integration tests passed"
	@echo ""

test-agent-coverage:
	@echo "Running agent tests with coverage..."
	@echo "Container: agent-lxc ($(AGENT_IP))"
	@echo ""
	@ssh root@$(AGENT_IP) 'cd /srv/agent && bash scripts/run-tests.sh coverage' || (echo "Agent tests FAILED" && exit 1)
	@echo "✓ Agent tests passed with coverage"
	@echo ""
	@echo "Coverage report: ssh root@$(AGENT_IP) and view /srv/agent/htmlcov/index.html"
	@echo ""

test-authz:
	@echo "Running authz service tests..."
	@echo "Container: authz-lxc ($(AUTHZ_IP))"
	@echo ""
	@if [ -z "$(AUTHZ_IP)" ] || [ "$(AUTHZ_IP)" = " " ]; then \
		echo "ERROR: Could not resolve authz-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@echo "=== Authz Service Tests (pytest) ==="
	@echo "Running PVT tests with environment from .env file..."
	ssh -o StrictHostKeyChecking=no root@$(AUTHZ_IP) "cd /srv/authz/app && source ../venv/bin/activate && pip install -q -r requirements.test.txt 2>/dev/null; set -a && source /srv/authz/.env && set +a && pytest tests/integration/test_pvt.py -v --tb=short" || (echo "Authz tests FAILED" && exit 1)
	@echo "✓ Authz tests passed"
	@echo ""

test-apps:
	@echo "Running app tests..."
	@echo "Container: core-apps-lxc ($(CORE_APPS_IP))"
	@echo ""
	@echo "=== Busibox Portal Tests ==="
	@ssh root@$(CORE_APPS_IP) 'cd /srv/apps/busibox-portal && npm test' || (echo "Busibox Portal tests FAILED" && exit 1)
	@echo "✓ Busibox Portal tests passed"
	@echo ""

test-search:
	@echo "Running search service tests (unit + integration)..."
	@echo "Container: milvus-lxc ($(MILVUS_IP))"
	@echo ""
	@if [ -z "$(MILVUS_IP)" ] || [ "$(MILVUS_IP)" = " " ]; then \
		echo "ERROR: Could not resolve milvus-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@echo "=== Search API Tests (unit + integration) ==="
	@ssh root@$(MILVUS_IP) 'search-test all' || (echo "Search tests FAILED" && exit 1)
	@echo "✓ Search tests passed"
	@echo ""

test-search-unit:
	@echo "Running search unit tests only..."
	@echo "Container: milvus-lxc ($(MILVUS_IP))"
	@echo ""
	@ssh root@$(MILVUS_IP) 'search-test unit' || (echo "Search unit tests FAILED" && exit 1)
	@echo "✓ Search unit tests passed"
	@echo ""

test-search-integration:
	@echo "Running search integration tests..."
	@echo "Container: milvus-lxc ($(MILVUS_IP))"
	@echo ""
	@echo "⚠️  Warning: Requires Milvus, PostgreSQL, and embedding service running"
	@echo ""
	@ssh root@$(MILVUS_IP) 'search-test integration' || (echo "Search integration tests FAILED" && exit 1)
	@echo "✓ Search integration tests passed"
	@echo ""

test-search-coverage:
	@echo "Running search tests with coverage..."
	@echo "Container: milvus-lxc ($(MILVUS_IP))"
	@echo ""
	@ssh root@$(MILVUS_IP) 'search-test coverage' || (echo "Search tests FAILED" && exit 1)
	@echo "✓ Search tests passed with coverage"
	@echo ""
	@echo "Coverage report: ssh root@$(MILVUS_IP) and view /opt/search/htmlcov/index.html"
	@echo ""

test-all: test-data test-search test-agent test-authz test-apps
	@echo "================================"
	@echo "All service tests passed!"
	@echo "================================"

# =============================================================================
# PVT (Post-Deployment Validation) Tests
# =============================================================================
# Fast, lightweight tests that verify services are working after deployment.
# Use client credentials (no admin token required).

pvt-all: pvt-authz pvt-data pvt-search pvt-agent
	@echo "================================"
	@echo "All PVT tests passed!"
	@echo "================================"

pvt-authz:
	@echo "Running AuthZ PVT tests..."
	@ssh root@$(AUTHZ_IP) 'cd /srv/authz/app && source ../venv/bin/activate && set -a && source /srv/authz/.env && set +a && pytest tests/integration/test_pvt.py -v --tb=short' || (echo "AuthZ PVT FAILED" && exit 1)
	@echo "✓ AuthZ PVT passed"
	@echo ""

pvt-data:
	@echo "Running Data PVT tests..."
	@ssh root@$(DATA_IP) 'cd /srv/data && source venv/bin/activate && set -a && source .env && set +a && pytest tests/integration/test_pvt.py -v --tb=short' || (echo "Data PVT FAILED" && exit 1)
	@echo "✓ Data PVT passed"
	@echo ""

pvt-search:
	@echo "Running Search PVT tests..."
	@ssh root@$(MILVUS_IP) 'cd /opt/search && source venv/bin/activate && set -a && source .env && set +a && pytest tests/integration/test_pvt.py -v --tb=short' || (echo "Search PVT FAILED" && exit 1)
	@echo "✓ Search PVT passed"
	@echo ""

pvt-agent:
	@echo "Running Agent PVT tests..."
	@ssh root@$(AGENT_IP) 'cd /srv/agent && source .venv/bin/activate && set -a && source .env && set +a && PYTHONPATH=/srv/agent pytest tests/integration/test_pvt.py -v --tb=short' || (echo "Agent PVT FAILED" && exit 1)
	@echo "✓ Agent PVT passed"
	@echo ""

# Security Tests (run from admin workstation)
test-security:
	@echo "Running security tests against $(INV)..."
	@if [ "$(INV)" = "inventory/staging" ]; then \
		SECURITY_TEST_ENV=test bash ../../tests/security/run_tests.sh; \
	else \
		SECURITY_TEST_ENV=production bash ../../tests/security/run_tests.sh; \
	fi

test-security-auth:
	@echo "Running authentication security tests..."
	@if [ "$(INV)" = "inventory/staging" ]; then \
		SECURITY_TEST_ENV=test bash ../../tests/security/run_tests.sh --marker=auth; \
	else \
		SECURITY_TEST_ENV=production bash ../../tests/security/run_tests.sh --marker=auth; \
	fi

test-security-injection:
	@echo "Running injection security tests..."
	@if [ "$(INV)" = "inventory/staging" ]; then \
		SECURITY_TEST_ENV=test bash ../../tests/security/run_tests.sh --marker=injection; \
	else \
		SECURITY_TEST_ENV=production bash ../../tests/security/run_tests.sh --marker=injection; \
	fi

test-security-fuzz:
	@echo "Running fuzzing security tests..."
	@if [ "$(INV)" = "inventory/staging" ]; then \
		SECURITY_TEST_ENV=test bash ../../tests/security/run_tests.sh --marker=fuzz; \
	else \
		SECURITY_TEST_ENV=production bash ../../tests/security/run_tests.sh --marker=fuzz; \
	fi

# ============================================================================
# Migration
# ============================================================================
# Embedding model migration - detect and handle dimension changes
#
# When you change the embedding model in model_registry.yml (e.g., from bge-small 
# to bge-large), the Milvus collection dimensions may no longer match.
#
# Usage:
#   make check-embeddings              # Check if migration is needed
#   make migrate-embeddings            # Interactive migration (prompts for confirmation)
#   make migrate-embeddings-force      # Force migration without confirmation
#
# Or use the interactive menu: make menu -> Migration -> Check/Migrate Embeddings
#
# After migration, all documents need to be re-ingested to generate new embeddings.

check-embeddings:
	@MILVUS_IP=$(MILVUS_IP) bash scripts/check-embedding-migration.sh --check || true

migrate-embeddings:
	@MILVUS_IP=$(MILVUS_IP) bash scripts/check-embedding-migration.sh --migrate

migrate-embeddings-force:
	@MILVUS_IP=$(MILVUS_IP) bash scripts/check-embedding-migration.sh --migrate --force

# ============================================================================
# DOCKER DEPLOYMENT
# ============================================================================
# Deploy Busibox via Docker Compose using Ansible
#
# This provides a unified deployment system for Docker environments,
# using the same Ansible patterns as LXC deployments.
#
# Usage:
#   make docker                    # Full Docker deployment
#   make docker-infrastructure     # Deploy data stores only
#   make docker-apis               # Deploy API services
#   make docker-frontend           # Deploy frontend apps
#   make docker-status             # Show container status
#
# Environment Variables:
#   CONTAINER_PREFIX   - Container name prefix (default: dev)
#   GITHUB_AUTH_TOKEN  - GitHub token for private repos
#   BUSIBOX_ENV        - Environment name (development, production)

# Docker inventory path
DOCKER_INV = inventory/docker

# Docker-specific vault flags (use same vault as LXC)
DOCKER_VAULT_FLAGS = $(shell [ -f $(VAULT_PASS_FILE) ] && echo "--vault-password-file $(VAULT_PASS_FILE)" || echo "")

# Container prefix (default: dev)
CONTAINER_PREFIX ?= dev

# Docker deployment variables
DOCKER_VARS = -e container_prefix=$(CONTAINER_PREFIX) -e busibox_env=$(or $(BUSIBOX_ENV),development)

.PHONY: docker docker-infrastructure docker-apis docker-llm docker-frontend docker-status docker-logs docker-clean

docker:
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                    BUSIBOX DOCKER DEPLOYMENT                          ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "Container prefix: $(CONTAINER_PREFIX)"
	@echo "Environment: $(or $(BUSIBOX_ENV),development)"
	@echo ""
	ansible-playbook -i $(DOCKER_INV) docker.yml $(DOCKER_VAULT_FLAGS) $(DOCKER_VARS) $(EXTRA_ARGS)

docker-infrastructure:
	@echo "Deploying Docker infrastructure (postgres, redis, minio, milvus)..."
	ansible-playbook -i $(DOCKER_INV) docker.yml --tags infrastructure $(DOCKER_VAULT_FLAGS) $(DOCKER_VARS) $(EXTRA_ARGS)

docker-apis:
	@echo "Deploying Docker API services (authz, data, search, agent)..."
	ansible-playbook -i $(DOCKER_INV) docker.yml --tags apis $(DOCKER_VAULT_FLAGS) $(DOCKER_VARS) $(EXTRA_ARGS)

docker-llm:
	@echo "Deploying Docker LLM services (litellm)..."
	ansible-playbook -i $(DOCKER_INV) docker.yml --tags llm $(DOCKER_VAULT_FLAGS) $(DOCKER_VARS) $(EXTRA_ARGS)

docker-frontend:
	@echo "Deploying Docker frontend (proxy, core-apps)..."
	ansible-playbook -i $(DOCKER_INV) docker.yml --tags frontend $(DOCKER_VAULT_FLAGS) $(DOCKER_VARS) $(EXTRA_ARGS)

docker-status:
	@echo "Docker Container Status:"
	@echo "========================"
	@docker compose -f ../../docker-compose.yml -f ../../docker-compose.local-dev.yml ps 2>/dev/null || echo "No containers running"

docker-logs:
	@SERVICE=$${SERVICE:-""}; \
	if [ -n "$$SERVICE" ]; then \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.local-dev.yml logs --tail=100 $$SERVICE; \
	else \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.local-dev.yml logs --tail=50; \
	fi

docker-clean:
	@echo "Stopping all Docker containers..."
	@docker compose -f ../../docker-compose.yml -f ../../docker-compose.local-dev.yml down --remove-orphans 2>/dev/null || true
	@echo "Note: Data volumes are preserved. Use 'docker volume prune' to remove them."

# =============================================================================
# DOCKER RUNTIME OPERATIONS (Proxmox-style)
# =============================================================================
# Runtime app management for Docker containers without rebuild.
# These commands use the Deploy API or docker exec to manage apps.
#
# For core apps (busibox-portal, busibox-agents):
#   make install SERVICE=busibox-portal            # Deploy/update via Deploy API
#   make app-restart SERVICE=busibox-portal        # Restart app via supervisorctl
#   make app-status                           # Show all app status
#   make app-logs SERVICE=busibox-portal           # View app logs
#   make nginx-reload                         # Reload nginx config
#
# These mirror the Proxmox deployment pattern where apps are installed
# at runtime rather than baked into container images.

.PHONY: install app-restart app-stop app-start app-status app-logs nginx-reload core-apps-shell

# Install/update an app in running container via Deploy API
install:
ifndef SERVICE
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                    RUNTIME APP INSTALLATION                           ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "Usage: make install SERVICE=<service> [REF=<git-ref>]"
	@echo ""
	@echo "Core apps: busibox-portal, busibox-agents"
	@echo "User apps: Any app deployed via Deploy API"
	@echo ""
	@echo "REF: git branch or tag (default: main)"
	@echo ""
	@echo "Examples:"
	@echo "  make install SERVICE=busibox-portal"
	@echo "  make install SERVICE=busibox-agents REF=v1.2.3"
	@echo "  make install SERVICE=busibox-portal REF=feature/new-ui"
else
	@echo "🚀 Deploying $(SERVICE) via Deploy API..."
	@REF=$${REF:-main}; \
	if [ -f ../../docker-compose.yml ]; then \
		echo "Using Docker environment (docker exec)"; \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec core-apps /usr/local/bin/entrypoint.sh deploy $(SERVICE) $$REF; \
	else \
		echo "Using Proxmox environment (Deploy API)"; \
		curl -X POST http://$(CORE_APPS_IP):8011/api/v1/deployment/deploy \
			-H "Content-Type: application/json" \
			-d '{"manifest": {"id": "$(SERVICE)", "name": "$(SERVICE)"}, "config": {"githubBranch": "'$$REF'"}}'; \
	fi
endif

# Reload nginx config (no container rebuild)
nginx-reload:
	@echo "🔄 Reloading nginx configuration..."
	@if [ -f ../../docker-compose.yml ]; then \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec proxy nginx -t && \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec proxy nginx -s reload; \
		echo "✅ Nginx reloaded"; \
	else \
		ssh root@$(PROXY_IP) 'nginx -t && nginx -s reload'; \
		echo "✅ Nginx reloaded"; \
	fi

# View app logs
app-logs:
ifndef SERVICE
	@echo "Viewing all supervisor logs..."
	@if [ -f ../../docker-compose.yml ]; then \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec core-apps tail -f /var/log/supervisor/*.log; \
	else \
		ssh root@$(CORE_APPS_IP) 'tail -f /var/log/supervisor/*.log'; \
	fi
else
	@echo "Viewing $(SERVICE) logs..."
	@if [ -f ../../docker-compose.yml ]; then \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec core-apps tail -f /var/log/supervisor/$(SERVICE)*.log; \
	else \
		ssh root@$(CORE_APPS_IP) 'journalctl -u $(SERVICE) -f'; \
	fi
endif

# Restart an app without rebuild
app-restart:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make app-restart SERVICE=<busibox-portal|busibox-agents>"
	@exit 1
else
	@echo "🔄 Restarting $(SERVICE)..."
	@if [ -f ../../docker-compose.yml ]; then \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec core-apps supervisorctl restart $(SERVICE); \
		echo "✅ $(SERVICE) restarted"; \
	else \
		ssh root@$(CORE_APPS_IP) 'systemctl restart $(SERVICE)'; \
		echo "✅ $(SERVICE) restarted"; \
	fi
endif

# Stop an app
app-stop:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make app-stop SERVICE=<busibox-portal|busibox-agents>"
	@exit 1
else
	@echo "🛑 Stopping $(SERVICE)..."
	@if [ -f ../../docker-compose.yml ]; then \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec core-apps supervisorctl stop $(SERVICE); \
		echo "✅ $(SERVICE) stopped"; \
	else \
		ssh root@$(CORE_APPS_IP) 'systemctl stop $(SERVICE)'; \
		echo "✅ $(SERVICE) stopped"; \
	fi
endif

# Start an app
app-start:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make app-start SERVICE=<busibox-portal|busibox-agents>"
	@exit 1
else
	@echo "🚀 Starting $(SERVICE)..."
	@if [ -f ../../docker-compose.yml ]; then \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec core-apps supervisorctl start $(SERVICE); \
		echo "✅ $(SERVICE) started"; \
	else \
		ssh root@$(CORE_APPS_IP) 'systemctl start $(SERVICE)'; \
		echo "✅ $(SERVICE) started"; \
	fi
endif

# Show app status
app-status:
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                    CORE APP STATUS                                    ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@if [ -f ../../docker-compose.yml ]; then \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec core-apps supervisorctl status; \
	else \
		ssh root@$(CORE_APPS_IP) 'systemctl status busibox-portal busibox-agents --no-pager'; \
	fi

# Open shell in core-apps container
core-apps-shell:
	@echo "Opening shell in core-apps container..."
	@if [ -f ../../docker-compose.yml ]; then \
		docker compose -f ../../docker-compose.yml -f ../../docker-compose.github.yml \
			exec core-apps /bin/bash; \
	else \
		ssh root@$(CORE_APPS_IP); \
	fi
