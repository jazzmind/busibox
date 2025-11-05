.PHONY: help local-up local-down local-deploy local-logs local-shell local-reset test-deploy prod-deploy

# Default target
help:
	@echo "Cashman Infrastructure Management"
	@echo ""
	@echo "Local Development (Docker):"
	@echo "  make local-up          - Start local Docker containers"
	@echo "  make local-down        - Stop local Docker containers"
	@echo "  make local-deploy      - Deploy to local containers with Ansible"
	@echo "  make local-logs        - Show logs from all containers"
	@echo "  make local-shell       - Open shell in ai-portal container"
	@echo "  make local-reset       - Reset local environment (WARNING: deletes data)"
	@echo ""
	@echo "Proxmox Deployment:"
	@echo "  make test-deploy       - Deploy to Proxmox test environment"
	@echo "  make prod-deploy       - Deploy to Proxmox production (requires confirmation)"
	@echo ""
	@echo "Examples:"
	@echo "  make local-up && make local-deploy    # Full local setup"
	@echo "  make local-shell                       # Debug ai-portal container"

# ============================================================================
# Local Development
# ============================================================================

local-up:
	@echo "🚀 Starting local Docker containers..."
	docker compose -f docker-compose.local.yml up -d
	@echo "✅ Containers started. Run 'make local-deploy' to provision with Ansible."

local-down:
	@echo "🛑 Stopping local Docker containers..."
	docker compose -f docker-compose.local.yml down
	@echo "✅ Containers stopped."

local-deploy:
	@echo "📦 Deploying to local containers with Ansible..."
	cd provision/ansible && \
	ansible-playbook -i inventory/local/hosts.yml site.yml \
		--vault-password-file .vault_pass_local
	@echo "✅ Deployment complete."

local-deploy-apps:
	@echo "📦 Deploying only ai-portal to local..."
	cd provision/ansible && \
	ansible-playbook -i inventory/local/hosts.yml site.yml \
		--vault-password-file .vault_pass_local \
		--limit apps --tags nextjs

local-logs:
	@echo "📋 Showing logs from all containers..."
	docker compose -f docker-compose.local.yml logs -f

local-shell:
	@echo "🐚 Opening shell in ai-portal container..."
	docker exec -it local-apps bash

local-shell-user:
	@echo "🐚 Opening shell as appuser in ai-portal container..."
	docker exec -it -u appuser local-apps bash

local-reset:
	@echo "⚠️  WARNING: This will delete all local data!"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		docker compose -f docker-compose.local.yml down -v; \
		echo "✅ Local environment reset."; \
	else \
		echo "❌ Aborted."; \
	fi

# ============================================================================
# Proxmox Deployment
# ============================================================================

test-deploy:
	@echo "🧪 Deploying to Proxmox TEST environment..."
	cd provision/ansible && \
	ansible-playbook -i inventory/test/hosts.yml site.yml \
		--vault-password-file ~/.vault_pass
	@echo "✅ Test deployment complete."

test-deploy-apps:
	@echo "🧪 Deploying only ai-portal to Proxmox TEST..."
	cd provision/ansible && \
	ansible-playbook -i inventory/test/hosts.yml site.yml \
		--vault-password-file ~/.vault_pass \
		--limit apps --tags nextjs

prod-deploy:
	@echo "🚨 WARNING: Deploying to PRODUCTION!"
	@read -p "Are you sure? Type 'production' to confirm: " confirm; \
	if [ "$$confirm" = "production" ]; then \
		cd provision/ansible && \
		ansible-playbook -i inventory/production/hosts.yml site.yml \
			--vault-password-file ~/.vault_pass; \
		echo "✅ Production deployment complete."; \
	else \
		echo "❌ Aborted."; \
	fi

# ============================================================================
# Utilities
# ============================================================================

check-docker:
	@docker info > /dev/null 2>&1 || (echo "❌ Docker is not running. Start Docker Desktop." && exit 1)
	@echo "✅ Docker is running."

check-ansible:
	@which ansible-playbook > /dev/null || (echo "❌ Ansible not found. Run: brew install ansible" && exit 1)
	@echo "✅ Ansible is installed."

check-deps: check-docker check-ansible
	@echo "✅ All dependencies are installed."










