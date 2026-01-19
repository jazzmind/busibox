.PHONY: ping all files pg milvus nginx search search-api agent ingest ingest-api ingest-worker ingest-update-tests docs apps openwebui deploy-apps deploy-ai-portal deploy-agent-manager deploy-doc-intel deploy-foundation deploy-project-analysis deploy-innovation verify verify-health verify-smoke service-start service-stop service-restart service-status service-logs service-health test test-menu test-ingest test-ingest-all test-ingest-coverage test-extraction-simple test-extraction-llm test-extraction-marker test-extraction-colpali test-search test-search-unit test-search-integration test-search-coverage test-agent test-authz test-apps test-security test-security-auth test-security-injection test-security-fuzz test-all configure generate-token-keys bootstrap-test-creds bridge tailscale test-bridge
# Default to production inventory (can override with: make all INV=inventory/staging)
INV=inventory/production

# Vault password file (optional, can also use --ask-vault-pass)
# Create with: echo "password" > .vault_pass && chmod 600 .vault_pass
VAULT_PASS_FILE=~/.vault_pass
VAULT_FLAGS=$(shell [ -f $(VAULT_PASS_FILE) ] && echo "--vault-password-file $(VAULT_PASS_FILE)" || echo "--ask-vault-pass")

# Extra arguments (can override with: make vllm-embedding EXTRA_ARGS="-e skip_model_check=true")
EXTRA_ARGS=

# Container IPs - production uses 10.96.200.x, staging uses 10.96.201.x
# For production, IPs are static and defined in group_vars
# For staging, they use the TEST network (10.96.201.x)
ifeq ($(INV),inventory/staging)
  # Staging IPs (10.96.201.0/21 network)
  INGEST_IP = 10.96.201.206
  AGENT_IP = 10.96.201.202
  APPS_IP = 10.96.201.201
  MILVUS_IP = 10.96.201.204
  AUTHZ_IP = 10.96.201.210
else
  # Production IPs (10.96.200.0/21 network)
  INGEST_IP = 10.96.200.206
  AGENT_IP = 10.96.200.202
  APPS_IP = 10.96.200.201
  MILVUS_IP = 10.96.200.204
  AUTHZ_IP = 10.96.200.210
endif

ping:
	ansible -i $(INV) all -m ping

all:
	ansible-playbook -i $(INV) site.yml $(VAULT_FLAGS)

files:
	ansible-playbook -i $(INV) -l files site.yml $(VAULT_FLAGS)

pg:
	ansible-playbook -i $(INV) -l pg site.yml $(VAULT_FLAGS)

authz:
	ansible-playbook -i $(INV) -l authz site.yml $(VAULT_FLAGS)

litellm:
	ansible-playbook -i $(INV) -l litellm site.yml $(VAULT_FLAGS)

vllm:
	ansible-playbook -i $(INV) -l vllm site.yml --tags vllm $(VAULT_FLAGS) $(EXTRA_ARGS)

vllm-embedding:
	ansible-playbook -i $(INV) -l vllm site.yml --tags vllm_embedding $(VAULT_FLAGS) $(EXTRA_ARGS)

colpali:
	ansible-playbook -i $(INV) -l vllm site.yml --tags colpali $(VAULT_FLAGS) $(EXTRA_ARGS)

milvus:
	ansible-playbook -i $(INV) -l milvus site.yml --tags milvus $(VAULT_FLAGS)

nginx:
	ansible-playbook -i $(INV) -l proxy site.yml --tags nginx $(VAULT_FLAGS)

search: milvus search-api
	@echo "================================"
	@echo "Search services deployed!"
	@echo "  - Milvus (vector database)"
	@echo "  - Search API (hybrid search)"
	@echo "================================"

search-api:
	ansible-playbook -i $(INV) -l milvus site.yml --tags search-api $(VAULT_FLAGS)

agent:
	ansible-playbook -i $(INV) -l agent site.yml $(VAULT_FLAGS)

ingest:
	ansible-playbook -i $(INV) -l ingest site.yml $(VAULT_FLAGS)

ingest-api:
	ansible-playbook -i $(INV) -l ingest site.yml --tags ingest_api $(VAULT_FLAGS)

ingest-worker:
	ansible-playbook -i $(INV) -l ingest site.yml --tags ingest_worker $(VAULT_FLAGS)

ingest-update-tests:
	@echo "Updating ingest test files..."
	ansible-playbook -i $(INV) -l ingest site.yml --tags ingest_update_tests $(VAULT_FLAGS)
	@echo "✓ Test files updated"

# Docs API - Documentation service (runs on apps container)
docs:
	ansible-playbook -i $(INV) -l apps site.yml --tags docs $(VAULT_FLAGS)

apps:
	ansible-playbook -i $(INV) -l apps site.yml $(VAULT_FLAGS)

openwebui:
	ansible-playbook -i $(INV) -l openwebui site.yml $(VAULT_FLAGS)

# Bridge Service (multi-channel communication: Signal, Email, WhatsApp, Webhooks)
# Note: This is optional and non-blocking - won't fail if bridge host doesn't exist
bridge:
	ansible-playbook -i $(INV) -l bridge site.yml --tags bridge $(VAULT_FLAGS) || true

# Tailscale VPN (for secure remote access)
tailscale:
	ansible-playbook -i $(INV) -l proxy site.yml --tags tailscale $(VAULT_FLAGS)

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
deploy-ai-portal:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=ai-portal"

deploy-agent-manager:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=agent-manager"

deploy-doc-intel:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=doc-intel"

deploy-foundation:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=foundation"

deploy-project-analysis:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=project-analysis"

deploy-innovation:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=innovation"

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
	@if curl -f -s http://$(APPS_IP):8004/health/live > /dev/null 2>&1; then \
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
  ingest-api=ingest-api@$(INGEST_IP) \
  ingest-worker=ingest-worker@$(INGEST_IP) \
  search-api=search-api@$(MILVUS_IP) \
  agent-api=agent@$(AGENT_IP) \
  milvus=milvus@$(MILVUS_IP) \
  nginx=nginx@$(APPS_IP) \
  postgresql=postgresql@10.96.200.203 \
  redis=redis@$(INGEST_IP)

service-start:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make service-start SERVICE=<service>"
	@echo ""
	@echo "Available services: authz, ingest-api, ingest-worker, search-api, agent-api, milvus, nginx, postgresql, redis"
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
	elif [ "$(SERVICE)" = "ingest-api" ]; then \
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

test-all-targets: test-ingest test-search test-agent-unit test-apps
	@echo "================================"
	@echo "All tests passed!"
	@echo "================================"

test-ingest:
	@echo "Running ingest service tests (unit + integration)..."
	@echo "Container: ingest-lxc ($(INGEST_IP))"
	@echo ""
	@if [ -z "$(INGEST_IP)" ] || [ "$(INGEST_IP)" = " " ]; then \
		echo "ERROR: Could not resolve ingest-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@echo "=== Ingest Tests (unit + integration) ==="
	@ssh root@$(INGEST_IP) 'ingest-test all' || (echo "Ingest tests FAILED" && exit 1)
	@echo "✓ Ingest tests passed"
	@echo ""

test-ingest-all:
	@echo "Running all ingest service tests (including integration)..."
	@echo "Container: ingest-lxc ($(INGEST_IP))"
	@echo ""
	@ssh root@$(INGEST_IP) 'ingest-test all' || (echo "Ingest integration tests FAILED" && exit 1)
	@echo "✓ All ingest tests passed"
	@echo ""

test-ingest-coverage:
	@echo "Running ingest tests with coverage..."
	@echo "Container: ingest-lxc ($(INGEST_IP))"
	@echo ""
	@ssh root@$(INGEST_IP) 'ingest-test coverage' || (echo "Ingest tests FAILED" && exit 1)
	@echo "✓ Ingest tests passed with coverage"
	@echo ""
	@echo "Coverage report: ssh root@$(INGEST_IP) and view /srv/ingest/htmlcov/index.html"
	@echo ""

# Testdocs repository - cloned to server via git (more efficient than copying large files)
# Uses GitHub token from ~/.github_token on the server (deployed by Ansible)
TESTDOCS_REPO = jazzmind/busibox-testdocs
TESTDOCS_DIR = /tmp/testdocs
SAMPLES_DIR = $(TESTDOCS_DIR)/pdf/general

# Helper target to ensure testdocs are available on ingest server
.PHONY: ensure-testdocs
ensure-testdocs:
	@echo "📦 Checking test documents on server..."
	@if ssh root@$(INGEST_IP) '[ -d $(SAMPLES_DIR) ] && [ -f $(SAMPLES_DIR)/doc01_rfp_project_management/source.pdf ]' 2>/dev/null; then \
		echo "✓ Test documents already available, updating..."; \
		ssh root@$(INGEST_IP) 'cd $(TESTDOCS_DIR) && git pull --quiet' || true; \
	else \
		echo "📥 Cloning testdocs repository to server..."; \
		echo "   Repo: $(TESTDOCS_REPO)"; \
		echo "   Destination: $(TESTDOCS_DIR)"; \
		if ! ssh root@$(INGEST_IP) '\
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
			echo "Ensure GitHub token is deployed to ingest server:"; \
			echo "  1. Run: make ingest  (deploys ~/.github_token from vault)"; \
			echo "  2. Or manually: ssh root@$(INGEST_IP) and create ~/.github_token"; \
			echo ""; \
			exit 1; \
		fi; \
		echo "✓ Test documents cloned successfully"; \
	fi

# Extraction strategy tests
test-extraction-simple: ensure-testdocs
	@echo "=== Testing SIMPLE Extraction Strategy ==="
	@echo "Running basic PDF extraction without LLM cleanup"
	@echo "Container: ingest-lxc ($(INGEST_IP))"
	@echo ""
	@if [ -z "$(INGEST_IP)" ] || [ "$(INGEST_IP)" = " " ]; then \
		echo "ERROR: Could not resolve ingest-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@echo ""
	@ssh root@$(INGEST_IP) 'cd /srv/ingest && source venv/bin/activate && SAMPLES_DIR=$(SAMPLES_DIR) python -m pytest tests/test_pdf_extraction_simple.py -v --tb=short --capture=no' || (echo "SIMPLE extraction tests FAILED" && exit 1)
	@echo "✓ SIMPLE extraction tests passed"
	@echo ""

test-extraction-llm: ensure-testdocs
	@echo "=== Testing SIMPLE + LLM Cleanup ==="
	@echo "Running PDF extraction WITH LLM cleanup"
	@echo "Container: ingest-lxc ($(INGEST_IP))"
	@echo ""
	@echo "⚠️  Requires: LiteLLM service running on litellm-lxc"
	@echo ""
	@if [ -z "$(INGEST_IP)" ] || [ "$(INGEST_IP)" = " " ]; then \
		echo "ERROR: Could not resolve ingest-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@ssh root@$(INGEST_IP) 'cd /srv/ingest && source venv/bin/activate && SAMPLES_DIR=$(SAMPLES_DIR) python -m pytest tests/test_llm_cleanup.py -v --tb=short --capture=no' || (echo "LLM cleanup tests FAILED" && exit 1)
	@echo "✓ LLM cleanup tests passed"
	@echo ""

test-extraction-marker: ensure-testdocs
	@echo "=== Testing MARKER Extraction Strategy ==="
	@echo "Running Marker PDF extraction (requires GPU/CPU resources)"
	@echo "Container: ingest-lxc ($(INGEST_IP))"
	@echo ""
	@echo "⚠️  Warning: Marker requires significant resources"
	@echo "   - 3.2GB model cache"
	@echo "   - High memory/CPU during processing"
	@echo "   - May fail on resource-constrained environments"
	@echo ""
	@if [ -z "$(INGEST_IP)" ] || [ "$(INGEST_IP)" = " " ]; then \
		echo "ERROR: Could not resolve ingest-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@ssh root@$(INGEST_IP) 'cd /srv/ingest && source venv/bin/activate && SAMPLES_DIR=$(SAMPLES_DIR) python -m pytest tests/test_pdf_extraction_marker.py -v --tb=short --capture=no' || (echo "Marker extraction tests FAILED (may be expected on low-resource systems)" && exit 1)
	@echo "✓ Marker extraction tests passed"
	@echo ""

test-extraction-colpali: ensure-testdocs
	@echo "=== Testing ColPali Visual Extraction ==="
	@echo "Running ColPali visual embeddings extraction"
	@echo "Container: ingest-lxc ($(INGEST_IP))"
	@echo ""
	@echo "⚠️  Requires: ColPali service running on vllm-lxc (GPU required)"
	@echo ""
	@if [ -z "$(INGEST_IP)" ] || [ "$(INGEST_IP)" = " " ]; then \
		echo "ERROR: Could not resolve ingest-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@ssh root@$(INGEST_IP) 'cd /srv/ingest && source venv/bin/activate && SAMPLES_DIR=$(SAMPLES_DIR) python -m pytest tests/test_colpali.py -v --tb=short --capture=no' || (echo "ColPali extraction tests FAILED" && exit 1)
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
	@echo "⚠️  Warning: Requires auth, search, ingest services running"
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
	@echo "Extracting test credentials from vault..."
	@TEST_DB_PASSWORD=$$(ansible-vault view roles/secrets/vars/vault.yml $(VAULT_FLAGS) 2>/dev/null | grep 'password: 0f78' | awk '{print $$2}'); \
	AUTHZ_ADMIN_TOKEN=$$(ansible-vault view roles/secrets/vars/vault.yml $(VAULT_FLAGS) 2>/dev/null | grep 'authz_admin_token:' | awk '{print $$2}'); \
	if [ -z "$$TEST_DB_PASSWORD" ]; then \
		echo "ERROR: Could not extract TEST_DB_PASSWORD from vault"; \
		exit 1; \
	fi; \
	if [ -z "$$AUTHZ_ADMIN_TOKEN" ]; then \
		echo "WARNING: Could not extract AUTHZ_ADMIN_TOKEN from vault"; \
	fi; \
	echo "Running tests with real database integration..."; \
	ssh -o StrictHostKeyChecking=no root@$(AUTHZ_IP) "cd /srv/authz/app && source ../venv/bin/activate && pip install -q -r requirements.test.txt 2>/dev/null; export TEST_DB_USER='busibox_test_user' TEST_DB_PASSWORD='$$TEST_DB_PASSWORD' TEST_DB_NAME='test_authz' TEST_DB_HOST='10.96.201.203' POSTGRES_DB='test_authz' AUTHZ_ADMIN_TOKEN='$$AUTHZ_ADMIN_TOKEN' && pytest -v --tb=short" || (echo "Authz tests FAILED" && exit 1)
	@echo "✓ Authz tests passed"
	@echo ""

test-apps:
	@echo "Running app tests..."
	@echo "Container: apps-lxc ($(APPS_IP))"
	@echo ""
	@echo "=== AI Portal Tests ==="
	@ssh root@$(APPS_IP) 'cd /srv/apps/ai-portal && npm test' || (echo "AI Portal tests FAILED" && exit 1)
	@echo "✓ AI Portal tests passed"
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

test-all: test-ingest test-search test-agent test-authz test-apps
	@echo "================================"
	@echo "All service tests passed!"
	@echo "================================"

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
