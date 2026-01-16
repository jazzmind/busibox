.PHONY: menu help setup configure deploy test test-local test-docker test-security mcp \
        docker-up docker-down docker-restart docker-build docker-logs docker-ps docker-clean ssl-check

# Default target - interactive menu
.DEFAULT_GOAL := menu

# Variables for direct test commands
# Usage: make test SERVICE=authz INV=test
#        make test-docker SERVICE=authz ARGS='-k test_health'
#        make test-local SERVICE=authz INV=test ARGS='-m pvt'
SERVICE ?=
INV ?= test
MODE ?= container
ARGS ?=
# FAST mode: skip slow/gpu tests
# - test-docker/test-local default to FAST=1 (skip slow tests for faster iteration)
# - test (on Proxmox) defaults to FAST=0 (run all tests on containers)
FAST ?=
# WORKER mode: start local ingest worker for integration tests
# - Set WORKER=1 to start a local worker for full pipeline tests
WORKER ?=

# Docker compose configuration
COMPOSE_FILE := docker-compose.local.yml
ENV_FILE := .env.local

# Interactive menu (default when running just 'make')
menu:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                         Busibox Main Menu                            ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "  1) Setup      - Initial setup (Proxmox host + LXC containers)"
	@echo "  2) Configure  - Configure models, GPUs, and containers"
	@echo "  3) Deploy     - Deploy services with Ansible"
	@echo "  4) Test       - Run tests (infrastructure and services)"
	@echo "  5) MCP        - Build MCP server for Cursor AI"
	@echo "  6) Help       - Show detailed help"
	@echo "  Q) Quit"
	@echo ""
	@read -p "Select option [1-6, Q]: " choice; \
	case "$$choice" in \
		1) $(MAKE) setup ;; \
		2) $(MAKE) configure ;; \
		3) $(MAKE) deploy ;; \
		4) $(MAKE) test ;; \
		5) $(MAKE) mcp ;; \
		6) $(MAKE) help ;; \
		[Qq]) echo "Exiting..." ;; \
		*) echo "Invalid choice" ;; \
	esac

help:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                    Busibox - Interactive Commands                    ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Available targets:"
	@echo "  menu          - Interactive menu (default)"
	@echo "  setup         - Initial setup (Proxmox host + LXC containers)"
	@echo "  configure     - Configure models, GPUs, and containers"
	@echo "  deploy        - Deploy services with Ansible"
	@echo "  test          - Run tests on Proxmox containers (interactive or direct)"
	@echo "  test-docker   - Run tests locally against Docker services"
	@echo "  test-local    - Run tests locally against Proxmox backends"
	@echo "  test-security - Run API security tests (fuzzing, OWASP)"
	@echo "  mcp           - Build MCP server for Cursor AI"
	@echo "  help          - Show this help message"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                    TESTING OVERVIEW"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "Choose the right test command based on your environment:"
	@echo ""
	@echo "  test-docker   Run tests on your LOCAL MACHINE against DOCKER services"
	@echo "                Use this for local development with 'make docker-up'"
	@echo "                Example: make test-docker SERVICE=agent"
	@echo ""
	@echo "  test-local    Run tests on your LOCAL MACHINE against PROXMOX backends"
	@echo "                Use this when Proxmox containers are running remotely"
	@echo "                Example: make test-local SERVICE=agent INV=test"
	@echo ""
	@echo "  test          Run tests INSIDE PROXMOX containers (SSH execution)"
	@echo "                Use this for CI/CD or full integration testing"
	@echo "                Example: make test SERVICE=agent INV=test"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                    LOCAL DOCKER DEVELOPMENT"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "Docker commands for local development (mirrors Proxmox environment):"
	@echo ""
	@echo "  make docker-up                              # Start backend services"
	@echo "  make docker-up SERVICE=authz-api            # Start specific service"
	@echo "  make docker-down                            # Stop all services"
	@echo "  make docker-restart                         # Restart all services"
	@echo "  make docker-restart SERVICE=authz-api       # Restart specific service"
	@echo "  make docker-build                           # Build backend images (cached)"
	@echo "  make docker-build SERVICE=authz-api         # Build specific image"
	@echo "  make docker-build NO_CACHE=1                # Force rebuild without cache"
	@echo "  make docker-logs                            # View all logs"
	@echo "  make docker-logs SERVICE=authz-api          # View specific logs"
	@echo "  make docker-ps                              # Show service status"
	@echo "  make docker-clean                           # Remove containers & volumes"
	@echo ""
	@echo "Docker Testing (run tests against local Docker):"
	@echo "  make test-docker SERVICE=authz              # Run authz tests"
	@echo "  make test-docker SERVICE=agent              # Run agent tests"
	@echo "  make test-docker SERVICE=all                # Run all service tests"
	@echo ""
	@echo "Docker Quick Start (Hybrid Mode - Recommended):"
	@echo "  1. make docker-build                        # Build backend images"
	@echo "  2. make docker-up                           # Start backend services"
	@echo "  3. cd ../ai-portal && npm run dev           # Run frontend locally"
	@echo "  4. cd ../agent-manager && npm run dev       # Run agent-manager locally"
	@echo "  5. Open https://localhost/portal            # Access via nginx"
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                    TEST FILTERING (ARGS)"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "Pass pytest arguments using ARGS. Use single quotes for the entire value:"
	@echo ""
	@echo "  # Filter by test name pattern (use single quotes around the value)"
	@echo "  make test-docker SERVICE=agent ARGS='-k test_health'"
	@echo "  make test-docker SERVICE=agent ARGS='-k test_weather'"
	@echo ""
	@echo "  # Filter by pytest marker"
	@echo "  make test-docker SERVICE=agent ARGS='-m pvt'"
	@echo "  make test-docker SERVICE=agent ARGS='-m integration'"
	@echo ""
	@echo "  # Combine multiple filters (use single quotes, no 'or' keyword)"
	@echo "  make test-docker SERVICE=agent ARGS='-k test_weather -v --tb=short'"
	@echo ""
	@echo "  # Run specific test file"
	@echo "  make test-docker SERVICE=agent ARGS='tests/unit/test_base_agent.py'"
	@echo ""
	@echo "  # Verbose output with short tracebacks"
	@echo "  make test-docker SERVICE=agent ARGS='-v --tb=short'"
	@echo ""
	@echo "  # Run ALL tests including slow/gpu (FAST=0 disables skip filter)"
	@echo "  make test-docker SERVICE=agent FAST=0"
	@echo ""
	@echo "  WARNING: ARGS with 'or' keyword requires escaping - prefer running"
	@echo "  separate test commands or use a specific test file path instead."
	@echo ""
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo "                    PROXMOX DEPLOYMENT"
	@echo "═══════════════════════════════════════════════════════════════════════"
	@echo ""
	@echo "Testing (on Proxmox containers):"
	@echo "  make test                                 # Interactive menu"
	@echo "  make test SERVICE=authz INV=test          # Run authz tests on test containers"
	@echo "  make test SERVICE=ingest INV=test         # Run ingest tests on test containers"
	@echo "  make test SERVICE=search INV=test         # Run search tests on test containers"
	@echo "  make test SERVICE=agent INV=test          # Run agent tests on test containers"
	@echo ""
	@echo "Local Testing (run tests on your machine against Proxmox backends):"
	@echo "  make test-local SERVICE=authz INV=test       # Run authz tests locally"
	@echo "  make test-local SERVICE=ingest INV=test      # Run ingest tests locally"
	@echo "  make test-local SERVICE=search INV=test      # Run search tests locally"
	@echo "  make test-local SERVICE=agent INV=test       # Run agent tests locally"
	@echo "  make test-local SERVICE=all INV=test         # Run all tests locally"
	@echo ""
	@echo "Worker Tests (full pipeline with local worker):"
	@echo "  make test-local SERVICE=ingest WORKER=1      # Start local worker for integration tests"
	@echo "  make test-local SERVICE=ingest WORKER=1 FAST=0  # Full pipeline with all tests"
	@echo ""
	@echo "Note: test-docker/test-local default to FAST=1 (skips slow tests)"
	@echo "      test (on containers) runs ALL tests by default"
	@echo "      WORKER=1 starts a local ingest worker for full pipeline tests"
	@echo ""
	@echo "Quick Start (Proxmox):"
	@echo "  1. make setup      # On Proxmox host"
	@echo "  2. make configure  # Configure models/GPUs"
	@echo "  3. make deploy     # Deploy services"
	@echo "  4. make test       # Verify deployment"
	@echo ""
	@echo "All commands are interactive and will guide you through the process."
	@echo ""

setup:
	@bash scripts/make/setup.sh

configure:
	@bash scripts/make/configure.sh

# Deploy services - interactive menu or direct command
# Interactive: make deploy
# Direct:      make deploy SERVICE=authz INV=test
deploy:
ifdef SERVICE
	@bash scripts/make/deploy.sh $(SERVICE) $(INV)
else
	@bash scripts/make/deploy.sh
endif

# Run tests - interactive menu or direct command
# Interactive: make test
# Direct:      make test SERVICE=authz INV=test
#              make test SERVICE=authz INV=test MODE=local
test:
ifdef SERVICE
	@PYTEST_ARGS="$(ARGS)" bash scripts/make/test.sh $(SERVICE) $(INV) $(MODE)
else
	@bash scripts/make/test.sh
endif

# Run tests locally against remote Proxmox container backends
# Usage: make test-local SERVICE=authz INV=test
#        make test-local SERVICE=authz INV=test ARGS='-k test_health'
#        make test-local SERVICE=search INV=test FAST=0  (run ALL tests including slow)
# Note: FAST=1 is the default for test-local (skips slow/gpu tests for faster iteration)
test-local:
ifndef SERVICE
	@echo ""
	@echo "Error: SERVICE is required"
	@echo ""
	@echo "Usage: make test-local SERVICE=<service> INV=<env> [ARGS='<pytest args>'] [FAST=0]"
	@echo ""
	@echo "Available services: authz, ingest, search, agent, all"
	@echo "Available environments (INV): test, production"
	@echo ""
	@echo "Examples:"
	@echo "  make test-local SERVICE=agent INV=test             # Run all agent tests"
	@echo "  make test-local SERVICE=agent INV=test ARGS='-k test_weather'"
	@echo "  make test-local SERVICE=agent INV=test ARGS='-v --tb=short'"
	@echo "  make test-local SERVICE=agent INV=test FAST=0      # Include slow/gpu tests"
	@echo "  make test-local SERVICE=ingest INV=test WORKER=1   # Start local worker"
	@echo ""
	@echo "ARGS Quoting:"
	@echo "  Use single quotes around the ARGS value: ARGS='-k test_name'"
	@echo ""
	@echo "Note: This runs tests against PROXMOX backends (requires vault access)"
	@echo "      For Docker testing, use: make test-docker SERVICE=agent"
	@exit 1
endif
	@FAST=$${FAST:-1} WORKER=$${WORKER:-0} bash scripts/test/run-local-tests.sh $(SERVICE) $(INV) $(ARGS)

test-security:
	@bash tests/security/run_tests.sh

mcp:
	@bash scripts/make/mcp.sh

# =============================================================================
# DOCKER LOCAL DEVELOPMENT
# =============================================================================
# These commands manage the local Docker development environment.
# This mirrors the Proxmox deployment but runs everything in Docker.

# Start all Docker services (infrastructure + APIs)
# Usage: make docker-up
#        make docker-up SERVICE=authz-api  # Start specific service
docker-up:
	@if [ ! -f $(ENV_FILE) ]; then \
		echo "Creating $(ENV_FILE) from env.local.example..."; \
		cp env.local.example $(ENV_FILE); \
		echo "Edit $(ENV_FILE) to add your API keys (OPENAI_API_KEY, etc.)"; \
	fi
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) up -d $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) up -d
endif
	@echo ""
	@echo "Services started. Use 'make docker-ps' to check status."
	@echo "Use 'make docker-logs' to view logs."

# Stop all Docker services
# Usage: make docker-down
docker-down:
	docker compose -f $(COMPOSE_FILE) down

# Restart Docker services
# Usage: make docker-restart                    # Restart all
#        make docker-restart SERVICE=authz-api  # Restart specific service
docker-restart:
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) restart $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) --env-file $(ENV_FILE) restart
endif

# Ensure SSL certificates exist for local nginx
ssl-check:
	@if [ ! -f ssl/localhost.crt ] || [ ! -f ssl/localhost.key ]; then \
		echo "[INFO] SSL certificates not found, generating..."; \
		bash scripts/setup/generate-local-ssl.sh; \
	fi

# Build Docker images (uses Docker layer cache by default for speed)
# Usage: make docker-build                    # Build all (cached)
#        make docker-build SERVICE=authz-api  # Build specific service
#        make docker-build NO_CACHE=1         # Force rebuild without cache
docker-build: ssl-check
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
# Usage: make docker-logs                    # All services
#        make docker-logs SERVICE=authz-api  # Specific service
docker-logs:
ifdef SERVICE
	docker compose -f $(COMPOSE_FILE) logs -f $(SERVICE)
else
	docker compose -f $(COMPOSE_FILE) logs -f
endif

# Show Docker service status
docker-ps:
	docker compose -f $(COMPOSE_FILE) ps

# Run tests against local Docker environment
# Usage: make test-docker SERVICE=authz                    # Run all authz tests
#        make test-docker SERVICE=authz ARGS='-k health'   # Run tests matching 'health'
#        make test-docker SERVICE=authz FAST=0             # Run all tests (no FAST filter)
#        make test-docker SERVICE=all                      # Run all service tests
# Note: FAST=1 by default skips @slow/@gpu tests UNLESS you specify -m in ARGS
test-docker:
ifndef SERVICE
	@echo ""
	@echo "Error: SERVICE is required"
	@echo ""
	@echo "Usage: make test-docker SERVICE=<service> [ARGS='<pytest args>'] [FAST=0]"
	@echo ""
	@echo "Available services: authz, ingest, search, agent, all"
	@echo ""
	@echo "Examples:"
	@echo "  make test-docker SERVICE=agent                    # Run all agent tests"
	@echo "  make test-docker SERVICE=agent ARGS='-k test_weather'  # Filter by name"
	@echo "  make test-docker SERVICE=agent ARGS='-m integration'   # Filter by marker"
	@echo "  make test-docker SERVICE=agent ARGS='-v --tb=short'    # Verbose + short traceback"
	@echo "  make test-docker SERVICE=agent FAST=0             # Include slow/gpu tests"
	@echo ""
	@echo "ARGS Quoting:"
	@echo "  Use single quotes around the ARGS value: ARGS='-k test_name'"
	@echo "  Avoid using 'or' in -k patterns (shell quoting issues)"
	@echo "  Instead, run separate commands or use test file paths"
	@echo ""
	@echo "Note: FAST=1 (default) skips @slow/@gpu tests unless -m is in ARGS"
	@exit 1
endif
	@FAST=$${FAST:-1} INV=docker bash scripts/test/run-local-tests.sh $(SERVICE) docker $(ARGS)

# Backward compatibility alias for docker-test
docker-test: test-docker

# Clean up Docker environment (removes containers and volumes)
docker-clean:
	@echo "WARNING: This will remove all containers and volumes (all data will be lost)!"
	@read -p "Are you sure? (y/N) " confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		docker compose -f $(COMPOSE_FILE) down -v --remove-orphans; \
		echo "Cleanup complete."; \
	else \
		echo "Cancelled."; \
	fi
