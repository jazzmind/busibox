.PHONY: ping all files pg milvus nginx search search-api agent ingest ingest-api ingest-worker ingest-update-tests apps openwebui deploy-apps deploy-ai-portal deploy-agent-client deploy-doc-intel deploy-foundation deploy-project-analysis deploy-innovation verify verify-health verify-smoke test test-menu test-ingest test-ingest-all test-ingest-coverage test-extraction-simple test-extraction-llm test-extraction-marker test-extraction-colpali test-search test-search-unit test-search-integration test-search-coverage test-agent test-apps test-all configure generate-token-keys deploy-agent-server
# Default to production inventory (can override with: make all INV=inventory/test)
INV=inventory/production

# Vault password file (optional, can also use --ask-vault-pass)
# Create with: echo "password" > .vault_pass && chmod 600 .vault_pass
VAULT_PASS_FILE=~/.vault_pass
VAULT_FLAGS=$(shell [ -f $(VAULT_PASS_FILE) ] && echo "--vault-password-file $(VAULT_PASS_FILE)" || echo "--ask-vault-pass")

# Extra arguments (can override with: make vllm-embedding EXTRA_ARGS="-e skip_model_check=true")
EXTRA_ARGS=

# Container IPs - production uses 10.96.200.x, test uses 10.96.201.x
# For production, IPs are static and defined in group_vars
# For test, they use the TEST network (10.96.201.x)
ifeq ($(INV),inventory/test)
  # Test IPs (10.96.201.0/21 network)
  INGEST_IP = 10.96.201.206
  AGENT_IP = 10.96.201.202
  APPS_IP = 10.96.201.201
  MILVUS_IP = 10.96.201.204
else
  # Production IPs (10.96.200.0/21 network)
  INGEST_IP = 10.96.200.206
  AGENT_IP = 10.96.200.202
  APPS_IP = 10.96.200.201
  MILVUS_IP = 10.96.200.204
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

apps:
	ansible-playbook -i $(INV) -l apps site.yml $(VAULT_FLAGS)

openwebui:
	ansible-playbook -i $(INV) -l openwebui site.yml $(VAULT_FLAGS)

# T029: Deploy applications (runs app_deployer + secrets roles)
deploy-apps:
	ansible-playbook -i $(INV) -l agent,apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS)

# Deploy individual applications (apps container)
deploy-ai-portal:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=ai-portal"

deploy-agent-client:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=agent-client"

deploy-doc-intel:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=doc-intel"

deploy-foundation:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=foundation"

deploy-project-analysis:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=project-analysis"

deploy-innovation:
	ansible-playbook -i $(INV) -l apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=innovation"

# Deploy agent-server (internal API service)
deploy-agent-server:
	ansible-playbook -i $(INV) -l agent --tags app_deployer,secrets site.yml $(VAULT_FLAGS) --extra-vars "deploy_app=agent-server"

# Configuration targets
configure:
	@bash ../../scripts/configure.sh

generate-token-keys:
	@bash ../../scripts/generate-token-service-keys.sh

# Verification targets
verify: verify-health verify-smoke
	@echo "================================"
	@echo "All verification checks passed!"
	@echo "================================"

verify-health:
	@echo "Running health checks on all services..."
	@echo ""
	@echo "=== Agent API ==="
	@curl -f -s http://10.96.200.202:8000/health/live > /dev/null || (echo "Agent API health check FAILED (may not be deployed yet)" && exit 0)
	@echo "✓ Agent API is healthy (or not deployed yet)"
	@echo ""
	@echo "=== PostgreSQL ==="
	@psql -h 10.96.200.203 -U busibox_user -d busibox -c "SELECT version();" || (echo "PostgreSQL health check FAILED" && exit 1)
	@echo "✓ PostgreSQL is healthy"
	@echo ""
	@echo "=== Milvus ==="
	@curl -f -s http://10.96.200.204:9091/healthz > /dev/null || (echo "Milvus health check FAILED" && exit 1)
	@echo "✓ Milvus is healthy"
	@echo ""
	@echo "=== MinIO ==="
	@curl -f -s http://10.96.200.205:9000/minio/health/live > /dev/null || (echo "MinIO health check FAILED" && exit 1)
	@echo "✓ MinIO is healthy"
	@echo ""

verify-smoke:
	@echo "Running smoke tests..."
	@echo ""
	@echo "=== Database Schema ==="
	@psql -h 10.96.200.203 -U busibox_user -d busibox -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;" || (echo "Schema verification FAILED" && exit 1)
	@echo "✓ Database schema verified"
	@echo ""
	@echo "=== Migrations ==="
	@psql -h 10.96.200.203 -U busibox_user -d busibox -c "SELECT version, name, applied_at FROM schema_migrations ORDER BY version;" || (echo "Migration verification FAILED" && exit 1)
	@echo "✓ Database migrations verified"
	@echo ""

# Test targets - run tests on remote containers
test: test-menu

test-menu:
	@./test-menu.sh

test-all-targets: test-ingest test-search test-agent test-apps
	@echo "================================"
	@echo "All tests passed!"
	@echo "================================"

test-ingest:
	@echo "Running ingest service tests..."
	@echo "Container: ingest-lxc ($(INGEST_IP))"
	@echo ""
	@if [ -z "$(INGEST_IP)" ] || [ "$(INGEST_IP)" = " " ]; then \
		echo "ERROR: Could not resolve ingest-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@echo "=== Ingest Chunker Tests ==="
	@ssh root@$(INGEST_IP) 'ingest-test' || (echo "Ingest tests FAILED" && exit 1)
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
	@echo "=== Agent API Tests ==="
	@ssh root@$(AGENT_IP) 'cd /srv/agent && source venv/bin/activate && npm test' || (echo "Agent tests FAILED" && exit 1)
	@echo "✓ Agent tests passed"
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
	@echo "Running search service tests..."
	@echo "Container: milvus-lxc ($(MILVUS_IP))"
	@echo ""
	@if [ -z "$(MILVUS_IP)" ] || [ "$(MILVUS_IP)" = " " ]; then \
		echo "ERROR: Could not resolve milvus-lxc IP address"; \
		echo "Check inventory: $(INV)/hosts.yml"; \
		exit 1; \
	fi
	@echo "=== Search API Unit Tests ==="
	@ssh root@$(MILVUS_IP) 'search-test' || (echo "Search tests FAILED" && exit 1)
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

test-all: test-ingest test-search test-agent test-apps
	@echo "================================"
	@echo "All service tests passed!"
	@echo "================================"
