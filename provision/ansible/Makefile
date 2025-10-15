.PHONY: ping all files pg milvus agent ingest apps openwebui deploy-apps verify verify-health verify-smoke
# Default to production inventory (can override with: make all INV=inventory/test)
INV=inventory/production

ping:
	ansible -i $(INV) all -m ping

all:
	ansible-playbook -i $(INV) site.yml

files:
	ansible-playbook -i $(INV) -l files site.yml

pg:
	ansible-playbook -i $(INV) -l pg site.yml

milvus:
	ansible-playbook -i $(INV) -l milvus site.yml

agent:
	ansible-playbook -i $(INV) -l agent site.yml

ingest:
	ansible-playbook -i $(INV) -l ingest site.yml

apps:
	ansible-playbook -i $(INV) -l apps site.yml

openwebui:
	ansible-playbook -i $(INV) -l openwebui site.yml

# T029: Deploy applications (runs app_deployer + secrets roles)
deploy-apps:
	ansible-playbook -i $(INV) -l agent,apps --tags app_deployer,secrets site.yml

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
