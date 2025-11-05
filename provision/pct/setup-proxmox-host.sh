#!/usr/bin/env bash
#
# Proxmox Host Setup Script
#
# This script prepares a Proxmox host for running Busibox tests
# Run this ONCE on the Proxmox host before running tests
#

set -euo pipefail

echo "=========================================="
echo "Busibox Proxmox Host Setup"
echo "=========================================="
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "❌ This script must be run as root"
   exit 1
fi

# Check if running on Proxmox
if ! command -v pct &> /dev/null; then
    echo "❌ This script must run on a Proxmox host"
    exit 1
fi

echo "✓ Running on Proxmox host"
echo ""

# 1. Install Ansible
echo "=========================================="
echo "Step 1: Installing Ansible"
echo "=========================================="
if command -v ansible &> /dev/null; then
    echo "✓ Ansible already installed: $(ansible --version | head -1)"
else
    echo "Installing Ansible..."
    apt update
    apt install -y ansible
    echo "✓ Ansible installed: $(ansible --version | head -1)"
fi
echo ""

# 2. Install other dependencies
echo "=========================================="
echo "Step 2: Installing Dependencies"
echo "=========================================="
echo "Installing: curl, git, jq, psql, python3-pip..."
apt install -y curl git jq postgresql-client python3-pip
echo "✓ Dependencies installed"
echo ""

# 3. Update template list
echo "=========================================="
echo "Step 3: Updating Template List"
echo "=========================================="
pveam update
echo "✓ Template list updated"
echo ""

# 4. Check for Debian template
echo "=========================================="
echo "Step 4: Checking for LXC Template"
echo "=========================================="
TEMPLATE="debian-12-standard_12.12-1_amd64.tar.zst"
if [[ -f "/var/lib/vz/template/cache/${TEMPLATE}" ]]; then
    echo "✓ Template already downloaded: ${TEMPLATE}"
else
    echo "Downloading Debian 12 template..."
    pveam download local "${TEMPLATE}"
    echo "✓ Template downloaded"
fi
echo ""

# 5. Generate SSH key if not exists
echo "=========================================="
echo "Step 5: Checking SSH Key"
echo "=========================================="
if [[ -f "/root/.ssh/id_rsa.pub" ]]; then
    echo "✓ SSH key already exists"
else
    echo "Generating SSH key..."
    ssh-keygen -t rsa -b 4096 -f /root/.ssh/id_rsa -N ""
    echo "✓ SSH key generated"
fi
echo ""

# 6. Check and install NVIDIA drivers
echo "=========================================="
echo "Step 6: Checking NVIDIA Drivers"
echo "=========================================="

if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
    CUDA_VERSION=$(nvidia-smi | grep "CUDA Version" | awk '{print $9}')
    echo "✓ NVIDIA drivers already installed"
    echo "  Driver version: ${DRIVER_VERSION}"
    echo "  CUDA version: ${CUDA_VERSION}"
    
    # List GPUs
    echo ""
    echo "Available GPUs:"
    nvidia-smi -L
else
    echo "⚠ NVIDIA drivers not found or not working"
    echo ""
    read -p "Install latest NVIDIA drivers? (y/N): " -n 1 -r
    echo ""
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Installing latest NVIDIA drivers from NVIDIA repository..."
        
        # Detect Debian version
        DEBIAN_VERSION=$(cat /etc/debian_version | cut -d. -f1)
        if [[ "$DEBIAN_VERSION" == "13" ]] || [[ "$DEBIAN_VERSION" == "12" ]]; then
            DEBIAN_CODENAME="debian12"
            echo "  Detected Debian ${DEBIAN_VERSION} - using debian12 NVIDIA repository"
        else
            echo "  ❌ Unsupported Debian version: $DEBIAN_VERSION"
            exit 1
        fi
        
        # Clean up any existing installations
        echo "  Cleaning up old NVIDIA installations..."
        rm -rf /etc/apt/sources.list.d/cuda* /etc/apt/sources.list.d/nvidia*
        rm -rf /usr/share/keyrings/cuda* /usr/share/keyrings/nvidia*
        apt-get purge -y 'nvidia-*' 'cuda-*' 'libnvidia-*' 'libcuda*' 2>/dev/null || true
        apt-get autoremove -y
        apt-get clean
        
        # Install CUDA keyring
        echo "  Installing NVIDIA CUDA repository..."
        cd /tmp
        wget -q https://developer.download.nvidia.com/compute/cuda/repos/${DEBIAN_CODENAME}/x86_64/cuda-keyring_1.1-1_all.deb
        dpkg -i cuda-keyring_1.1-1_all.deb
        rm cuda-keyring_1.1-1_all.deb
        cd -
        
        # Update and install
        echo "  Installing NVIDIA drivers and CUDA toolkit..."
        apt-get update
        apt-get install -y cuda-drivers cuda-toolkit
        
        echo ""
        echo "✓ NVIDIA drivers installed!"
        echo ""
        
        # Create udev rule for nvidia-caps permissions
        echo "  Creating udev rule for NVIDIA capability devices..."
        cat > /etc/udev/rules.d/70-nvidia-caps.rules << 'UDEV_EOF'
# Make NVIDIA capability devices accessible to LXC containers
KERNEL=="nvidia-cap[0-9]*", MODE="0666"
UDEV_EOF
        
        echo ""
        echo "⚠ ⚠ ⚠  REBOOT REQUIRED  ⚠ ⚠ ⚠"
        echo ""
        echo "After reboot:"
        echo "  1. Run: nvidia-smi"
        echo "  2. Verify GPUs are detected"
        echo "  3. Re-run this script to continue setup"
        echo ""
        read -p "Reboot now? (y/N): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            reboot
        else
            exit 0
        fi
    else
        echo "⚠ Skipping NVIDIA driver installation"
        echo "  Note: GPU passthrough to LXC containers requires NVIDIA drivers on host"
    fi
    
    # Check if udev rule exists
    if [[ ! -f /etc/udev/rules.d/70-nvidia-caps.rules ]]; then
        echo "  Creating udev rule for NVIDIA capability devices..."
        cat > /etc/udev/rules.d/70-nvidia-caps.rules << 'UDEV_EOF'
# Make NVIDIA capability devices accessible to LXC containers
KERNEL=="nvidia-cap[0-9]*", MODE="0666"
UDEV_EOF
        
        echo "  Reloading udev rules..."
        udevadm control --reload-rules
        udevadm trigger
        
        echo "  ✓ udev rule created and applied"
    fi
fi
echo ""

# 7. Setup ZFS storage for persistent data
echo "=========================================="
echo "Step 7: Setting up ZFS Storage for Data"
echo "=========================================="

# Check if ZFS is available
if command -v zfs &>/dev/null && zfs list rpool &>/dev/null 2>&1; then
    echo "✓ ZFS detected - setting up datasets for persistent data"
    echo ""
    
    # Create parent dataset
    if ! zfs list rpool/data &>/dev/null 2>&1; then
        echo "  Creating parent dataset: rpool/data"
        zfs create rpool/data
    fi
    
    # Function to create and configure dataset
    setup_zfs_dataset() {
        local name=$1
        local mountpoint=$2
        local recordsize=$3
        local logbias=$4
        
        if ! zfs list "rpool/data/${name}" &>/dev/null 2>&1; then
            echo "  Creating dataset: rpool/data/${name}"
            zfs create "rpool/data/${name}"
            zfs set mountpoint="${mountpoint}" "rpool/data/${name}"
            zfs set compression=lz4 "rpool/data/${name}"
            zfs set recordsize="${recordsize}" "rpool/data/${name}"
            zfs set logbias="${logbias}" "rpool/data/${name}"
            
            # Special tuning for Milvus
            if [[ "$name" == "milvus" ]]; then
                zfs set primarycache=metadata "rpool/data/${name}"
            fi
            
            echo "    ✓ ${mountpoint} (recordsize=${recordsize}, compression=lz4)"
        else
            echo "  ✓ Dataset rpool/data/${name} already exists"
        fi
    }
    
    # Setup datasets for each service
    echo ""
    echo "  Setting up datasets for services:"
    setup_zfs_dataset "postgres" "/var/lib/data/postgres" "8K" "latency"
    setup_zfs_dataset "minio" "/var/lib/data/minio" "1M" "throughput"
    setup_zfs_dataset "milvus" "/var/lib/data/milvus" "128K" "latency"
    
    # Setup LLM models dataset
    if ! zfs list rpool/llm-models &>/dev/null 2>&1; then
        echo "  Creating dataset: rpool/llm-models"
        zfs create -o mountpoint=/var/lib/llm-models rpool/llm-models
        zfs create rpool/llm-models/ollama
        zfs create rpool/llm-models/huggingface
        zfs set compression=lz4 rpool/llm-models
        echo "    ✓ /var/lib/llm-models (for LLM models)"
    else
        echo "  ✓ Dataset rpool/llm-models already exists"
    fi
    
    echo ""
    echo "  ✓ ZFS storage configured"
    echo ""
    echo "  Dataset summary:"
    zfs list -o name,used,avail,compressratio,mountpoint rpool/data 2>/dev/null || true
    zfs list -o name,used,avail,compressratio,mountpoint rpool/llm-models 2>/dev/null || true
    
else
    echo "⚠ ZFS not detected - using regular directories"
    echo "  (ZFS recommended for production - provides snapshots, compression, etc.)"
    echo ""
    
    # Fallback to regular directories
    echo "  Creating directories for persistent data..."
    mkdir -p /var/lib/data/postgres
    mkdir -p /var/lib/data/minio
    mkdir -p /var/lib/data/milvus
    mkdir -p /var/lib/llm-models/ollama
    mkdir -p /var/lib/llm-models/huggingface
    echo "  ✓ Directories created"
fi

echo ""
read -p "Pre-download test models now? (saves time during deployment) (y/N): " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "  Downloading test models..."
    
    # Install Ollama on host
    if ! command -v ollama &>/dev/null; then
        echo "    Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
    fi
    
    # Download Ollama test model
    echo "    Downloading qwen2.5:0.5b for Ollama (~500MB)..."
    OLLAMA_MODELS=/var/lib/llm-models/ollama ollama pull qwen2.5:0.5b
    
    # Set up Python venv for model downloads
    VENV_DIR="/opt/model-downloader"
    if [ ! -d "${VENV_DIR}" ]; then
        echo "    Installing Python venv support..."
        apt-get install -y python3-venv >/dev/null 2>&1
        
        echo "    Creating virtual environment..."
        python3 -m venv "${VENV_DIR}"
    fi
    
    # Install huggingface-hub in venv
    if ! "${VENV_DIR}/bin/python3" -c "import huggingface_hub" 2>/dev/null; then
        echo "    Installing huggingface-hub..."
        "${VENV_DIR}/bin/pip" install -q huggingface-hub
    fi
    
    echo "    Downloading Qwen2.5-0.5B-Instruct for vLLM (~1GB)..."
    HF_HOME=/var/lib/llm-models/huggingface "${VENV_DIR}/bin/python3" -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen2.5-0.5B-Instruct', local_dir_use_symlinks=False)
print('Model downloaded successfully')
" || echo "    Model download failed, can retry later"
    
    echo ""
    echo "  ✓ Models downloaded"
    echo ""
    echo "  Model storage:"
    du -sh /var/lib/llm-models/ollama 2>/dev/null || echo "    Ollama: 0B"
    du -sh /var/lib/llm-models/huggingface 2>/dev/null || echo "    HuggingFace: 0B"
else
    echo "  ⚠ Skipping model download"
    echo "    Models can be downloaded later with:"
    echo "    bash provision/pct/setup-llm-models.sh"
fi

echo ""

# 8. Summary
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Your Proxmox host is ready for Busibox deployment."
echo ""
echo "Next steps:"
echo "  1. Review configuration: vim provision/pct/test-vars.env"
echo "  2. Run tests: bash test-infrastructure.sh full"
echo "  3. Or provision production: cd provision/pct && bash create_lxc_base.sh"
echo ""
echo "Available templates:"
ls -1 /var/lib/vz/template/cache/*.tar.* 2>/dev/null || echo "  (none found)"
echo ""

