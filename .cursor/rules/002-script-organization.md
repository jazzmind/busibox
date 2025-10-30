# Script Organization Rules

**Purpose**: Ensure consistent script organization across the Busibox project

## Script Directory Structure

Scripts are organized by **execution context** - where and how they are intended to run:

```
scripts/                    # Scripts run from admin workstation
├── deploy-*.sh            # Orchestration scripts (call Ansible)
├── setup-*.sh             # Setup/configuration scripts
├── test-*.sh              # Testing and validation scripts
└── upload-*.sh            # Utility scripts for uploading assets

provision/pct/              # Scripts run ON Proxmox host (as root)
├── create_*.sh            # LXC container creation
├── destroy_*.sh           # LXC container destruction
├── configure-*.sh         # Proxmox host configuration
├── setup-*.sh             # Proxmox host setup
├── test-*.sh              # Host-level testing
├── check-*.sh             # Validation and checking
├── list-*.sh              # Information gathering
├── vars.env               # Configuration file (production)
└── test-vars.env          # Configuration file (test)

provision/ansible/roles/*/files/  # Scripts deployed INTO LXC containers
└── *.sh                   # Service-specific scripts

provision/ansible/roles/*/templates/  # Script templates deployed INTO containers
└── *.sh.j2                # Templated scripts with Ansible variables
```

## Execution Context Rules

### `scripts/` - Admin Workstation Scripts

**Execution Context**: Run from developer/admin machine
**SSH Required**: Yes (usually)
**Root Required**: No (uses Ansible for privilege escalation)
**Purpose**: Orchestration, deployment, high-level operations

**When to place script in `scripts/`**:
- ✅ Calls Ansible playbooks
- ✅ Orchestrates multi-step deployments
- ✅ Runs from admin workstation via SSH
- ✅ Coordinates across multiple containers
- ✅ Uploads files to remote hosts
- ✅ Validates infrastructure from outside

**Examples**:
- `deploy-ai-portal.sh` - Orchestrates Ansible deployment
- `setup-vault-links.sh` - Configures vault on workstation
- `test-infrastructure.sh` - Tests from admin perspective
- `upload-ssl-cert.sh` - Uploads certs to Ansible vault

### `provision/pct/` - Proxmox Host Scripts

**Execution Context**: Run ON Proxmox host (via SSH or directly)
**SSH Required**: Must be on Proxmox host
**Root Required**: Yes (LXC operations require root)
**Purpose**: Container lifecycle, host configuration, GPU passthrough

**When to place script in `provision/pct/`**:
- ✅ Creates/destroys LXC containers
- ✅ Configures Proxmox host settings
- ✅ Manages GPU passthrough
- ✅ Manages ZFS storage
- ✅ Lists Proxmox resources
- ✅ Requires `pct`, `pvesm`, or other Proxmox commands
- ✅ Direct host-level operations

**Examples**:
- `create_lxc_base.sh` - Creates LXC containers using `pct`
- `configure-gpu-passthrough.sh` - Configures GPU for containers
- `setup-llm-models.sh` - Sets up model storage on host
- `check-storage.sh` - Validates Proxmox storage
- `list-templates.sh` - Lists available LXC templates

### `provision/ansible/roles/*/files/` - Container Runtime Scripts

**Execution Context**: Run INSIDE LXC containers
**Deployed By**: Ansible (copied to container)
**Root Required**: Varies by script
**Purpose**: Service-specific operations, monitoring, maintenance

**When to place script in role files/**:
- ✅ Service-specific operation (e.g., backup, restart)
- ✅ Runs inside a specific container
- ✅ No Ansible variables needed
- ✅ Static script that doesn't change per environment
- ✅ Monitoring or health check scripts

**Examples**:
- `deploywatch.sh` - Monitors and deploys app updates
- Service health check scripts
- Backup scripts
- Service-specific utilities

### `provision/ansible/roles/*/templates/` - Templated Container Scripts

**Execution Context**: Run INSIDE LXC containers (after templating)
**Deployed By**: Ansible (templated then copied)
**Root Required**: Varies by script
**Purpose**: Dynamic scripts that need environment-specific values

**When to place script in role templates/**:
- ✅ Script needs Ansible variables (IPs, ports, credentials)
- ✅ Different per environment (test/prod)
- ✅ Service configuration embedded in script
- ✅ Uses Jinja2 templating

**Examples**:
- `check-cert-expiry.sh.j2` - Uses domain variables
- `deploywatch-app.sh.j2` - Uses app-specific git URLs
- Service startup scripts with environment variables

## Script Naming Conventions

### Prefix-Based Naming

Use consistent prefixes to indicate script purpose:

**Deployment Scripts** (`deploy-*`):
- `deploy-{service}.sh` - Deploy specific service
- `deploy-{environment}.sh` - Deploy entire environment
- Example: `deploy-ai-portal.sh`, `deploy-production.sh`

**Setup Scripts** (`setup-*`):
- `setup-{component}.sh` - One-time setup
- Example: `setup-proxmox-host.sh`, `setup-vault-links.sh`, `setup-zfs-storage.sh`

**Test Scripts** (`test-*`):
- `test-{what}.sh` - Testing and validation
- Example: `test-infrastructure.sh`, `test-llm-containers.sh`, `test-vllm-on-host.sh`

**Creation Scripts** (`create_*`):
- `create_{what}.sh` - Create resources
- Example: `create_lxc_base.sh`

**Destruction Scripts** (`destroy_*`):
- `destroy_{what}.sh` - Destroy resources (use with caution!)
- Example: `destroy_test.sh`

**Configuration Scripts** (`configure-*`):
- `configure-{what}.sh` - Configure existing resources
- Example: `configure-gpu-passthrough.sh`

**Utility Scripts**:
- `check-{what}.sh` - Validation without changes
- `list-{what}.sh` - Information gathering
- `upload-{what}.sh` - Upload files/assets
- `add-{what}.sh` - Add configuration/mounts

### General Naming Rules
- Use `kebab-case` for all scripts
- Use `.sh` extension for bash scripts
- Use descriptive, action-oriented names
- Prefix clearly indicates purpose

## Script Header Template

Every script MUST include a comprehensive header:

```bash
#!/usr/bin/env bash
#
# Script Name
#
# Purpose: [One-line description of what this script does]
#
# Execution Context: [Proxmox Host|Admin Workstation|LXC Container]
# Required Privileges: [root|user|sudo]
# Dependencies: [ansible|pct|docker|etc]
#
# Usage:
#   bash script-name.sh [options]
#
# Options:
#   --flag      Description
#
# Examples:
#   bash script-name.sh --flag value
#
# Environment Variables:
#   VAR_NAME    Description (required/optional)
#

set -euo pipefail  # REQUIRED: Fail on errors, undefined vars, pipe failures

# Script directory detection (if needed for relative paths)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
```

## Script Content Standards

### Error Handling
```bash
# REQUIRED at start of script
set -euo pipefail

# Optional: Enable debug mode
# set -x

# Recommended: Color output functions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
```

### Validation
```bash
# Validate execution context
if [[ $EUID -ne 0 ]] && [[ "$REQUIRES_ROOT" == "yes" ]]; then
   error "This script must be run as root"
   exit 1
fi

# Check for required commands
for cmd in ansible pct ssh; do
    if ! command -v "$cmd" &> /dev/null; then
        error "Required command not found: $cmd"
        exit 1
    fi
done
```

### Configuration Loading
```bash
# For pct scripts - load vars.env
SCRIPT_DIR="$(dirname "$0")"
MODE="${1:-production}"

if [[ "$MODE" == "test" ]]; then
    source "${SCRIPT_DIR}/test-vars.env"
else
    source "${SCRIPT_DIR}/vars.env"
fi
```

## Decision Tree: Where Does This Script Go?

```
START: I need to create a script

Q1: Where will this script execute?
├─ On Proxmox host (requires pct/pvesm/pvesh)
│  └─→ provision/pct/
│
├─ Inside an LXC container
│  Q2: Does it need Ansible variables?
│  ├─ Yes (IPs, credentials, environment-specific)
│  │  └─→ provision/ansible/roles/{role}/templates/{script}.sh.j2
│  └─ No (static utility)
│     └─→ provision/ansible/roles/{role}/files/{script}.sh
│
└─ From admin workstation
   Q3: What does it do?
   ├─ Orchestrates Ansible/deployment
   │  └─→ scripts/deploy-*.sh
   ├─ One-time setup/configuration
   │  └─→ scripts/setup-*.sh
   ├─ Testing/validation
   │  └─→ scripts/test-*.sh
   └─ Utility (upload, backup, etc)
      └─→ scripts/{action}-*.sh
```

## Examples by Context

### Admin Workstation Script Pattern
```bash
#!/usr/bin/env bash
# scripts/deploy-myservice.sh
#
# Purpose: Deploy MyService to test environment
# Execution Context: Admin Workstation
# Required Privileges: user (Ansible handles escalation)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="${SCRIPT_DIR}/../provision/ansible"

info "Deploying MyService to test environment..."

cd "$ANSIBLE_DIR"
ansible-playbook -i inventory/test/hosts.yml site.yml \
    --tags myservice \
    --ask-vault-pass

success "MyService deployed successfully"
```

### Proxmox Host Script Pattern
```bash
#!/usr/bin/env bash
# provision/pct/create_mycontainer.sh
#
# Purpose: Create LXC container for MyService
# Execution Context: Proxmox Host (must run on PVE)
# Required Privileges: root

set -euo pipefail

# Check running on Proxmox
if ! command -v pct &> /dev/null; then
    echo "ERROR: Must run on Proxmox host"
    exit 1
fi

# Load configuration
SCRIPT_DIR="$(dirname "$0")"
source "${SCRIPT_DIR}/vars.env"

# Create container
pct create "$CT_MYSERVICE" "$TEMPLATE" \
    -hostname myservice-lxc \
    -net0 name=eth0,bridge=vmbr0,ip="${IP_MYSERVICE}/21",gw="$GATEWAY" \
    -storage "$STORAGE" \
    -rootfs "$STORAGE:${DISK_GB}" \
    -memory "$RAM_MB" \
    -cores "$CORES" \
    -unprivileged 1

pct start "$CT_MYSERVICE"
```

### Container Runtime Script Pattern
```bash
#!/usr/bin/env bash
# provision/ansible/roles/myservice/files/health-check.sh
#
# Purpose: Check health of MyService
# Execution Context: Inside myservice-lxc container
# Required Privileges: user

set -euo pipefail

SERVICE_URL="http://localhost:3000/health"

if curl -sf "$SERVICE_URL" > /dev/null 2>&1; then
    echo "✓ MyService is healthy"
    exit 0
else
    echo "✗ MyService is unhealthy"
    exit 1
fi
```

## Migration from Current Structure

Current misplaced scripts:

```bash
# Already correctly placed:
✅ scripts/ - All scripts here are workstation orchestration
✅ provision/pct/ - All scripts here run on Proxmox host

# No migrations needed - structure is already correct!
```

## AI Agent Instructions

When asked to create a script:

1. **Determine execution context** - Where will this run?
2. **Check decision tree** - Follow the logic above
3. **Use correct naming** - Follow prefix conventions
4. **Include proper header** - Use header template
5. **Add error handling** - Use `set -euo pipefail` and validation
6. **Document location** - Tell user WHY script is placed there
7. **Consider templating** - If script needs vars, use `.j2` template

When asked about script location:

1. **Identify execution context** first
2. **Explain the reasoning** - Why this location?
3. **Suggest refactoring** if script is misplaced
4. **Update related docs** if script location changes

## Common Pitfalls

### ❌ DON'T: Place Proxmox scripts in `scripts/`
```bash
# WRONG: scripts/create-containers.sh
# This needs pct command which only exists on Proxmox host
```
**FIX**: Place in `provision/pct/create-containers.sh`

### ❌ DON'T: Place orchestration scripts in `provision/pct/`
```bash
# WRONG: provision/pct/deploy-all.sh
# This calls Ansible from workstation, not Proxmox operations
```
**FIX**: Place in `scripts/deploy-all.sh`

### ❌ DON'T: Hardcode values that vary per environment
```bash
# WRONG: provision/ansible/roles/myservice/files/start.sh
POSTGRES_HOST="10.96.200.26"  # This changes per environment!
```
**FIX**: Use template in `templates/start.sh.j2`:
```bash
POSTGRES_HOST="{{ postgres_ip }}"
```

### ❌ DON'T: Forget script headers
```bash
# WRONG: No context about where/how to run
#!/usr/bin/env bash
pct create 201 ...
```
**FIX**: Add comprehensive header explaining context

## Validation Checklist

Before committing a new script:

- [ ] Placed in correct directory based on execution context
- [ ] Named following prefix conventions
- [ ] Includes comprehensive header
- [ ] Uses `set -euo pipefail`
- [ ] Validates prerequisites (root, commands, etc)
- [ ] Uses color output functions
- [ ] Includes usage examples in header
- [ ] Documented in related docs (if needed)
- [ ] Executable permissions set (`chmod +x`)

## Related Documentation

- [Documentation Organization](001-documentation-organization.md) - For documenting scripts
- `provision/ansible/SETUP.md` - Ansible setup and usage
- `docs/architecture/architecture.md` - System architecture



