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
  local missing=()
  
  if [[ "$mode" == "test" ]]; then
    # Check test containers (300-310)
    for ctid in 300 301 302 303 304 305 306 307 308 309 310; do
      if ! pct status "$ctid" &>/dev/null; then
        exists=false
        missing+=("$ctid")
      fi
    done
  else
    # Check production containers (200-210)
    for ctid in 200 201 202 203 204 205 206 207 208 209 210; do
      if ! pct status "$ctid" &>/dev/null; then
        exists=false
        missing+=("$ctid")
      fi
    done
  fi
  
  if $exists; then
    print_success "All containers exist for $mode environment"
    return 0
  else
    print_info "Some containers missing for $mode environment: ${missing[*]}"
    return 1
  fi
}

# List existing containers for an environment
list_existing_containers() {
  local mode=$1
  local existing=()
  
  if [[ "$mode" == "test" ]]; then
    # Check test containers (300-310)
    for ctid in 300 301 302 303 304 305 306 307 308 309 310; do
      if pct status "$ctid" &>/dev/null; then
        local name=$(pct config "$ctid" | grep "hostname:" | awk '{print $2}')
        existing+=("$ctid:$name")
      fi
    done
  else
    # Check production containers (200-210)
    for ctid in 200 201 202 203 204 205 206 207 208 209 210; do
      if pct status "$ctid" &>/dev/null; then
        local name=$(pct config "$ctid" | grep "hostname:" | awk '{print $2}')
        existing+=("$ctid:$name")
      fi
    done
  fi
  
  if [[ ${#existing[@]} -eq 0 ]]; then
    return 1
  fi
  
  echo ""
  echo "Existing containers:"
  for container in "${existing[@]}"; do
    local ctid="${container%%:*}"
    local name="${container##*:}"
    echo "  - $ctid: $name"
  done
  return 0
}

# Check if vault file is encrypted
check_vault_encrypted() {
  local vault_file="${ANSIBLE_DIR}/roles/secrets/vars/vault.yml"
  
  if [[ ! -f "$vault_file" ]]; then
    return 1
  fi
  
  # Check if file starts with $ANSIBLE_VAULT
  if head -1 "$vault_file" | grep -q '^\$ANSIBLE_VAULT'; then
    return 0
  fi
  
  return 1
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

# Individual container management (create/recreate specific containers)
step_individual_containers() {
  print_header "Individual Container Management"
  
  # Select environment first
  echo "Select environment:"
  echo "  1) Production (200-210)"
  echo "  2) Test (300-310)"
  echo ""
  read -p "Choose [1-2]: " -n 1 -r env_choice
  echo ""
  echo ""
  
  case "$env_choice" in
    1)
      MODE="production"
      ;;
    2)
      MODE="test"
      ;;
    *)
      print_error "Invalid choice"
      return 1
      ;;
  esac
  
  # Show current container status
  echo "Current container status for $MODE:"
  list_existing_containers "$MODE" || print_info "No containers exist yet"
  
  # Loop for creating individual containers
  while true; do
    echo ""
    echo "=========================================="
    echo "Individual Container Creation - $MODE"
    echo "=========================================="
    echo ""
    echo "Available container groups:"
    echo "  1) Core services (authz, proxy, apps, agent)"
    echo "  2) Data services (postgres, milvus, minio)"
    echo "  3) Worker services (ingest, litellm)"
    echo "  4) vLLM (all GPUs)"
    echo "  5) Ollama (optional, single GPU)"
    echo "  6) Destroy specific container(s)"
    echo "  7) Show container status"
    echo "  8) Done with individual containers"
    echo ""
    read -p "Choose [1-8]: " -n 1 -r container_choice
    echo ""
    echo ""
    
    case "$container_choice" in
      1)
        print_info "Creating core services (proxy, apps, agent)..."
        if bash "${PCT_DIR}/containers/create-core-services.sh" "$MODE"; then
          print_success "Core services created!"
        else
          print_error "Core services creation failed"
        fi
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      2)
        print_info "Creating data services (postgres, milvus, minio)..."
        if bash "${PCT_DIR}/containers/create-data-services.sh" "$MODE"; then
          print_success "Data services created!"
        else
          print_error "Data services creation failed"
        fi
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      3)
        print_info "Creating worker services (ingest, litellm)..."
        if bash "${PCT_DIR}/containers/create-worker-services.sh" "$MODE"; then
          print_success "Worker services created!"
        else
          print_error "Worker services creation failed"
        fi
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      4)
        print_info "Creating vLLM container (all GPUs)..."
        if bash "${PCT_DIR}/containers/create-vllm.sh" "$MODE"; then
          print_success "vLLM container created!"
        else
          print_error "vLLM container creation failed"
        fi
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      5)
        echo "GPU number for Ollama (default: 0):"
        read -p "Enter GPU number: " gpu_num
        gpu_num=${gpu_num:-0}
        echo ""
        print_info "Creating Ollama container (GPU ${gpu_num})..."
        if bash "${PCT_DIR}/containers/create-ollama.sh" "$MODE" "$gpu_num"; then
          print_success "Ollama container created!"
        else
          print_error "Ollama container creation failed"
        fi
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      6)
        # Destroy specific containers
        echo "Enter container IDs to destroy (space-separated):"
        echo "Example: 200 203 208"
        read -r ctids_to_destroy
        
        if [[ -z "$ctids_to_destroy" ]]; then
          print_error "No container IDs provided"
          read -p "Press Enter to continue..." -r
          continue
        fi
        
        echo ""
        print_warning "Will destroy: $ctids_to_destroy"
        read -p "Confirm? (y/N): " -n 1 -r
        echo ""
        
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
          print_info "Cancelled"
          read -p "Press Enter to continue..." -r
          continue
        fi
        
        echo ""
        for ctid in $ctids_to_destroy; do
          if pct status "$ctid" &>/dev/null; then
            print_info "Destroying container $ctid..."
            pct stop "$ctid" 2>/dev/null || true
            sleep 1
            pct destroy "$ctid" --purge 2>/dev/null || true
            print_success "Container $ctid destroyed"
          else
            print_warning "Container $ctid not found"
          fi
        done
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      7)
        # Show status
        echo "Container status for $MODE:"
        list_existing_containers "$MODE" || print_info "No containers exist"
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      8)
        print_info "Done with individual container management"
        # Save mode for next step
        echo "$MODE" > /tmp/busibox_setup_mode
        return 0
        ;;
      *)
        print_error "Invalid choice"
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
    esac
  done
}

# Step 2: Container Creation
step_container_creation() {
  print_header "Step 2: LXC Container Creation"
  
  # Select environment
  echo "Select deployment environment:"
  echo "  1) Production (containers 200-210)"
  echo "  2) Test (containers 300-310)"
  echo "  3) Individual container management (create/recreate specific)"
  echo "  4) Skip container creation"
  echo ""
  read -p "Choose [1-4]: " -n 1 -r env_choice
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
      # Individual container management
      step_individual_containers
      return $?
      ;;
    4)
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
    print_success "All containers already exist for $MODE environment"
    list_existing_containers "$MODE"
    echo ""
    echo "What would you like to do?"
    echo "  1) Skip - keep existing containers (will create any missing)"
    echo "  2) Destroy specific containers and recreate"
    echo "  3) Destroy ALL containers and recreate from scratch"
    echo "  4) Cancel container creation"
    echo ""
    read -p "Choose [1-4]: " -n 1 -r container_action
    echo ""
    echo ""
    
    case "$container_action" in
      1)
        print_info "Keeping existing containers, will create any missing ones"
        ;;
      2)
        # Destroy specific containers
        echo "Enter container IDs to destroy (space-separated, e.g., 200 203 208):"
        read -r ctids_to_destroy
        
        if [[ -z "$ctids_to_destroy" ]]; then
          print_error "No container IDs provided"
          return 1
        fi
        
        echo ""
        print_warning "Will destroy: $ctids_to_destroy"
        read -p "Confirm? (y/N): " -n 1 -r
        echo ""
        
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
          print_info "Cancelled"
          return 0
        fi
        
        echo ""
        for ctid in $ctids_to_destroy; do
          if pct status "$ctid" &>/dev/null; then
            print_info "Destroying container $ctid..."
            pct stop "$ctid" 2>/dev/null || true
            sleep 1
            pct destroy "$ctid" --purge 2>/dev/null || true
          fi
        done
        print_success "Selected containers destroyed"
        ;;
      3)
        # Destroy all containers
        echo ""
        print_warning "This will DESTROY ALL $MODE containers!"
        read -p "Are you absolutely sure? (type 'yes' to confirm): " confirm
        
        if [[ "$confirm" != "yes" ]]; then
          print_info "Cancelled"
          return 0
        fi
        
        echo ""
        if [[ "$MODE" == "test" ]]; then
          print_info "Destroying all test containers..."
          bash "${PCT_DIR}/diagnostic/destroy_test.sh" || true
        else
          print_info "Destroying all production containers..."
          for ctid in 200 201 202 203 204 205 206 207 208; do
            if pct status "$ctid" &>/dev/null; then
              print_info "Destroying container $ctid..."
              pct stop "$ctid" 2>/dev/null || true
              sleep 1
              pct destroy "$ctid" --purge 2>/dev/null || true
            fi
          done
        fi
        print_success "All containers destroyed"
        ;;
      4)
        print_info "Skipping container creation"
        return 0
        ;;
      *)
        print_error "Invalid choice"
        return 1
        ;;
    esac
  else
    # Some containers missing
    if list_existing_containers "$MODE"; then
      echo ""
      print_info "Some containers exist, missing containers will be created"
    else
      print_info "No existing containers found, will create all"
    fi
  fi
  
  # Summary of what will be created
  echo ""
  echo "=========================================="
  echo "Container Creation Summary"
  echo "=========================================="
  echo ""
  echo "Environment: $MODE"
  echo ""
  echo "Containers to create/verify:"
  echo "  - Core services: proxy, apps, agent"
  echo "  - Data services: postgres, milvus, minio"
  echo "  - Worker services: ingest (GPU 0), litellm"
  echo "  - LLM services: vLLM (GPUs 1+, requires 2+ GPUs)"
  echo ""
  echo "GPU Allocation Strategy:"
  echo "  - GPU 0: Ingest container (Marker PDF extraction + ColPali visual embeddings)"
  echo "  - GPUs 1+: vLLM container (LLM inference with tensor parallelism)"
  echo ""
  
  # Optional Ollama
  echo "Optional: Ollama LXC Container"
  echo "  - Container ID: 210 (production) / 310 (test)"
  echo "  - Uses single GPU (shares with ingest or vLLM)"
  echo "  - Alternative to vLLM for some use cases"
  echo "  - Not required (vLLM is primary inference engine)"
  echo ""
  read -p "Include Ollama container? (y/N): " -n 1 -r
  echo ""
  
  OLLAMA_FLAG=""
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    OLLAMA_FLAG="--with-ollama"
    print_info "Will create: vLLM + Ollama"
  else
    print_info "Will create: vLLM only (Ollama skipped)"
  fi
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

# Step 2.5: GPU Configuration
step_gpu_configuration() {
  print_header "Step 2.5: GPU Configuration"
  
  # Check if GPUs are available
  if ! command -v nvidia-smi &>/dev/null; then
    print_warning "nvidia-smi not found on host"
    echo "GPUs may not be available. Continue anyway?"
    read -p "(y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      print_info "Skipping GPU configuration"
      return 0
    fi
  fi
  
  # Get GPU count
  local gpu_count=$(nvidia-smi -L 2>/dev/null | wc -l || echo "0")
  
  if [[ "$gpu_count" -eq 0 ]]; then
    print_warning "No GPUs detected on host"
    print_info "Skipping GPU configuration"
    return 0
  fi
  
  print_success "Detected $gpu_count GPU(s) on host"
  echo ""
  
  # Detect mode from previous step or ask
  if [[ -f /tmp/busibox_setup_mode ]]; then
    MODE=$(cat /tmp/busibox_setup_mode)
    print_info "Using $MODE environment from previous step"
  else
    echo "Select environment:"
    echo "  1) Production"
    echo "  2) Test"
    echo ""
    read -p "Choose [1-2]: " -n 1 -r env_choice
    echo ""
    echo ""
    
    case "$env_choice" in
      1) MODE="production" ;;
      2) MODE="test" ;;
      *)
        print_error "Invalid choice"
        return 1
        ;;
    esac
  fi
  
  # Get container IDs based on mode
  local CT_INGEST CT_VLLM CT_LITELLM
  if [[ "$MODE" == "test" ]]; then
    CT_INGEST="306"
    CT_VLLM="308"
    CT_LITELLM="307"
  else
    CT_INGEST="206"
    CT_VLLM="208"
    CT_LITELLM="207"
  fi
  
  echo ""
  echo "GPU Configuration Options:"
  echo "  1) Configure GPUs for all containers (ingest, vLLM, litellm)"
  echo "  2) Configure GPUs for specific container"
  echo "  3) Configure vLLM model routing (assign models to GPUs)"
  echo "  4) Skip GPU configuration"
  echo ""
  read -p "Choose [1-4]: " -n 1 -r gpu_choice
  echo ""
  echo ""
  
  case "$gpu_choice" in
    1)
      # Configure all containers
      print_info "Configuring GPUs for all containers..."
      
      # Ingest container
      if pct status "$CT_INGEST" &>/dev/null; then
        print_info "Configuring GPUs for ingest container ($CT_INGEST)..."
        if bash "${PCT_DIR}/host/configure-container-gpus.sh" "$CT_INGEST"; then
          print_success "Ingest container GPU configuration complete"
        else
          print_warning "Ingest container GPU configuration failed"
        fi
      else
        print_warning "Ingest container ($CT_INGEST) not found"
      fi
      
      # vLLM container
      if pct status "$CT_VLLM" &>/dev/null; then
        print_info "Configuring GPUs for vLLM container ($CT_VLLM)..."
        if bash "${PCT_DIR}/host/configure-container-gpus.sh" "$CT_VLLM"; then
          print_success "vLLM container GPU configuration complete"
        else
          print_warning "vLLM container GPU configuration failed"
        fi
      else
        print_warning "vLLM container ($CT_VLLM) not found"
      fi
      
      # LiteLLM container (usually doesn't need GPUs, but check anyway)
      if pct status "$CT_LITELLM" &>/dev/null; then
        echo ""
        read -p "Configure GPUs for LiteLLM container? (y/N): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
          print_info "Configuring GPUs for LiteLLM container ($CT_LITELLM)..."
          if bash "${PCT_DIR}/host/configure-container-gpus.sh" "$CT_LITELLM"; then
            print_success "LiteLLM container GPU configuration complete"
          else
            print_warning "LiteLLM container GPU configuration failed"
          fi
        fi
      fi
      
      print_success "GPU configuration complete for all containers"
      ;;
    2)
      # Configure specific container
      echo "Enter container ID to configure:"
      read -r container_id
      
      if [[ -z "$container_id" ]]; then
        print_error "No container ID provided"
        return 1
      fi
      
      if ! pct status "$container_id" &>/dev/null; then
        print_error "Container $container_id does not exist"
        return 1
      fi
      
      print_info "Configuring GPUs for container $container_id..."
      if bash "${PCT_DIR}/host/configure-container-gpus.sh" "$container_id"; then
        print_success "GPU configuration complete"
      else
        print_error "GPU configuration failed"
        return 1
      fi
      ;;
    3)
      # Configure vLLM model routing
      print_info "Configuring vLLM model routing..."
      echo ""
      echo "This will:"
      echo "  - Show model memory estimates"
      echo "  - Allow you to assign models to specific GPUs"
      echo "  - Configure multiple models on single GPU if memory allows"
      echo "  - Automatically update LiteLLM configuration"
      echo ""
      read -p "Continue? (Y/n): " -n 1 -r
      echo ""
      
      if [[ $REPLY =~ ^[Nn]$ ]]; then
        print_info "Skipping vLLM model routing"
        return 0
      fi
      
      if bash "${PCT_DIR}/host/configure-vllm-model-routing.sh" --interactive --auto-update; then
        print_success "vLLM model routing configured"
      else
        print_warning "vLLM model routing configuration failed"
      fi
      ;;
    4)
      print_info "Skipping GPU configuration"
      return 0
      ;;
    *)
      print_error "Invalid choice"
      return 1
      ;;
  esac
  
  return 0
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
  
  # Check for encrypted vault
  VAULT_PASS_FLAG=""
  if check_vault_encrypted; then
    print_warning "Encrypted Ansible vault detected"
    echo "  File: roles/secrets/vars/vault.yml"
    echo ""
    
    # Check if vault password file exists
    if [[ -f ~/.vault_pass ]]; then
      print_info "Found vault password file: ~/.vault_pass"
      VAULT_PASS_FLAG="--vault-password-file ~/.vault_pass"
    else
      print_warning "Vault password file not found: ~/.vault_pass"
      echo "  You will be prompted for the vault password"
      VAULT_PASS_FLAG="--ask-vault-pass"
    fi
    echo ""
  fi
  
  # Ansible deployment loop (allows multiple tag-based deployments)
  while true; do
    echo ""
    echo "Ansible configuration options:"
    echo "  1) Full deployment (all services)"
    echo "  2) Specific services (use tags)"
    echo "  3) Custom command"
    echo "  4) Done with Ansible configuration"
    echo ""
    read -p "Choose [1-4]: " -n 1 -r ansible_choice
    echo ""
    echo ""
    
    case "$ansible_choice" in
      1)
        # Full deployment
        print_info "Running full Ansible deployment for $MODE..."
        if [[ -n "$VAULT_PASS_FLAG" ]] && [[ "$VAULT_PASS_FLAG" == *"--ask-vault-pass"* ]]; then
          print_warning "You will be prompted for the vault password"
        fi
        echo ""
        cd "$ANSIBLE_DIR"
        
        # Use make with vault password if needed
        if [[ -n "$VAULT_PASS_FLAG" ]]; then
          # Run ansible-playbook directly with vault password
          ansible-playbook -i "inventory/${MODE}/hosts.yml" site.yml $VAULT_PASS_FLAG
        else
          make "$MODE"
        fi
        
        echo ""
        print_success "Full deployment complete!"
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      2)
        # Tag-based deployment (can repeat)
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
          continue
        fi
        
        print_info "Running Ansible with tags: $tags"
        if [[ -n "$VAULT_PASS_FLAG" ]] && [[ "$VAULT_PASS_FLAG" == *"--ask-vault-pass"* ]]; then
          print_warning "You will be prompted for the vault password"
        fi
        echo ""
        cd "$ANSIBLE_DIR"
        
        if ansible-playbook -i "inventory/${MODE}/hosts.yml" site.yml --tags "$tags" $VAULT_PASS_FLAG; then
          echo ""
          print_success "Tag deployment complete: $tags"
        else
          echo ""
          print_error "Tag deployment failed: $tags"
        fi
        
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      3)
        # Custom command
        echo "Enter custom Ansible command (without 'ansible-playbook'):"
        echo "Note: Script will automatically add $VAULT_PASS_FLAG if vault is encrypted"
        read -r custom_cmd
        echo ""
        
        if [[ -z "$custom_cmd" ]]; then
          print_error "No command provided"
          continue
        fi
        
        print_info "Running custom Ansible command..."
        if [[ -n "$VAULT_PASS_FLAG" ]] && [[ "$VAULT_PASS_FLAG" == *"--ask-vault-pass"* ]]; then
          print_warning "You will be prompted for the vault password"
        fi
        echo ""
        cd "$ANSIBLE_DIR"
        
        if eval "ansible-playbook $custom_cmd $VAULT_PASS_FLAG"; then
          echo ""
          print_success "Custom command complete!"
        else
          echo ""
          print_error "Custom command failed"
        fi
        
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
      4)
        print_info "Finished with Ansible configuration"
        break
        ;;
      *)
        print_error "Invalid choice"
        echo ""
        read -p "Press Enter to continue..." -r
        ;;
    esac
  done
  
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
  echo "  2.5. Configure GPU passthrough and model routing"
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
    echo "  - Continue to GPU configuration if containers already exist"
    echo ""
    read -p "Continue to GPU configuration anyway? (y/N): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      exit 1
    fi
  fi
  
  # Step 2.5: GPU Configuration
  if ! step_gpu_configuration; then
    print_warning "GPU configuration failed or was skipped"
    echo ""
    echo "You can:"
    echo "  - Fix any issues and run this script again"
    echo "  - Configure GPUs manually: bash provision/pct/host/configure-container-gpus.sh"
    echo "  - Continue to Ansible configuration"
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

