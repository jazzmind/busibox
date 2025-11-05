#!/usr/bin/env bash
#
# Busibox Interactive Setup Script
#
# Description:
#   Universal interactive setup script that guides through the complete
#   Busibox deployment process: host configuration, container creation,
#   and Ansible configuration.
#
# Execution Context: Proxmox VE Host
# Dependencies: bash, provision/pct/*, provision/ansible/*
#
# Usage:
#   bash provision/setup.sh
#
# Steps:
#   1. Check and configure Proxmox host
#   2. Create LXC containers (with options)
#   3. Configure containers with Ansible (with options)
#
# Notes:
#   - Interactive prompts guide through each step
#   - Can skip steps that are already complete
#   - Validates prerequisites before proceeding

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PCT_DIR="${SCRIPT_DIR}/pct"
ANSIBLE_DIR="${SCRIPT_DIR}/ansible"

# Helper functions
print_header() {
  echo ""
  echo -e "${BLUE}==========================================${NC}"
  echo -e "${BLUE}$1${NC}"
  echo -e "${BLUE}==========================================${NC}"
  echo ""
}

print_success() {
  echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
  echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
  echo -e "${RED}✗ $1${NC}"
}

print_info() {
  echo -e "${BLUE}ℹ $1${NC}"
}

# Check if running on Proxmox
check_proxmox() {
  if ! command -v pct &> /dev/null; then
    print_error "This script must run on a Proxmox host"
    echo ""
    echo "If you're on your admin workstation, you should:"
    echo "  1. Copy this script to your Proxmox host"
    echo "  2. SSH to the Proxmox host"
    echo "  3. Run this script on the host"
    exit 1
  fi
  print_success "Running on Proxmox host"
}

# Check if running as root
check_root() {
  if [[ $EUID -ne 0 ]]; then
    print_error "This script must be run as root"
    echo ""
    echo "Please run: sudo bash $0"
    exit 1
  fi
  print_success "Running as root"
}

# Check if host is configured
check_host_configured() {
  local issues=()
  
  # Check for SSH key
  if [[ ! -f /root/.ssh/id_rsa.pub ]]; then
    issues+=("SSH key not generated")
  fi
  
  # Check for LXC template
  if ! ls /var/lib/vz/template/cache/debian-12*.tar.* &>/dev/null; then
    issues+=("No Debian 12 template found")
  fi
  
  # Check for data directories
  if [[ ! -d /var/lib/data ]]; then
    issues+=("Data directories not created")
  fi
  
  # Check for Ansible
  if ! command -v ansible &>/dev/null; then
    issues+=("Ansible not installed")
  fi
  
  if [[ ${#issues[@]} -eq 0 ]]; then
    print_success "Proxmox host is configured"
    return 0
  else
    print_warning "Proxmox host needs configuration"
    echo ""
    echo "Missing requirements:"
    for issue in "${issues[@]}"; do
      echo "  - $issue"
    done
    return 1
  fi
}

# Check if containers exist
check_containers_exist() {
  local mode=$1
  local exists=true
  
  if [[ "$mode" == "test" ]]; then
    # Check test containers (300-310)
    for ctid in 300 301 302 303 304 305 306 307 308; do
      if ! pct status "$ctid" &>/dev/null; then
        exists=false
        break
      fi
    done
  else
    # Check production containers (200-208)
    for ctid in 200 201 202 203 204 205 206 207 208; do
      if ! pct status "$ctid" &>/dev/null; then
        exists=false
        break
      fi
    done
  fi
  
  if $exists; then
    print_success "Containers already exist for $mode environment"
    return 0
  else
    print_info "Containers not found for $mode environment"
    return 1
  fi
}

# Step 1: Host Configuration
step_host_configuration() {
  print_header "Step 1: Proxmox Host Configuration"
  
  if check_host_configured; then
    echo ""
    read -p "Host appears configured. Re-run setup anyway? (y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      print_info "Skipping host configuration"
      return 0
    fi
  fi
  
  echo ""
  echo "This will:"
  echo "  - Install Ansible and dependencies"
  echo "  - Download LXC templates"
  echo "  - Generate SSH keys"
  echo "  - Check/install NVIDIA drivers (if GPU present)"
  echo "  - Setup ZFS storage for persistent data"
  echo "  - Optionally download LLM models"
  echo ""
  read -p "Run host configuration? (Y/n): " -n 1 -r
  echo ""
  
  if [[ $REPLY =~ ^[Nn]$ ]]; then
    print_warning "Skipping host configuration"
    echo ""
    echo "Note: Container creation requires configured host"
    return 1
  fi
  
  echo ""
  print_info "Running host setup script..."
  echo ""
  
  if bash "${PCT_DIR}/host/setup-proxmox-host.sh"; then
    echo ""
    print_success "Host configuration complete!"
    return 0
  else
    echo ""
    print_error "Host configuration failed"
    return 1
  fi
}

# Step 2: Container Creation
step_container_creation() {
  print_header "Step 2: LXC Container Creation"
  
  # Select environment
  echo "Select deployment environment:"
  echo "  1) Production (containers 200-208)"
  echo "  2) Test (containers 300-310)"
  echo "  3) Skip container creation"
  echo ""
  read -p "Choose [1-3]: " -n 1 -r env_choice
  echo ""
  echo ""
  
  case "$env_choice" in
    1)
      MODE="production"
      ;;
    2)
      MODE="test"
      ;;
    3)
      print_info "Skipping container creation"
      return 0
      ;;
    *)
      print_error "Invalid choice"
      return 1
      ;;
  esac
  
  # Check if containers already exist
  if check_containers_exist "$MODE"; then
    echo ""
    read -p "Containers exist. Recreate them? This will DESTROY existing containers! (y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      print_info "Skipping container creation"
      return 0
    fi
    
    # Destroy existing containers
    echo ""
    print_warning "Destroying existing $MODE containers..."
    if [[ "$MODE" == "test" ]]; then
      bash "${PCT_DIR}/diagnostic/destroy_test.sh" || true
    else
      echo "Please manually destroy production containers if needed"
      echo "Use: pct stop <CTID> && pct destroy <CTID> --purge"
      return 1
    fi
  fi
  
  # Optional Ollama
  echo ""
  echo "Ollama LXC Container (optional):"
  echo "  - Uses single GPU (GPU 0)"
  echo "  - Alternative to vLLM for some use cases"
  echo "  - Not required (vLLM is primary inference engine)"
  echo ""
  read -p "Include Ollama container? (y/N): " -n 1 -r
  echo ""
  
  OLLAMA_FLAG=""
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    OLLAMA_FLAG="--with-ollama"
    print_info "Ollama will be included"
  else
    print_info "Ollama will NOT be created (vLLM only)"
  fi
  
  # Summary
  echo ""
  echo "Container creation summary:"
  echo "  Environment: $MODE"
  echo "  Ollama: $(if [[ -n "$OLLAMA_FLAG" ]]; then echo "Yes"; else echo "No"; fi)"
  echo ""
  echo "Containers to create:"
  echo "  - Core services: proxy, apps, agent"
  echo "  - Data services: postgres, milvus, minio"
  echo "  - Worker services: ingest, litellm"
  echo "  - LLM services: vLLM (all GPUs)$(if [[ -n "$OLLAMA_FLAG" ]]; then echo ", Ollama (GPU 0)"; fi)"
  echo ""
  read -p "Proceed with container creation? (Y/n): " -n 1 -r
  echo ""
  
  if [[ $REPLY =~ ^[Nn]$ ]]; then
    print_info "Skipping container creation"
    return 0
  fi
  
  echo ""
  print_info "Creating containers..."
  echo ""
  
  if bash "${PCT_DIR}/containers/create_lxc_base.sh" "$MODE" $OLLAMA_FLAG; then
    echo ""
    print_success "Container creation complete!"
    
    # Save mode for next step
    echo "$MODE" > /tmp/busibox_setup_mode
    return 0
  else
    echo ""
    print_error "Container creation failed"
    return 1
  fi
}

# Step 3: Ansible Configuration
step_ansible_configuration() {
  print_header "Step 3: Ansible Configuration"
  
  # Detect mode from previous step or ask
  if [[ -f /tmp/busibox_setup_mode ]]; then
    MODE=$(cat /tmp/busibox_setup_mode)
    print_info "Using $MODE environment from previous step"
  else
    echo "Select environment to configure:"
    echo "  1) Production"
    echo "  2) Test"
    echo "  3) Local (docker-compose)"
    echo "  4) Skip Ansible configuration"
    echo ""
    read -p "Choose [1-4]: " -n 1 -r env_choice
    echo ""
    echo ""
    
    case "$env_choice" in
      1) MODE="production" ;;
      2) MODE="test" ;;
      3) MODE="local" ;;
      4)
        print_info "Skipping Ansible configuration"
        return 0
        ;;
      *)
        print_error "Invalid choice"
        return 1
        ;;
    esac
  fi
  
  # Check if Ansible directory exists
  if [[ ! -d "$ANSIBLE_DIR" ]]; then
    print_error "Ansible directory not found: $ANSIBLE_DIR"
    return 1
  fi
  
  # Check if inventory exists
  if [[ ! -f "${ANSIBLE_DIR}/inventory/${MODE}/hosts.yml" ]]; then
    print_error "Inventory not found for $MODE environment"
    echo "Expected: ${ANSIBLE_DIR}/inventory/${MODE}/hosts.yml"
    return 1
  fi
  
  # Tag selection
  echo ""
  echo "Ansible configuration options:"
  echo "  1) Full deployment (all services)"
  echo "  2) Specific services (use tags)"
  echo "  3) Custom command"
  echo "  4) Skip Ansible"
  echo ""
  read -p "Choose [1-4]: " -n 1 -r ansible_choice
  echo ""
  echo ""
  
  case "$ansible_choice" in
    1)
      # Full deployment
      print_info "Running full Ansible deployment for $MODE..."
      echo ""
      cd "$ANSIBLE_DIR"
      make "$MODE"
      ;;
    2)
      # Tag-based deployment
      echo "Available tags:"
      echo "  - nginx (reverse proxy)"
      echo "  - postgres (database)"
      echo "  - milvus (vector database)"
      echo "  - minio (object storage)"
      echo "  - redis (queue)"
      echo "  - ingest (worker service)"
      echo "  - agent (API service)"
      echo "  - apps (Next.js applications)"
      echo "  - litellm (LLM gateway)"
      echo "  - ollama (LLM runtime)"
      echo "  - vllm (LLM runtime)"
      echo ""
      read -p "Enter tags (comma-separated, e.g., nginx,postgres): " tags
      echo ""
      
      if [[ -z "$tags" ]]; then
        print_error "No tags provided"
        return 1
      fi
      
      print_info "Running Ansible with tags: $tags"
      echo ""
      cd "$ANSIBLE_DIR"
      ansible-playbook -i "inventory/${MODE}/hosts.yml" site.yml --tags "$tags"
      ;;
    3)
      # Custom command
      echo "Enter custom Ansible command (without 'ansible-playbook'):"
      read -r custom_cmd
      echo ""
      
      print_info "Running custom Ansible command..."
      echo ""
      cd "$ANSIBLE_DIR"
      eval "ansible-playbook $custom_cmd"
      ;;
    4)
      print_info "Skipping Ansible configuration"
      return 0
      ;;
    *)
      print_error "Invalid choice"
      return 1
      ;;
  esac
  
  echo ""
  print_success "Ansible configuration complete!"
  
  # Cleanup temp file
  rm -f /tmp/busibox_setup_mode
  return 0
}

# Main setup flow
main() {
  print_header "Busibox Interactive Setup"
  
  echo "This script will guide you through setting up Busibox infrastructure:"
  echo "  1. Configure Proxmox host"
  echo "  2. Create LXC containers"
  echo "  3. Configure with Ansible"
  echo ""
  
  # Prerequisite checks
  check_proxmox
  check_root
  
  echo ""
  read -p "Ready to begin? (Y/n): " -n 1 -r
  echo ""
  
  if [[ $REPLY =~ ^[Nn]$ ]]; then
    print_info "Setup cancelled"
    exit 0
  fi
  
  # Step 1: Host Configuration
  if ! step_host_configuration; then
    print_error "Host configuration failed or was skipped"
    echo ""
    echo "You can:"
    echo "  - Fix any issues and run this script again"
    echo "  - Run host setup manually: bash provision/pct/host/setup-proxmox-host.sh"
    echo "  - Continue to next steps if host is already configured"
    echo ""
    read -p "Continue to container creation anyway? (y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      exit 1
    fi
  fi
  
  # Step 2: Container Creation
  if ! step_container_creation; then
    print_warning "Container creation failed or was skipped"
    echo ""
    echo "You can:"
    echo "  - Fix any issues and run this script again"
    echo "  - Create containers manually: bash provision/pct/containers/create_lxc_base.sh"
    echo "  - Continue to Ansible if containers already exist"
    echo ""
    read -p "Continue to Ansible configuration anyway? (y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      exit 1
    fi
  fi
  
  # Step 3: Ansible Configuration
  if ! step_ansible_configuration; then
    print_warning "Ansible configuration failed or was skipped"
    echo ""
    echo "You can:"
    echo "  - Fix any issues and run this script again"
    echo "  - Run Ansible manually: cd provision/ansible && make <environment>"
  fi
  
  # Final summary
  print_header "Setup Complete!"
  
  echo "Your Busibox infrastructure is ready!"
  echo ""
  echo "Next steps:"
  echo "  - Verify services: bash scripts/test-infrastructure.sh"
  echo "  - Check container status: pct list"
  echo "  - Check GPU usage: bash provision/pct/diagnostic/check-gpu-usage.sh"
  echo "  - View logs: ssh <container-ip> && journalctl -u <service>"
  echo ""
  echo "Documentation:"
  echo "  - Architecture: docs/architecture/architecture.md"
  echo "  - Deployment: docs/deployment/"
  echo "  - Troubleshooting: docs/troubleshooting/"
  echo ""
  print_success "Setup complete! 🚀"
}

# Run main function
main

