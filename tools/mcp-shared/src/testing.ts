/**
 * Testing utilities for Busibox
 */

export const TESTING_GUIDES: Record<string, string> = {
  overview: `# Busibox Testing Overview

## Test Types

1. **Docker Tests** (\`make test-docker\`)
   - Run locally against Docker services
   - Best for development and CI
   - Requires: Docker running, test DBs initialized

2. **Remote Tests** (\`make test-local\`)
   - Run locally but connect to remote staging/production
   - Tests your local code against real services
   - Requires: Network access to staging/production

3. **Container Tests** (\`make test\`)
   - Run directly on containers via SSH
   - Tests the deployed code
   - Requires: SSH access to Proxmox

## Quick Start

\`\`\`bash
# Start Docker services
make docker-up

# Initialize test databases (first time)
make test-db-init

# Run agent tests
make test-docker SERVICE=agent

# Run all tests
make test-docker SERVICE=all
\`\`\`
`,
  docker: `# Docker Testing Guide

## Prerequisites
1. Docker Desktop running
2. Services started: \`make docker-up\`
3. Test DBs initialized: \`make test-db-init\`

## Commands

\`\`\`bash
# Run specific service tests
make test-docker SERVICE=authz
make test-docker SERVICE=ingest
make test-docker SERVICE=search
make test-docker SERVICE=agent

# Run all tests
make test-docker SERVICE=all

# Include slow/GPU tests
make test-docker SERVICE=agent FAST=0

# Run specific test
make test-docker SERVICE=agent ARGS='-k test_weather'

# Verbose output
make test-docker SERVICE=agent ARGS='-v --tb=short'
\`\`\`

## Services in Docker
- authz-api: http://localhost:8080
- data-api: http://localhost:8002
- search-api: http://localhost:8003
- agent-api: http://localhost:8000
`,
  remote: `# Remote Testing Guide

Test your local code changes against staging/production services.

## Prerequisites
1. Network/VPN access to staging (10.96.201.x) or production (10.96.200.x)
2. Python environment with dependencies

## Commands

\`\`\`bash
# Test against staging
make test-local SERVICE=agent INV=staging

# Test against production
make test-local SERVICE=agent INV=production

# Include slow tests
make test-local SERVICE=agent INV=staging FAST=0

# With data worker (for pipeline tests)
make test-local SERVICE=data INV=staging WORKER=1

# Specific test
make test-local SERVICE=agent INV=staging ARGS='-k test_weather'
\`\`\`
`,
  container: `# Container Testing Guide

Run tests directly on deployed containers via SSH.

## Prerequisites
1. SSH access to Proxmox host
2. Services deployed to staging/production

## Commands (run from Proxmox or via SSH)

\`\`\`bash
# Interactive menu
make test

# Direct command
make test SERVICE=agent INV=staging

# With pytest args
PYTEST_ARGS='-k test_weather' make test SERVICE=agent INV=staging
\`\`\`

## What happens
1. SSH to the service container
2. Run pytest in the container's code directory
3. Return results
`,
  troubleshooting: `# Testing Troubleshooting

## Common Issues

### "Test databases not initialized"
\`\`\`bash
make test-db-init
\`\`\`

### "Connection refused" in Docker tests
\`\`\`bash
# Check services are running
make docker-ps

# Restart services
make docker-restart
\`\`\`

### "Network unreachable" for remote tests
- Check VPN connection
- Verify network access: \`ping 10.96.201.200\` (staging)

### Tests timeout
\`\`\`bash
# Use FAST mode to skip slow tests
make test-docker SERVICE=agent FAST=1
\`\`\`

### Permission denied
- Check SSH keys are set up for Proxmox access
- Verify container SSH access

## Test Database Info

Tests run against isolated databases:
- test_authz (not authz)
- test_data (not data)
- test_agent (not agent)

User: busibox_test_user
`,
};
