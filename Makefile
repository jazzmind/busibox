.PHONY: menu help setup configure deploy test test-local test-docker test-security mcp \
        docker-up docker-start docker-down docker-restart docker-build docker-logs docker-ps docker-clean ssl-check

# Default target - interactive menu with health check
.DEFAULT_GOAL := menu

# ============================================================================
# VARIABLES
# ============================================================================
# Environment: local, staging, production
ENV ?=

# Service for targeted operations
SERVICE ?=

# Inventory (maps to environment for Ansible)
# INV=staging maps to inventory/staging, INV=production maps to inventory/production
INV ?= staging

# Test mode: container (on deployed containers) or local (on your machine)
MODE ?= container

# Additional args (e.g., pytest args)
ARGS ?=

# FAST mode: skip slow/gpu tests (default for local testing)
FAST ?=

# WORKER mode: start local ingest worker for integration tests
WORKER ?=

# Docker compose configuration
COMPOSE_FILE := docker-compose.local.yml
ENV_FILE := .env.local

# ============================================================================
# MAIN MENU (Default)
# ============================================================================
# Interactive menu with environment selection and health checks
# Usage: make              # Full interactive menu
#        make ENV=staging  # Start with staging environment selected
menu:
	@bash scripts/make/menu.sh $(ENV)

# ============================================================================
# HELP
# ============================================================================
help:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                         Busibox Commands                             ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "Usage: make <target> [OPTIONS]"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                         QUICK START"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  make                     # Interactive menu (recommended)"
	@echo "  make ENV=local           # Start menu with local environment"
	@echo "  make ENV=staging         # Start menu with staging environment"
	@echo "  make ENV=production      # Start menu with production environment"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                    DIRECT COMMANDS"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  setup         - Initial setup (install dependencies)"
	@echo "  configure     - Configure models, GPUs, secrets"
	@echo "  deploy        - Deploy services"
	@echo "  test          - Run tests (see testing section)"
	@echo "  mcp           - Build MCP server for Cursor AI"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                    DOCKER DEVELOPMENT"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  make docker-build                        # Build all images"
	@echo "  make docker-build SERVICE=authz-api      # Build specific service"
	@echo "  make docker-build NO_CACHE=1             # Rebuild without cache"
	@echo ""
	@echo "  make docker-up                           # Start all services"
	@echo "  make docker-up SERVICE=authz-api         # Start specific service"
	@echo "  make docker-down                         # Stop all services"
	@echo "  make docker-restart                      # Restart all services"
	@echo ""
	@echo "  make docker-ps                           # Show status"
	@echo "  make docker-logs                         # View all logs"
	@echo "  make docker-logs SERVICE=authz-api       # View specific logs"
	@echo "  make docker-clean                        # Remove containers & data"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                         TESTING"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  Docker (local development):"
	@echo "    make test-docker SERVICE=authz         # Run authz tests"
	@echo "    make test-docker SERVICE=agent         # Run agent tests"
	@echo "    make test-docker SERVICE=all           # Run all tests"
	@echo "    make test-docker SERVICE=agent ARGS='-k test_weather'"
	@echo ""
	@echo "  Against remote (staging/production via Proxmox):"
	@echo "    make test-local SERVICE=agent INV=staging"
	@echo "    make test-local SERVICE=all INV=production"
	@echo ""
	@echo "  On containers (via SSH):"
	@echo "    make test SERVICE=agent INV=staging"
	@echo ""
	@echo "  Options:"
	@echo "    FAST=0      Include slow/GPU tests (default: FAST=1 skips them)"
	@echo "    WORKER=1    Start local ingest worker for pipeline tests"
	@echo "    ARGS='...'  Pass pytest arguments"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                      ENVIRONMENTS"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  local       - Docker on localhost (development)"
	@echo "  staging     - 10.96.201.x network (pre-production)"
	@echo "  production  - 10.96.200.x network (live)"
	@echo ""
	@echo "  Staging/Production can use either Docker or Proxmox backends."
	@echo "  The interactive menu will ask for your preference."
	@echo ""

# ============================================================================
# SETUP & CONFIGURE
# ============================================================================
setup:
	@bash scripts/make/setup.sh

configure:
	@bash scripts/make/configure.sh

# ============================================================================
# DEPLOY
# ============================================================================
# Interactive: make deploy
# Direct:      make deploy SERVICE=authz INV=staging
deploy:
ifdef SERVICE
	@bash scripts/make/deploy.sh $(SERVICE) $(INV)
else
	@bash scripts/make/deploy.sh
endif

# ============================================================================
# TESTING
# ============================================================================

# Run tests on containers (via SSH)
# Interactive: make test
# Direct:      make test SERVICE=authz INV=staging
test:
ifdef SERVICE
	@PYTEST_ARGS="$(ARGS)" bash scripts/make/test.sh $(SERVICE) $(INV) $(MODE)
else
	@bash scripts/make/test.sh
endif

# Run tests locally against remote containers
# Usage: make test-local SERVICE=authz INV=staging
test-local:
ifndef SERVICE
	@echo ""
	@echo "Error: SERVICE is required"
	@echo ""
	@echo "Usage: make test-local SERVICE=<service> INV=<env>"
	@echo ""
	@echo "Services: authz, ingest, search, agent, all"
	@echo "Environments: staging, production"
	@echo ""
	@echo "Examples:"
	@echo "  make test-local SERVICE=agent INV=staging"
	@echo "  make test-local SERVICE=all INV=production"
	@echo "  make test-local SERVICE=agent INV=staging ARGS='-k test_weather'"
	@echo "  make test-local SERVICE=ingest INV=staging WORKER=1"
	@echo ""
	@exit 1
endif
	@FAST=$${FAST:-1} WORKER=$${WORKER:-0} bash scripts/test/run-local-tests.sh $(SERVICE) $(INV) $(ARGS)

# Bootstrap test databases (schema + OAuth clients + signing keys)
# Run this before running tests to initialize test_authz, test_files, test_agent_server
test-db-init:
	@echo "Bootstrapping test databases..."
	@docker compose -f docker-compose.local.yml --env-file .env.local run --rm test-db-init

# Check if test databases are bootstrapped
test-db-check:
	@echo "Checking test database status..."
	@docker exec local-postgres psql -U busibox_test_user -d test_authz -c "SELECT COUNT(*) as signing_keys FROM authz_signing_keys WHERE is_active = true;" 2>/dev/null || echo "Test databases not initialized. Run: make test-db-init"

# Run tests against local Docker
# Usage: make test-docker SERVICE=authz
test-docker:
ifndef SERVICE
	@echo ""
	@echo "Error: SERVICE is required"
	@echo ""
	@echo "Usage: make test-docker SERVICE=<service>"
	@echo ""
	@echo "Services: authz, ingest, search, agent, all"
	@echo ""
	@echo "Examples:"
	@echo "  make test-docker SERVICE=agent"
	@echo "  make test-docker SERVICE=all"
	@echo "  make test-docker SERVICE=agent ARGS='-k test_weather'"
	@echo "  make test-docker SERVICE=agent FAST=0  # Include slow tests"
	@echo ""
	@exit 1
endif
	@FAST=$${FAST:-1} INV=docker bash scripts/test/run-local-tests.sh $(SERVICE) docker $(ARGS)

# Security tests
test-security:
	@bash tests/security/run_tests.sh

# ============================================================================
# MCP SERVER
# ============================================================================
mcp:
	@bash scripts/make/mcp.sh

# ============================================================================
# DOCKER LOCAL DEVELOPMENT
# ============================================================================

# Ensure .env.local exists
_ensure-env:
	@if [ ! -f $(ENV_FILE) ]; then \
		if [ -f env.local.example ]; then \
			echo "Creating $(ENV_FILE) from env.local.example..."; \
			cp env.local.example $(ENV_FILE); \
			echo "Edit $(ENV_FILE) to add your API keys"; \
		fi; \
	fi

# Start Docker services (may rebuild if files changed)
docker-up: _ensure-env
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) up -d $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) up -d
endif
	@echo ""
	@echo "Services started. Use 'make docker-ps' to check status."

# Start Docker services without rebuilding (fast start)
docker-start: _ensure-env
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) up -d --no-build $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) up -d --no-build
endif
	@echo ""
	@echo "Services started. Use 'make docker-ps' to check status."

# Stop Docker services
docker-down:
	docker compose -f $(COMPOSE_FILE) down

# Restart Docker services
docker-restart:
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) restart $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) restart
endif

# Check/generate SSL certificates
ssl-check:
	@if [ ! -f ssl/localhost.crt ] || [ ! -f ssl/localhost.key ]; then \
		echo "[INFO] Generating SSL certificates..."; \
		bash scripts/setup/generate-local-ssl.sh; \
	fi

# Build Docker images
docker-build: ssl-check _ensure-env
ifdef SERVICE
ifdef NO_CACHE
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) build --no-cache $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) build $(SERVICE)
endif
else
ifdef NO_CACHE
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) build --no-cache
else
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) build
endif
endif

# View Docker logs
docker-logs:
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) logs -f $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) logs -f
endif

# Show Docker service status
docker-ps:
	docker compose -f $(COMPOSE_FILE) ps

# Clean Docker environment
docker-clean:
	@echo "WARNING: This will remove all containers and volumes!"
	@read -p "Are you sure? (y/N) " confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		docker compose -f $(COMPOSE_FILE) down -v --remove-orphans; \
		echo "Cleanup complete."; \
	else \
		echo "Cancelled."; \
	fi

# Backward compatibility
docker-test: test-docker
