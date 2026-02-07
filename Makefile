.PHONY: menu help setup configure deploy test test-local test-docker test-security mcp \
        docker-up docker-up-prod docker-start docker-down docker-down-all docker-restart docker-restart-apis docker-restart-data docker-build docker-logs docker-ps docker-ps-all docker-clean docker-clean-all \
        vault-generate-env vault-migrate vault-sync ssl-check \
        github-check github-ensure \
        install update manage recover-admin demo warmup demo-clean demo-status \
        docker-deploy docker-deploy-infra docker-deploy-apis docker-deploy-llm docker-deploy-frontend \
        deploy-user-app undeploy-user-app list-user-apps user-app-logs user-app-status \
        mlx-status mlx-start mlx-stop mlx-restart host-agent-status host-agent-start host-agent-stop host-agent-restart

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

# Service for targeted operations (comma-separated for multiple)
SERVICE ?=

# Action for service management (start, stop, restart, logs, redeploy, status)
ACTION ?=

# Inventory (maps to environment for Ansible)
# INV=staging maps to inventory/staging, INV=production maps to inventory/production
INV ?= staging

# Test mode: container (on deployed containers) or local (on your machine)
MODE ?= container

# Additional args (e.g., pytest args)
ARGS ?=

# FAST mode: skip slow/gpu tests (default for local testing)
FAST ?=

# WORKER mode: start local data worker for integration tests
WORKER ?=

# Docker compose configuration
# Base: infrastructure and Python APIs
# Overlay selection based on environment:
#   development -> COMPOSE_DEV (volume mounts, npm link)
#   demo/staging/production -> COMPOSE_GITHUB (built from GitHub)
COMPOSE_FILE := docker-compose.yml
COMPOSE_DEV := docker-compose.local-dev.yml
COMPOSE_GITHUB := docker-compose.github.yml

# Environment-prefixed files (allows multiple installations to coexist)
# ENV=demo        -> .env.demo, .busibox-state-demo
# ENV=development -> .env.dev, .busibox-state-dev
# ENV=staging     -> .env.staging, .busibox-state-staging
# ENV=production  -> .env.prod, .busibox-state-prod
ENV_PREFIX = $(if $(filter demo,$(ENV)),demo,$(if $(filter development,$(ENV)),dev,$(if $(filter staging,$(ENV)),staging,$(if $(filter production,$(ENV)),prod,dev))))
ENV_FILE := .env.$(ENV_PREFIX)
STATE_FILE := .busibox-state-$(ENV_PREFIX)

# Read DEV_APPS_DIR from state file if it exists
# This is set via: make configure -> Docker Configuration -> Configure Dev Apps Directory
DEV_APPS_DIR := $(shell grep -s '^DEV_APPS_DIR=' $(STATE_FILE) 2>/dev/null | cut -d'=' -f2- | tr -d '"' || echo "")
export DEV_APPS_DIR

# Automatically select overlay based on environment
# development uses dev overlay, everything else uses github overlay
COMPOSE_OVERLAY = $(if $(filter development,$(ENV)),$(COMPOSE_DEV),$(COMPOSE_GITHUB))

# ============================================================================
# DOCKER PROJECT NAMING
# ============================================================================
# Each environment gets its own isolated Docker project (stack) with prefixed containers:
#   - demo-busibox    -> demo-postgres, demo-authz-api, etc.
#   - dev-busibox     -> dev-postgres, dev-authz-api, etc.
#   - staging-busibox -> staging-postgres, staging-authz-api, etc.
#   - prod-busibox    -> prod-postgres, prod-authz-api, etc.
#
# This allows multiple environments to coexist on the same Docker host.

# Map ENV to container prefix
CONTAINER_PREFIX = $(if $(filter demo,$(ENV)),demo,$(if $(filter development,$(ENV)),dev,$(if $(filter staging,$(ENV)),staging,$(if $(filter production,$(ENV)),prod,dev))))

# Compose project name (stack name)
COMPOSE_PROJECT = $(CONTAINER_PREFIX)-busibox

# Export for docker-compose and scripts
export COMPOSE_PROJECT_NAME = $(COMPOSE_PROJECT)
export CONTAINER_PREFIX
export BUSIBOX_ENV = $(ENV)

# ============================================================================
# MAIN MENU (Default)
# ============================================================================
# Interactive launcher menu - the main entry point
# Usage: make              # Full interactive menu
#        make ENV=staging  # Start with staging environment selected
menu:
	@bash scripts/make/launcher.sh

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
	@echo "                         MAIN COMMANDS"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  make                         # Interactive launcher menu"
	@echo "  make install                 # Fresh installation wizard"
	@echo "  make install SERVICE=authz   # Deploy specific service (via Ansible)"
	@echo "  make update                  # Update existing installation"
	@echo "  make manage                  # Service management (interactive)"
	@echo "  make manage SERVICE=authz ACTION=restart  # Direct service action"
	@echo "  make test                    # Testing menu"
	@echo ""
	@echo "  Services: postgres, redis, minio, milvus, authz, agent, data,"
	@echo "            search, deploy, docs, embedding, litellm, core-apps, nginx"
	@echo "  Actions:  start, stop, restart, logs, redeploy, status"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                    OTHER COMMANDS"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  setup         - Initial setup (install dependencies)"
	@echo "  configure     - Configure models, GPUs, secrets"
	@echo "  deploy        - Deploy services (via Ansible)"
	@echo "  mcp           - Build MCP server for Cursor AI"
	@echo "  warmup        - Check cache and download missing models"
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
	@echo "  make vault-setup                         # Multi-vault setup wizard"
	@echo "  make vault-setup ARGS='--status'         # Show vault status"
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
	@echo "    WORKER=1    Start local data worker for pipeline tests"
	@echo "    ARGS='...'  Pass pytest arguments"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                 MLX & HOST-AGENT (Apple Silicon)"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "  MLX Server (local LLM on Apple Silicon):"
	@echo "    make mlx-status                # Check MLX server status"
	@echo "    make mlx-start                 # Start MLX server"
	@echo "    make mlx-stop                  # Stop MLX server"
	@echo "    make mlx-restart               # Restart MLX server"
	@echo ""
	@echo "  Host Agent (controls MLX from Docker):"
	@echo "    make host-agent-status         # Check host-agent status"
	@echo "    make host-agent-start          # Start host-agent"
	@echo "    make host-agent-stop           # Stop host-agent"
	@echo "    make host-agent-restart        # Restart host-agent"
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

# Run tests - interactive menu or direct service test
# Interactive: make test              # Opens test menu
# Direct:      make test SERVICE=authz INV=staging
test:
ifdef SERVICE
	@PYTEST_ARGS="$(ARGS)" bash scripts/make/test.sh $(SERVICE) $(INV) $(MODE)
else
	@bash scripts/make/test-menu.sh
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
	@echo "Services: authz, data, search, agent, all"
	@echo "Environments: staging, production"
	@echo ""
	@echo "Examples:"
	@echo "  make test-local SERVICE=agent INV=staging"
	@echo "  make test-local SERVICE=all INV=production"
	@echo "  make test-local SERVICE=agent INV=staging ARGS='-k test_weather'"
	@echo "  make test-local SERVICE=data INV=staging WORKER=1"
	@echo ""
	@exit 1
endif
	@FAST=$${FAST:-1} WORKER=$${WORKER:-0} bash scripts/test/run-local-tests.sh $(SERVICE) $(INV) $(ARGS)

# Bootstrap test databases (schema + OAuth clients + signing keys)
# Run this before running tests to initialize test_authz, test_data, test_agent
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
	@echo "  Python APIs: authz, data, search, agent"
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
# Requires valid GitHub token for private repos
docker-up:
	@echo "Starting Docker services (ENV=$(ENV), overlay=$(notdir $(COMPOSE_OVERLAY)))..."
	$(eval GITHUB_AUTH_TOKEN := $(or $(GITHUB_AUTH_TOKEN),$(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.github.personal_access_token 2>/dev/null || echo ""')))
	$(eval POSTGRES_PASSWORD := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.postgresql.password 2>/dev/null || echo "devpassword"'))
	$(eval MINIO_ACCESS_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.minio.root_user 2>/dev/null || echo "minioadmin"'))
	$(eval MINIO_SECRET_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.minio.root_password 2>/dev/null || echo "minioadmin"'))
	$(eval AUTHZ_MASTER_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.authz_master_key 2>/dev/null || echo "local-master-key-change-in-production"'))
	@if [ -z "$(GITHUB_AUTH_TOKEN)" ]; then \
		echo "[ERROR] No GitHub token found"; \
		echo ""; \
		echo "Set GITHUB_AUTH_TOKEN with: export GITHUB_AUTH_TOKEN=ghp_your_token"; \
		echo "Create a token at: https://github.com/settings/tokens/new"; \
		echo "Required scopes: repo, read:packages"; \
		exit 1; \
	fi
ifneq ($(DEV_APPS_DIR),)
	@echo "Dev Apps Directory: $(DEV_APPS_DIR)"
endif
ifdef SERVICE
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" DEV_APPS_DIR="$(DEV_APPS_DIR)" BUSIBOX_HOST_PATH="$(PWD)" CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) up -d $(SERVICE)
else
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" DEV_APPS_DIR="$(DEV_APPS_DIR)" BUSIBOX_HOST_PATH="$(PWD)" CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) up -d
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
docker-start:
	$(eval POSTGRES_PASSWORD := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.postgresql.password 2>/dev/null || echo "devpassword"'))
	$(eval MINIO_ACCESS_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.minio.root_user 2>/dev/null || echo "minioadmin"'))
	$(eval MINIO_SECRET_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.minio.root_password 2>/dev/null || echo "minioadmin"'))
	$(eval AUTHZ_MASTER_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.authz_master_key 2>/dev/null || echo "local-master-key-change-in-production"'))
ifdef SERVICE
	DEV_APPS_DIR="$(DEV_APPS_DIR)" BUSIBOX_HOST_PATH="$(PWD)" CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) up -d --no-build $(SERVICE)
else
	DEV_APPS_DIR="$(DEV_APPS_DIR)" BUSIBOX_HOST_PATH="$(PWD)" CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) up -d --no-build
endif
	@echo ""
	@echo "Services started ($(ENV) mode). Use 'make docker-ps' to check status."

# Stop Docker services
# Uses COMPOSE_PROJECT_NAME to stop the correct environment's containers
# Usage: make docker-down ENV=demo   # Stops demo-busibox stack
#        make docker-down            # Stops dev-busibox stack (default)
docker-down:
	@echo "Stopping $(COMPOSE_PROJECT) containers..."
	docker compose -p $(COMPOSE_PROJECT) down 2>/dev/null || \
	(DEV_APPS_DIR="$(DEV_APPS_DIR)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) down 2>/dev/null || true)

# Stop ALL busibox environments (demo, dev, staging, prod)
docker-down-all:
	@echo "Stopping all Busibox environments..."
	docker compose -p demo-busibox down 2>/dev/null || true
	docker compose -p dev-busibox down 2>/dev/null || true
	docker compose -p staging-busibox down 2>/dev/null || true
	docker compose -p prod-busibox down 2>/dev/null || true

# Restart Docker services (simple restart, no recreation)
docker-restart:
	$(eval GITHUB_AUTH_TOKEN := $(or $(GITHUB_AUTH_TOKEN),$(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.github.personal_access_token 2>/dev/null || echo ""')))
	$(eval POSTGRES_PASSWORD := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.postgresql.password 2>/dev/null || echo "devpassword"'))
	$(eval MINIO_ACCESS_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.minio.root_user 2>/dev/null || echo "minioadmin"'))
	$(eval MINIO_SECRET_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.minio.root_password 2>/dev/null || echo "minioadmin"'))
	$(eval AUTHZ_MASTER_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.authz_master_key 2>/dev/null || echo "local-master-key-change-in-production"'))
ifdef SERVICE
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" DEV_APPS_DIR="$(DEV_APPS_DIR)" CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) restart $(SERVICE)
else
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" DEV_APPS_DIR="$(DEV_APPS_DIR)" CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) restart
endif

# Restart only API services (fast, preserves infrastructure like embedding-api, milvus, postgres)
# Use this when developing - embedding model stays loaded, so restarts are fast
docker-restart-apis:
	@echo "Restarting API services (infrastructure tier preserved)..."
	CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) restart authz-api deploy-api data-api data-worker search-api agent-api docs-api nginx

# Restart data services only
docker-restart-data:
	@echo "Restarting data services..."
	CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) restart data-api data-worker

# Check/generate SSL certificates
ssl-check:
	@if [ ! -f ssl/localhost.crt ] || [ ! -f ssl/localhost.key ]; then \
		echo "[INFO] Generating SSL certificates..."; \
		bash scripts/setup/generate-local-ssl.sh; \
	fi

# Check GitHub token is available and valid
github-check:
	@bash scripts/lib/github.sh check
	$(eval GITHUB_AUTH_TOKEN := $(or $(GITHUB_AUTH_TOKEN),$(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.github.personal_access_token 2>/dev/null || echo ""')))
	@if [ -n "$(GITHUB_AUTH_TOKEN)" ]; then \
		export GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)"; \
	fi

# Ensure GitHub token is available (interactive prompt if needed)
github-ensure:
	@bash scripts/lib/github.sh ensure
	$(eval GITHUB_AUTH_TOKEN := $(or $(GITHUB_AUTH_TOKEN),$(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.github.personal_access_token 2>/dev/null || echo ""')))
	@if [ -n "$(GITHUB_AUTH_TOKEN)" ]; then \
		export GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)"; \
	fi

# Build Docker images based on environment
# ENV=development -> dev overlay, ENV=demo/staging/production -> prod overlay
# Requires valid GitHub token for private repos
docker-build: ssl-check
	$(eval GIT_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo "unknown"))
	$(eval GITHUB_AUTH_TOKEN := $(or $(GITHUB_AUTH_TOKEN),$(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.github.personal_access_token 2>/dev/null || echo ""')))
	$(eval POSTGRES_PASSWORD := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.postgresql.password 2>/dev/null || echo "devpassword"'))
	$(eval MINIO_ACCESS_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.minio.root_user 2>/dev/null || echo "minioadmin"'))
	$(eval MINIO_SECRET_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.minio.root_password 2>/dev/null || echo "minioadmin"'))
	$(eval AUTHZ_MASTER_KEY := $(shell bash -c 'source scripts/lib/vault.sh >/dev/null 2>&1 && set_vault_environment $(ENV_PREFIX) >/dev/null 2>&1 && ensure_vault_access >/dev/null 2>&1 && get_vault_secret secrets.authz_master_key 2>/dev/null || echo "local-master-key-change-in-production"'))
	@if [ -z "$(GITHUB_AUTH_TOKEN)" ]; then \
		echo "[ERROR] No GitHub token found"; \
		echo ""; \
		echo "Set GITHUB_AUTH_TOKEN with: export GITHUB_AUTH_TOKEN=ghp_your_token"; \
		echo "Create a token at: https://github.com/settings/tokens/new"; \
		echo "Required scopes: repo, read:packages"; \
		exit 1; \
	fi
	@echo "Building with version: $(GIT_COMMIT) (ENV=$(ENV), overlay=$(notdir $(COMPOSE_OVERLAY)))"
ifdef SERVICE
ifdef NO_CACHE
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" GIT_COMMIT=$(GIT_COMMIT) CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) build --no-cache $(SERVICE)
else
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" GIT_COMMIT=$(GIT_COMMIT) CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) build $(SERVICE)
endif
	@echo "Recreating container to apply new image..."
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" GIT_COMMIT=$(GIT_COMMIT) BUSIBOX_HOST_PATH="$(PWD)" CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) up -d --no-deps $(SERVICE)
else
ifdef NO_CACHE
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" GIT_COMMIT=$(GIT_COMMIT) CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) build --no-cache
else
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" GIT_COMMIT=$(GIT_COMMIT) CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) build
endif
	@echo "Recreating containers to apply new images..."
	GITHUB_AUTH_TOKEN="$(GITHUB_AUTH_TOKEN)" GIT_COMMIT=$(GIT_COMMIT) BUSIBOX_HOST_PATH="$(PWD)" CONTAINER_PREFIX=$(CONTAINER_PREFIX) COMPOSE_PROJECT_NAME=$(COMPOSE_PROJECT) POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" MINIO_ACCESS_KEY="$(MINIO_ACCESS_KEY)" MINIO_SECRET_KEY="$(MINIO_SECRET_KEY)" AUTHZ_MASTER_KEY="$(AUTHZ_MASTER_KEY)" docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) up -d
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

# Show Docker service status
# Usage: make docker-ps ENV=demo  # Shows demo-busibox stack
#        make docker-ps           # Shows dev-busibox stack (default)
docker-ps:
	@echo "Project: $(COMPOSE_PROJECT)"
	@docker compose -p $(COMPOSE_PROJECT) ps 2>/dev/null || \
	docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_OVERLAY) ps 2>/dev/null

# Show status of ALL busibox environments
docker-ps-all:
	@echo "=== Demo Environment ===" && docker compose -p demo-busibox ps 2>/dev/null || true
	@echo ""
	@echo "=== Development Environment ===" && docker compose -p dev-busibox ps 2>/dev/null || true
	@echo ""
	@echo "=== Staging Environment ===" && docker compose -p staging-busibox ps 2>/dev/null || true
	@echo ""
	@echo "=== Production Environment ===" && docker compose -p prod-busibox ps 2>/dev/null || true

# Clean Docker environment for current ENV
# Usage: make docker-clean ENV=demo  # Cleans demo-busibox stack
docker-clean:
	@echo "WARNING: This will remove $(COMPOSE_PROJECT) containers and volumes!"
	@read -p "Are you sure? (y/N) " confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		docker compose -p $(COMPOSE_PROJECT) down -v --remove-orphans 2>/dev/null || true; \
		echo "Cleanup complete for $(COMPOSE_PROJECT)."; \
	else \
		echo "Cancelled."; \
	fi

# Clean ALL busibox Docker environments
docker-clean-all:
	@echo "WARNING: This will remove ALL Busibox environments (demo, dev, staging, prod)!"
	@read -p "Are you sure? (y/N) " confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		docker compose -p demo-busibox down -v --remove-orphans 2>/dev/null || true; \
		docker compose -p dev-busibox down -v --remove-orphans 2>/dev/null || true; \
		docker compose -p staging-busibox down -v --remove-orphans 2>/dev/null || true; \
		docker compose -p prod-busibox down -v --remove-orphans 2>/dev/null || true; \
		echo "All environments cleaned."; \
	else \
		echo "Cancelled."; \
	fi

# ============================================================================
# INSTALLATION
# ============================================================================
# Unified install with interactive wizard or demo mode.
# All management after install is via web UI (AI Portal).
#
# Usage:
#   make install         # Full wizard
#   make demo            # Demo mode (auto-configure)
#   make warmup          # Check cache and download missing models
#   make warmup FORCE=1  # Re-download (interactive selection)

# Interactive install with wizard OR deploy specific service
# Usage: make install                      # Install menu (Continue/Full/Clean if existing)
#        make install VERBOSE=1            # Show all logs
#        make install SERVICE=authz        # Deploy specific service (uses Ansible)
#        make install SERVICE=authz,agent  # Deploy multiple services
#
# When called without SERVICE=, shows the same install options as `make` -> Install:
# - Fresh install if no existing installation
# - Continue/Full/Clean menu if existing installation detected
install:
ifdef SERVICE
	@bash scripts/make/service-deploy.sh "$(SERVICE)"
else
	@USE_ANSIBLE_FOR_DOCKER=$(USE_ANSIBLE) bash scripts/make/install-menu.sh $(if $(VERBOSE),-v)
endif

# Update existing installation
# Preserves: PostgreSQL, Redis, MinIO, Milvus, model cache, user apps
# Updates: APIs, apps, nginx, runs migrations
# Supports both Docker and Proxmox (auto-detected)
#
# Usage: make update                       # Interactive update (auto-detect platform)
#        make update ENV=staging           # Update staging environment
#        make update VERBOSE=1             # Show all logs
#        make update REBUILD=1             # Force rebuild all containers (Docker only)
#        make update USE_ANSIBLE=1         # Use Ansible for Docker deployment
update:
	@USE_ANSIBLE_FOR_DOCKER=$(USE_ANSIBLE) ENV=$(ENV) INV=$(INV) bash scripts/make/update.sh $(if $(VERBOSE),-v) $(if $(REBUILD),--rebuild-all)

# Service management menu OR direct service action
# Interactive menu for stopping/starting/redeploying services
# Supports both Docker and Proxmox backends
#
# Usage: make manage                                   # Interactive menu
#        make manage SERVICE=authz ACTION=restart      # Restart authz service
#        make manage SERVICE=authz,agent ACTION=stop   # Stop multiple services
#        make manage SERVICE=authz ACTION=logs         # View logs
#
# Actions: start, stop, restart, logs, redeploy, status
manage:
ifdef SERVICE
	@bash scripts/make/service-manage.sh "$(SERVICE)" "$(ACTION)"
else
	@bash scripts/make/manage.sh
endif

# Generate recovery magic link for admin access
# Use when browser/passkey access is lost
recover-admin:
	@bash scripts/make/recover-admin.sh

# ============================================================================
# ANSIBLE-BASED DOCKER DEPLOYMENT
# ============================================================================
# These targets use Ansible for Docker deployment, providing:
# - Idempotent operations
# - Unified patterns with LXC deployment
# - Better secrets management via Ansible Vault
#
# Usage: make docker-deploy              # Full deployment via Ansible
#        make docker-deploy-infra        # Deploy infrastructure only
#        make docker-deploy-apis         # Deploy API services
#        make docker-deploy-frontend     # Deploy frontend apps

docker-deploy:
	@cd provision/ansible && $(MAKE) docker CONTAINER_PREFIX=$(CONTAINER_PREFIX) BUSIBOX_ENV=$(ENV)

docker-deploy-infra:
	@cd provision/ansible && $(MAKE) docker-infrastructure CONTAINER_PREFIX=$(CONTAINER_PREFIX) BUSIBOX_ENV=$(ENV)

docker-deploy-apis:
	@cd provision/ansible && $(MAKE) docker-apis CONTAINER_PREFIX=$(CONTAINER_PREFIX) BUSIBOX_ENV=$(ENV)

docker-deploy-llm:
	@cd provision/ansible && $(MAKE) docker-llm CONTAINER_PREFIX=$(CONTAINER_PREFIX) BUSIBOX_ENV=$(ENV)

docker-deploy-frontend:
	@cd provision/ansible && $(MAKE) docker-frontend CONTAINER_PREFIX=$(CONTAINER_PREFIX) BUSIBOX_ENV=$(ENV)

# ============================================================================
# USER APP DEPLOYMENT
# ============================================================================
# Deploy user/external applications to the user-apps container.
# These are untrusted apps that run in an isolated container.
#
# Usage:
#   make deploy-user-app APP_ID=myapp REPO=owner/repo
#   make deploy-user-app APP_ID=myapp REPO=owner/repo BRANCH=develop
#   make deploy-user-app APP_ID=myapp REPO=owner/repo ENV=staging
#   make undeploy-user-app APP_ID=myapp
#
# Variables:
#   APP_ID   - Unique identifier for the app (required)
#   REPO     - GitHub repository in owner/repo format (required for deploy)
#   BRANCH   - Branch to deploy (default: main)
#   PORT     - Port the app listens on (default: auto-assigned)
#   ENV      - Target environment: docker, staging, production

# App deployment variables
APP_ID ?=
REPO ?=
BRANCH ?= main
APP_PORT ?=

.PHONY: deploy-user-app undeploy-user-app list-user-apps user-app-logs user-app-status

deploy-user-app:
	@if [ -z "$(APP_ID)" ]; then echo "ERROR: APP_ID is required"; exit 1; fi
	@if [ -z "$(REPO)" ]; then echo "ERROR: REPO is required (format: owner/repo)"; exit 1; fi
	@echo "Deploying user app: $(APP_ID) from $(REPO)"
	@cd provision/ansible && ansible-playbook \
		-i inventory/$(if $(filter docker,$(ENV)),docker,$(if $(filter staging production,$(ENV)),$(ENV),docker)) \
		user-app-deploy.yml \
		-e "app_id=$(APP_ID)" \
		-e "github_repo=$(REPO)" \
		-e "deploy_branch=$(BRANCH)" \
		$(if $(APP_PORT),-e "app_port=$(APP_PORT)") \
		$(if $(GITHUB_TOKEN),-e "github_token=$(GITHUB_TOKEN)") \
		$(EXTRA_ARGS)

undeploy-user-app:
	@if [ -z "$(APP_ID)" ]; then echo "ERROR: APP_ID is required"; exit 1; fi
	@echo "Undeploying user app: $(APP_ID)"
	@cd provision/ansible && ansible-playbook \
		-i inventory/$(if $(filter docker,$(ENV)),docker,$(if $(filter staging production,$(ENV)),$(ENV),docker)) \
		user-app-undeploy.yml \
		-e "app_id=$(APP_ID)" \
		$(EXTRA_ARGS)

list-user-apps:
	@echo "Listing deployed user apps..."
	@cd provision/ansible && ansible-playbook \
		-i inventory/$(if $(filter docker,$(ENV)),docker,$(if $(filter staging production,$(ENV)),$(ENV),docker)) \
		user-app-list.yml \
		$(EXTRA_ARGS)

user-app-logs:
	@if [ -z "$(APP_ID)" ]; then echo "ERROR: APP_ID is required"; exit 1; fi
	@if [ "$(ENV)" = "docker" ] || [ -z "$(ENV)" ]; then \
		docker exec $(CONTAINER_PREFIX)-user-apps sh -c "cd /srv/apps/$(APP_ID) && tail -f logs/*.log 2>/dev/null || echo 'No logs found'"; \
	else \
		cd provision/ansible && ansible user_apps -i inventory/$(ENV) -m shell -a "cd /srv/apps/$(APP_ID) && tail -100 logs/*.log 2>/dev/null || journalctl -u $(APP_ID) -n 100"; \
	fi

user-app-status:
	@if [ -z "$(APP_ID)" ]; then echo "ERROR: APP_ID is required"; exit 1; fi
	@if [ "$(ENV)" = "docker" ] || [ -z "$(ENV)" ]; then \
		docker exec $(CONTAINER_PREFIX)-user-apps sh -c "ps aux | grep -E '$(APP_ID)|node' | grep -v grep || echo 'App not running'"; \
	else \
		cd provision/ansible && ansible user_apps -i inventory/$(ENV) -m shell -a "systemctl status $(APP_ID) 2>/dev/null || echo 'Service not found'"; \
	fi

# ============================================================================
# MODEL WARMUP
# ============================================================================
# Pre-download models to cache for fast startup and offline use.
# Downloads: FastEmbed embedding model + MLX LLM models (Apple Silicon only)

# Check cache status and download any missing models
# Use --force to re-download (interactive model selection)
warmup:
	@bash scripts/make/warmup.sh $(if $(FORCE),--force)

# ============================================================================
# DEMO MODE
# ============================================================================
# One-command demo for investor presentations and air-gap demonstrations.
# Uses unified install with demo preset (local Docker, auto-detect LLM).

# Run demo (auto-configures everything)
# Prerequisites: Docker Desktop, 16GB+ RAM
# For offline mode: run 'make warmup' first
demo:
	@bash scripts/make/install.sh --demo --no-prompt $(if $(VERBOSE),-v)

# Stop demo environment and remove containers
# Use ARGS=--volumes to also remove data volumes
demo-clean:
	@echo "Stopping demo-busibox containers..."
	@docker compose -p demo-busibox down $(if $(findstring --volumes,$(ARGS)),-v) 2>/dev/null || true
	@echo "Demo environment cleaned."

# Show demo status (system info, running services)
demo-status:
	@echo ""
	@echo "=== Demo System Info ==="
	@bash scripts/llm/detect-backend.sh 2>/dev/null || echo "Backend: cloud"
	@echo ""
	@bash scripts/llm/get-memory-tier.sh 2>/dev/null || echo "Tier: minimal"
	@echo ""
	@echo "=== Demo Docker Services ==="
	@docker compose -p demo-busibox ps 2>/dev/null || echo "No demo services running"
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

# Multi-vault setup: Create environment-specific vault files
# Usage: make vault-setup           # Interactive wizard
#        make vault-setup --status  # Show vault status
vault-setup:
	@bash scripts/vault-migrate.sh $(ARGS)

# ============================================================================
# MLX & HOST-AGENT MANAGEMENT (Apple Silicon only)
# ============================================================================
# MLX runs on the host machine (not in Docker) for best performance.
# The host-agent provides an HTTP API to control MLX from Docker containers.

# Check MLX server status
mlx-status:
	@echo "=== MLX Server Status ==="
	@if curl -sf http://localhost:8080/health >/dev/null 2>&1; then \
		echo "MLX Server: Running (port 8080)"; \
		curl -sf http://localhost:8080/v1/models 2>/dev/null | head -5 || true; \
	else \
		echo "MLX Server: Not running"; \
	fi
	@echo ""
	@echo "=== Host Agent Status ==="
	@if curl -sf http://localhost:8089/health >/dev/null 2>&1; then \
		echo "Host Agent: Running (port 8089)"; \
		curl -sf http://localhost:8089/mlx/status 2>/dev/null || true; \
	else \
		echo "Host Agent: Not running"; \
	fi

# Start MLX server (via host-agent or directly)
# Reads HOST_AGENT_TOKEN from .env.dev for authentication
mlx-start:
	@echo "Starting MLX server..."
	@TOKEN=$$(grep -s '^HOST_AGENT_TOKEN=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
	if curl -sf http://localhost:8089/health >/dev/null 2>&1; then \
		echo "Using host-agent to start MLX..."; \
		if [ -n "$$TOKEN" ]; then \
			curl -sf -X POST http://localhost:8089/mlx/start \
				-H "Content-Type: application/json" \
				-H "Authorization: Bearer $$TOKEN" \
				-d '{"model_type": "agent"}' && echo "MLX server started" || echo "Failed - check host-agent logs"; \
		else \
			echo "Warning: HOST_AGENT_TOKEN not found in $(ENV_FILE)"; \
			curl -sf -X POST http://localhost:8089/mlx/start \
				-H "Content-Type: application/json" \
				-d '{"model_type": "agent"}' || echo "Failed - authentication may be required"; \
		fi; \
	else \
		echo "Host-agent not running. Starting MLX directly..."; \
		bash scripts/llm/start-mlx-server.sh; \
	fi

# Stop MLX server
mlx-stop:
	@echo "Stopping MLX server..."
	@TOKEN=$$(grep -s '^HOST_AGENT_TOKEN=' $(ENV_FILE) 2>/dev/null | cut -d= -f2); \
	if curl -sf http://localhost:8089/health >/dev/null 2>&1; then \
		if [ -n "$$TOKEN" ]; then \
			curl -sf -X POST http://localhost:8089/mlx/stop \
				-H "Authorization: Bearer $$TOKEN" && echo "MLX server stopped" || echo "Failed - check host-agent logs"; \
		else \
			curl -sf -X POST http://localhost:8089/mlx/stop || echo "Failed - authentication may be required"; \
		fi; \
	else \
		pkill -f "mlx_lm.server" 2>/dev/null && echo "MLX server stopped" || echo "MLX server not running"; \
	fi

# Restart MLX server
mlx-restart: mlx-stop
	@sleep 2
	@$(MAKE) mlx-start

# Check host-agent status
host-agent-status:
	@echo "=== Host Agent Status ==="
	@if curl -sf http://localhost:8089/health >/dev/null 2>&1; then \
		echo "Status: Running (port 8089)"; \
		curl -sf http://localhost:8089/health 2>/dev/null; \
	else \
		echo "Status: Not running"; \
		echo ""; \
		echo "Start with: make host-agent-start"; \
	fi

# Start host-agent (runs in background)
host-agent-start:
	@echo "Starting host-agent..."
	@if curl -sf http://localhost:8089/health >/dev/null 2>&1; then \
		echo "Host-agent is already running."; \
	else \
		bash scripts/host-agent/install-host-agent.sh; \
		sleep 2; \
		if curl -sf http://localhost:8089/health >/dev/null 2>&1; then \
			echo "Host-agent started successfully."; \
		else \
			echo "Failed to start host-agent. Check logs."; \
		fi; \
	fi

# Stop host-agent
host-agent-stop:
	@echo "Stopping host-agent..."
	@pkill -f "host-agent.py" 2>/dev/null || echo "Host-agent not running"

# Restart host-agent
host-agent-restart: host-agent-stop
	@sleep 1
	@$(MAKE) host-agent-start

# Backward compatibility
docker-test: test-docker
