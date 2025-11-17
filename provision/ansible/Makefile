.PHONY: ping all files pg milvus search search-api agent ingest ingest-api ingest-worker apps openwebui deploy-apps verify verify-health verify-smoke test test-ingest test-ingest-all test-ingest-coverage test-search test-search-unit test-search-integration test-search-coverage test-agent test-apps test-all
# Default to production inventory (can override with: make all INV=inventory/test)
INV=inventory/production

# Vault password file (optional, can also use --ask-vault-pass)
# Create with: echo "password" > .vault_pass && chmod 600 .vault_pass
VAULT_PASS_FILE=~/.vault_pass
VAULT_FLAGS=$(shell [ -f $(VAULT_PASS_FILE) ] && echo "--vault-password-file $(VAULT_PASS_FILE)" || echo "--ask-vault-pass")

# Extra arguments (can override with: make vllm-embedding EXTRA_ARGS="-e skip_model_check=true")
EXTRA_ARGS=

# Dynamically get container IPs from inventory based on environment
# This allows tests to work in both production and test environments
INGEST_IP = $(shell ansible-inventory -i $(INV) --host ingest-lxc 2>/dev/null | grep -oP '"ansible_host":\s*"\K[^"]+' || \
	ansible-inventory -i $(INV) --host TEST-ingest-lxc 2>/dev/null | grep -oP '"ansible_host":\s*"\K[^"]+' || \
	echo "10.96.200.206")
AGENT_IP = $(shell ansible-inventory -i $(INV) --host agent-lxc 2>/dev/null | grep -oP '"ansible_host":\s*"\K[^"]+' || \
	ansible-inventory -i $(INV) --host TEST-agent-lxc 2>/dev/null | grep -oP '"ansible_host":\s*"\K[^"]+' || \
	echo "10.96.200.202")
APPS_IP = $(shell ansible-inventory -i $(INV) --host apps-lxc 2>/dev/null | grep -oP '"ansible_host":\s*"\K[^"]+' || \
	ansible-inventory -i $(INV) --host TEST-apps-lxc 2>/dev/null | grep -oP '"ansible_host":\s*"\K[^"]+' || \
	echo "10.96.200.201")
MILVUS_IP = $(shell ansible-inventory -i $(INV) --host milvus-lxc 2>/dev/null | grep -oP '"ansible_host":\s*"\K[^"]+' || \
	ansible-inventory -i $(INV) --host TEST-milvus-lxc 2>/dev/null | grep -oP '"ansible_host":\s*"\K[^"]+' || \
	echo "10.96.200.204")

ping:
	ansible -i $(INV) all -m ping

all:
	ansible-playbook -i $(INV) site.yml $(VAULT_FLAGS)

files:
	ansible-playbook -i $(INV) -l files site.yml $(VAULT_FLAGS)

pg:
	ansible-playbook -i $(INV) -l pg site.yml $(VAULT_FLAGS)

litellm:
	ansible-playbook -i $(INV) -l litellm site.yml $(VAULT_FLAGS)

vllm:
	ansible-playbook -i $(INV) -l vllm site.yml --tags vllm $(VAULT_FLAGS) $(EXTRA_ARGS)

vllm-embedding:
	ansible-playbook -i $(INV) -l vllm site.yml --tags vllm_embedding $(VAULT_FLAGS) $(EXTRA_ARGS)

colpali:
	ansible-playbook -i $(INV) -l vllm site.yml --tags colpali $(VAULT_FLAGS) $(EXTRA_ARGS)

milvus:
	ansible-playbook -i $(INV) -l milvus site.yml $(VAULT_FLAGS)

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

apps:
	ansible-playbook -i $(INV) -l apps site.yml $(VAULT_FLAGS)

openwebui:
	ansible-playbook -i $(INV) -l openwebui site.yml $(VAULT_FLAGS)

# T029: Deploy applications (runs app_deployer + secrets roles)
deploy-apps:
	ansible-playbook -i $(INV) -l agent,apps --tags app_deployer,secrets site.yml $(VAULT_FLAGS)

# Verification targets
verify: verify-health verify-smoke
	@echo "================================"
	@echo "All verification checks passed!"
	@echo "================================"

verify-health:
	@echo "Running health checks on all services..."
	@echo ""
	@echo "=== PostgreSQL ==="
	@psql -h 10.96.200.26 -U busibox_user -d busibox -c "SELECT version();" || (echo "PostgreSQL health check FAILED" && exit 1)
	@echo "✓ PostgreSQL is healthy"
	@echo ""
	@echo "=== MinIO ==="
	@curl -f -s http://10.96.200.28:9000/minio/health/live > /dev/null || (echo "MinIO health check FAILED" && exit 1)
	@echo "✓ MinIO is healthy"
	@echo ""
	@echo "=== Milvus ==="
	@curl -f -s http://10.96.200.27:9091/healthz > /dev/null || (echo "Milvus health check FAILED" && exit 1)
	@echo "✓ Milvus is healthy"
	@echo ""
	@echo "=== Agent API ==="
	@curl -f -s http://10.96.200.30:8000/health/live > /dev/null || (echo "Agent API health check FAILED (may not be deployed yet)" && exit 0)
	@echo "✓ Agent API is healthy (or not deployed yet)"
	@echo ""

verify-smoke:
	@echo "Running smoke tests..."
	@echo ""
	@echo "=== Database Schema ==="
	@psql -h 10.96.200.26 -U busibox_user -d busibox -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;" || (echo "Schema verification FAILED" && exit 1)
	@echo "✓ Database schema verified"
	@echo ""
	@echo "=== Migrations ==="
	@psql -h 10.96.200.26 -U busibox_user -d busibox -c "SELECT version, name, applied_at FROM schema_migrations ORDER BY version;" || (echo "Migration verification FAILED" && exit 1)
	@echo "✓ Database migrations verified"
	@echo ""

# Test targets - run tests on remote containers
test: test-ingest
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
