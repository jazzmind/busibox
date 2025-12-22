.PHONY: menu help setup configure deploy test test-local test-security mcp

# Default target - interactive menu
.DEFAULT_GOAL := menu

# Variables for direct test commands
# Usage: make test SERVICE=authz INV=test
#        make test SERVICE=authz INV=test MODE=local
#        make test-local SERVICE=authz INV=test ARGS="-m pvt"
#        make test-local SERVICE=search INV=test FAST=1
SERVICE ?=
INV ?= test
MODE ?= container
ARGS ?=
FAST ?=

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
	@echo "  test          - Run tests (interactive or direct)"
	@echo "  test-local    - Run tests locally against containers"
	@echo "  test-security - Run API security tests (fuzzing, OWASP)"
	@echo "  mcp           - Build MCP server for Cursor AI"
	@echo "  help          - Show this help message"
	@echo ""
	@echo "Testing (on containers):"
	@echo "  make test                                 # Interactive menu"
	@echo "  make test SERVICE=authz INV=test          # Run authz tests on test containers"
	@echo "  make test SERVICE=ingest INV=test         # Run ingest tests on test containers"
	@echo "  make test SERVICE=search INV=test         # Run search tests on test containers"
	@echo "  make test SERVICE=agent INV=test          # Run agent tests on test containers"
	@echo ""
	@echo "Local Testing (run tests on your machine against container backends):"
	@echo "  make test SERVICE=authz INV=test MODE=local  # Run authz tests locally"
	@echo "  make test-local SERVICE=authz INV=test       # Same as above (shorthand)"
	@echo "  make test-local SERVICE=ingest INV=test      # Run ingest tests locally"
	@echo "  make test-local SERVICE=search INV=test      # Run search tests locally"
	@echo "  make test-local SERVICE=agent INV=test       # Run agent tests locally"
	@echo "  make test-local SERVICE=all INV=test         # Run all tests locally"
	@echo ""
	@echo "Test Filtering:"
	@echo "  make test-local ... ARGS=\"-m pvt\"            # Run only PVT tests"
	@echo "  make test-local ... ARGS=\"-k test_health\"    # Run tests matching pattern"
	@echo "  make test-local ... FAST=1                   # Skip slow/GPU tests"
	@echo "  make test-local ... ARGS=\"--tb=short\"        # Short tracebacks"
	@echo ""
	@echo "Quick Start:"
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
	@bash scripts/make/test.sh $(SERVICE) $(INV) $(MODE)
else
	@bash scripts/make/test.sh
endif

# Run tests locally against remote container backends
# Usage: make test-local SERVICE=authz INV=test
#        make test-local SERVICE=authz INV=test ARGS="-m pvt"
#        make test-local SERVICE=search INV=test FAST=1
test-local:
ifndef SERVICE
	@echo "Error: SERVICE is required"
	@echo "Usage: make test-local SERVICE=authz INV=test"
	@echo "       make test-local SERVICE=authz INV=test ARGS=\"-m pvt\""
	@echo "       make test-local SERVICE=search INV=test FAST=1"
	@echo ""
	@echo "Available services: authz, ingest, search, agent, all"
	@echo "ARGS: Pass additional pytest arguments (e.g., -m pvt, -k pattern, --tb=short)"
	@echo "FAST=1: Skip tests marked as @pytest.mark.slow or @pytest.mark.gpu"
	@exit 1
endif
	@FAST=$(FAST) bash scripts/test/run-local-tests.sh $(SERVICE) $(INV) $(ARGS)

test-security:
	@bash tests/security/run_tests.sh

mcp:
	@bash scripts/make/mcp.sh
