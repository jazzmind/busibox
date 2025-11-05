#!/bin/bash
#
# Setup ZFS storage for Busibox persistent data
# Run this on the Proxmox host to create dedicated datasets for databases and storage
#
set -e

# Color output functions
log_info() {
    echo -e "\e[34m[INFO]\e[0m $1"
}

log_success() {
    echo -e "\e[32m[SUCCESS]\e[0m $1"
}

log_error() {
    echo -e "\e[31m[ERROR]\e[0m $1"
}

log_warning() {
    echo -e "\e[33m[WARNING]\e[0m $1"
}

echo "=========================================="
echo "ZFS Storage Setup for Busibox"
echo "=========================================="
echo ""

# Check if running on Proxmox host
if ! command -v zfs &> /dev/null; then
    log_error "ZFS not found. This script must run on the Proxmox host."
    exit 1
fi

if ! command -v pct &> /dev/null; then
    log_error "Proxmox 'pct' command not found. This script must run on the Proxmox host."
    exit 1
fi

log_success "Running on Proxmox host with ZFS support"
echo ""

# Show current ZFS status
log_info "Current ZFS pools:"
zpool list
echo ""

log_info "Current datasets under rpool:"
zfs list | grep -E "^rpool" || true
echo ""

# Confirm action
log_warning "This script will create ZFS datasets for persistent data:"
echo "  - rpool/data/postgres  -> /var/lib/data/postgres"
echo "  - rpool/data/minio     -> /var/lib/data/minio"
echo "  - rpool/data/milvus    -> /var/lib/data/milvus"
echo ""
read -p "Continue? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    log_info "Aborted by user"
    exit 0
fi

# Create parent dataset if it doesn't exist
if zfs list rpool/data &>/dev/null; then
    log_info "Parent dataset 'rpool/data' already exists"
else
    log_info "Creating parent dataset 'rpool/data'..."
    zfs create rpool/data
    log_success "Created rpool/data"
fi

# Function to create and configure a dataset
setup_dataset() {
    local name=$1
    local mountpoint=$2
    local recordsize=$3
    local logbias=$4
    local extra_opts=$5
    
    log_info "Setting up dataset: rpool/data/${name}"
    
    # Create dataset if it doesn't exist
    if zfs list "rpool/data/${name}" &>/dev/null; then
        log_warning "Dataset 'rpool/data/${name}' already exists, skipping creation"
    else
        zfs create "rpool/data/${name}"
        log_success "Created rpool/data/${name}"
    fi
    
    # Set properties
    zfs set mountpoint="${mountpoint}" "rpool/data/${name}"
    zfs set compression=lz4 "rpool/data/${name}"
    zfs set recordsize="${recordsize}" "rpool/data/${name}"
    zfs set logbias="${logbias}" "rpool/data/${name}"
    
    # Apply extra options if provided
    if [[ -n "$extra_opts" ]]; then
        eval "zfs set $extra_opts rpool/data/${name}"
    fi
    
    log_success "Configured rpool/data/${name}"
    echo "  Mountpoint: ${mountpoint}"
    echo "  Compression: lz4"
    echo "  Record size: ${recordsize}"
    echo "  Log bias: ${logbias}"
    [[ -n "$extra_opts" ]] && echo "  Extra: ${extra_opts}"
    echo ""
}

# Setup PostgreSQL dataset (optimized for database workload)
setup_dataset \
    "postgres" \
    "/var/lib/data/postgres" \
    "8K" \
    "latency" \
    ""

# Setup MinIO dataset (optimized for large files)
setup_dataset \
    "minio" \
    "/var/lib/data/minio" \
    "1M" \
    "throughput" \
    ""

# Setup Milvus dataset (optimized for vector data)
setup_dataset \
    "milvus" \
    "/var/lib/data/milvus" \
    "128K" \
    "latency" \
    "primarycache=metadata"

# Optional: Set quotas (commented out by default)
log_info "Quotas not set. To set quotas, run:"
echo "  zfs set quota=100G rpool/data/postgres"
echo "  zfs set quota=500G rpool/data/minio"
echo "  zfs set quota=200G rpool/data/milvus"
echo ""

# Show final status
log_success "ZFS datasets created successfully!"
echo ""
log_info "Dataset status:"
zfs list -o name,used,avail,refer,compressratio,mountpoint rpool/data
echo ""

# Show next steps
log_info "=========================================="
log_info "Next Steps - Migration"
log_info "=========================================="
echo ""
echo "To migrate existing data to these datasets:"
echo ""
echo "1. Stop the service in its container"
echo "   Example: pct exec 203 -- systemctl stop postgresql"
echo ""
echo "2. Copy data from container to host dataset"
echo "   Example: pct exec 203 -- tar czf /tmp/backup.tar.gz -C /var/lib/postgresql ."
echo "            pct pull 203 /tmp/backup.tar.gz /tmp/pg_backup.tar.gz"
echo "            tar xzf /tmp/pg_backup.tar.gz -C /var/lib/data/postgres/"
echo ""
echo "3. Stop container and add bind mount to config"
echo "   Example: pct stop 203"
echo "            echo 'mp0: /var/lib/data/postgres,mp=/var/lib/postgresql/data' >> /etc/pve/lxc/203.conf"
echo ""
echo "4. Start container and verify service"
echo "   Example: pct start 203"
echo "            pct exec 203 -- systemctl status postgresql"
echo ""
echo "See docs/ZFS_STORAGE_STRATEGY.md for detailed migration procedures."
echo ""

log_success "ZFS storage setup complete!"

