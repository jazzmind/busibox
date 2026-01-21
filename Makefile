.PHONY: menu help setup configure deploy test test-local test-docker test-security mcp \
        docker-up docker-up-prod docker-start docker-down docker-restart docker-restart-apis docker-restart-ingest docker-build docker-logs docker-ps docker-clean \
        vault-generate-env vault-migrate vault-sync ssl-check \
        demo demo-warmup demo-clean demo-status

# Default target - interactive menu with health check
.DEFAULT_GOAL := menu

# ============================================================================
# VARIABLES
# ============================================================================
# Environment: development, demo, staging, production
#   development - Docker dev mode (volume mounts, npm-linked busibox-app)
#   demo        - Docker prod mode (for demos/presentations)
#   staging     - Docker or Proxmox (10.96.201.x network)
#   production  - Docker or Proxmox (10.96.200.x network)
#
# Environment is persisted in .busibox-state (shared with menu and Ansible Makefile)
# If not set, defaults to development for Docker workflows
SAVED_ENV := $(shell grep '^ENVIRONMENT=' .busibox-state 2>/dev/null | cut -d= -f2)
ENV ?= $(if $(SAVED_ENV),$(SAVED_ENV),development)

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
# Base: infrastructure and Python APIs
# Overlay selection based on environment:
#   development -> COMPOSE_DEV (volume mounts, npm link)
#   demo/staging/production -> COMPOSE_PROD (built from GitHub)
COMPOSE_FILE := docker-compose.local.yml
COMPOSE_DEV := docker-compose.dev.yml
COMPOSE_PROD := docker-compose.prod.yml
ENV_FILE := .env.local

# Automatically select overlay based on environment
# development uses dev overlay, everything else uses prod overlay
COMPOSE_OVERLAY = $(if $(filter development,$(ENV)),$(COMPOSE_DEV),$(COMPOSE_PROD))

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
	@echo "  make                         # Interactive menu (recommended)"
	@echo "  make ENV=development         # Start menu with development environment"
	@echo "  make ENV=demo                # Start menu with demo environment"
	@echo "  make ENV=staging             # Start menu with staging environment"
	@echo "  make ENV=production          # Start menu with production environment"
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
	@echo "  Environment-based mode selection (ENV variable):"
	@echo "    make docker-up                         # Start (default: development)"
	@echo "    make docker-up ENV=development         # Dev mode (volume mounts, npm link)"
	@echo "    make docker-up ENV=demo                # Demo mode (prod-like, from GitHub)"
	@echo "    make docker-up SERVICE=ai-portal       # Start specific service"
	@echo ""
	@echo "  Building:"
	@echo "    make docker-build                      # Build for current ENV"
	@echo "    make docker-build ENV=demo             # Build prod-like images"
	@echo "    make docker-build SERVICE=authz-api    # Build specific service"
	@echo "    make docker-build NO_CACHE=1           # Rebuild without cache"
	@echo ""
	@echo "  Other:"
	@echo "    make docker-down                       # Stop all services"
	@echo "    make docker-restart                    # Restart all services"
	@echo "    make docker-ps                         # Show status"
	@echo "    make docker-logs                       # View all logs"
	@echo "    make docker-logs SERVICE=authz-api     # View specific logs"
	@echo "    make docker-clean                      # Remove containers & data"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                    VAULT & ENV MANAGEMENT"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  make vault-generate-env                  # Generate .env.local from vault"
	@echo "  make vault-migrate                       # Migrate .env.local to vault (one-time)"
	@echo "  make vault-sync                          # Sync vault with vault.example.yml"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                         TESTING"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  Docker (local development):"
	@echo "    make test-docker SERVICE=authz         # Run authz tests"
	@echo "    make test-docker SERVICE=agent         # Run agent tests"
	@echo "    make test-docker SERVICE=ai-portal     # Run ai-portal tests"
	@echo "    make test-docker SERVICE=apps          # Run all Node.js app tests"
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
	@echo "  development - Docker dev mode (volume mounts, npm-linked busibox-app)"
	@echo "  demo        - Docker prod mode (apps from GitHub, for presentations)"
	@echo "  staging     - 10.96.201.x network (Docker or Proxmox)"
	@echo "  production  - 10.96.200.x network (Docker or Proxmox)"
	@echo ""
	@echo "  development and demo are always Docker-only."
	@echo "  staging and production can use either Docker or Proxmox backends."
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
	@echo "Services:"
	@echo "  Python APIs: authz, ingest, search, agent"
	@echo "  Node.js apps: ai-portal, agent-manager, apps (both)"
	@echo "  All: all"
	@echo ""
	@echo "Examples:"
	@echo "  make test-docker SERVICE=authz"
	@echo "  make test-docker SERVICE=agent"
	@echo "  make test-docker SERVICE=ai-portal"
	@echo "  make test-docker SERVICE=apps          # Both Node.js apps"
	@echo "  make test-docker SERVICE=all           # Everything"
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

# Start Docker services based on environment
# ENV=development -> dev overlay (volume mounts, npm-linked busibox-app)
# ENV=demo/staging/production -> prod overlay (apps built from GitHub)
docker-up: _ensure-env
	@echo "Starting Docker services (ENV=$(ENV), overlay=$(notdir $(COMPOSE_OVERLAY)))..."
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) up -d $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) up -d
endif
	@echo ""
ifeq ($(ENV),development)
	@echo "Development mode started. Use 'make docker-ps' to check status."
	@echo "Next.js apps are volume-mounted with busibox-app npm-linked."
else
	@echo "$(ENV) mode started. Use 'make docker-ps' to check status."
	@echo "Next.js apps built from GitHub with npm-published busibox-app."
endif

# Legacy alias for explicit prod mode
docker-up-prod: _ensure-env
	$(MAKE) docker-up ENV=demo

# Start Docker services without rebuilding (fast start)
docker-start: _ensure-env
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) up -d --no-build $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) up -d --no-build
endif
	@echo ""
	@echo "Services started ($(ENV) mode). Use 'make docker-ps' to check status."

# Stop Docker services (works for both dev and prod mode)
docker-down:
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_DEV) down 2>/dev/null || true
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_PROD) down 2>/dev/null || true

# Restart Docker services (uses up -d to ensure env vars are reloaded)
docker-restart:
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) up -d --force-recreate $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) up -d --force-recreate
endif

# Restart only API services (fast, preserves infrastructure like embedding-api, milvus, postgres)
# Use this when developing - embedding model stays loaded, so restarts are fast
docker-restart-apis:
	@echo "Restarting API services (infrastructure tier preserved)..."
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) restart authz-api ingest-api ingest-worker search-api agent-api docs-api nginx

# Restart ingest services only
docker-restart-ingest:
	@echo "Restarting ingest services..."
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) restart ingest-api ingest-worker

# Check/generate SSL certificates
ssl-check:
	@if [ ! -f ssl/localhost.crt ] || [ ! -f ssl/localhost.key ]; then \
		echo "[INFO] Generating SSL certificates..."; \
		bash scripts/setup/generate-local-ssl.sh; \
	fi

# Build Docker images based on environment
# ENV=development -> dev overlay, ENV=demo/staging/production -> prod overlay
docker-build: ssl-check _ensure-env
	$(eval GIT_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown"))
	@echo "Building with version: $(GIT_COMMIT) (ENV=$(ENV), overlay=$(notdir $(COMPOSE_OVERLAY)))"
ifdef SERVICE
ifdef NO_CACHE
	GIT_COMMIT=$(GIT_COMMIT) docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) build --no-cache $(SERVICE)
else
	GIT_COMMIT=$(GIT_COMMIT) docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) build $(SERVICE)
endif
	@echo "Recreating container to apply new image..."
	GIT_COMMIT=$(GIT_COMMIT) docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) up -d $(SERVICE)
else
ifdef NO_CACHE
	GIT_COMMIT=$(GIT_COMMIT) docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) build --no-cache
else
	GIT_COMMIT=$(GIT_COMMIT) docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) build
endif
	@echo "Recreating containers to apply new images..."
	GIT_COMMIT=$(GIT_COMMIT) docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) --env-file $(ENV_FILE) up -d
endif

# View Docker logs (uses environment-based overlay)
docker-logs:
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) logs -f $(SERVICE) 2>/dev/null || \
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_DEV) logs -f $(SERVICE) 2>/dev/null || \
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_PROD) logs -f $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) logs -f 2>/dev/null || \
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_DEV) logs -f 2>/dev/null || \
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_PROD) logs -f
endif

# Show Docker service status (uses environment-based overlay)
docker-ps:
	@docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) ps 2>/dev/null || \
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_DEV) ps 2>/dev/null || \
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_PROD) ps

# Clean Docker environment
docker-clean:
	@echo "WARNING: This will remove all containers and volumes!"
	@read -p "Are you sure? (y/N) " confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_DEV) down -v --remove-orphans 2>/dev/null || true; \
		docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_PROD) down -v --remove-orphans 2>/dev/null || true; \
		echo "Cleanup complete."; \
	else \
		echo "Cancelled."; \
	fi

# ============================================================================
# DEMO MODE
# ============================================================================
# One-command demo for investor presentations and air-gap demonstrations.
# Automatically detects system architecture and RAM to select optimal models.
#
# Usage:
#   make demo-warmup   # Pre-download everything (run with network)
#   make demo          # Start the full demo (can run offline after warmup)
#   make demo-clean    # Stop demo and clean up
#   make demo-status   # Show current demo status

# Run the full demo (start all services with local LLM)
# Prerequisites: Docker Desktop, 16GB+ RAM
# For offline mode: run 'make demo-warmup' first
demo:
	@bash scripts/demo/check-prereqs.sh
	@bash scripts/demo/demo.sh

# Pre-download everything for offline demo
# Requires: GitHub authentication (gh auth login)
# Downloads: repos, models (MLX or vLLM), Docker images
demo-warmup:
	@bash scripts/demo/check-prereqs.sh
	@bash scripts/demo/warmup.sh

# Stop demo and optionally remove data
# Use ARGS=--all to remove all data volumes
demo-clean:
	@bash scripts/demo/clean.sh $(ARGS)

# Show demo status (system info, running services)
demo-status:
	@echo ""
	@echo "=== Demo System Info ==="
	@bash scripts/demo/detect-system.sh
	@echo ""
	@echo "=== Docker Services ==="
	@docker compose -f $(COMPOSE_FILE) ps 2>/dev/null || echo "No services running"
	@echo ""

# ============================================================================
# VAULT & ENV MANAGEMENT
# ============================================================================
# Generate .env.local from Ansible vault (single source of truth)
vault-generate-env:
	@bash scripts/vault/generate-env-from-vault.sh

# Migrate existing .env.local to Ansible vault (one-time operation)
vault-migrate:
	@bash scripts/vault/migrate-env-to-vault.sh

# Sync vault structure with vault.example.yml
vault-sync:
	@bash scripts/vault/sync-vault.sh

# Backward compatibility
docker-test: test-docker
