# =============================================================================
# Busibox Rackspace Spot - Infrastructure as Code
# =============================================================================
#
# Manages the Rackspace Spot cloudspace and node pools.
# Supports:
#   - Base node pool (always running, memory-optimized)
#   - GPU burst node pool (on-demand, for heavy AI workloads)
#
# Usage:
#   terraform init
#   terraform plan
#   terraform apply                           # Deploy base infrastructure
#   terraform apply -var="gpu_enabled=true"   # Add GPU node
#   terraform apply -var="gpu_enabled=false"  # Remove GPU node
#
# =============================================================================

terraform {
  required_providers {
    spot = {
      source  = "rackerlabs/spot"
      version = ">= 0.1.0"
    }
  }
}

# =============================================================================
# Variables
# =============================================================================

variable "rackspace_spot_token" {
  description = "Rackspace Spot API token"
  type        = string
  sensitive   = true
}

variable "cloudspace_name" {
  description = "Name of the cloudspace"
  type        = string
  default     = "sonnenreich-dev"
}

variable "region" {
  description = "Rackspace Spot region"
  type        = string
  default     = "us-east-iad-1"
}

# Base node pool configuration
# Persistent storage: db-ssd (50Gi ssdv2-performance) + objects-store (100Gi ssdv2)
# Everything else uses ephemeral node storage (emptyDir).
# Node ephemeral disk is used for Docker layer cache, image registry, model caches.
variable "base_server_class" {
  description = "Server class for the base node pool"
  type        = string
  default     = "mh.vs1.xlarge-iad"  # 8 CPU, 60GB RAM - IAD region
}

variable "base_bid_price" {
  description = "Bid price for base nodes (USD/hr)"
  type        = number
  default     = 0.05
}

variable "base_node_count" {
  description = "Number of base nodes"
  type        = number
  default     = 1
}

# GPU burst node pool configuration
variable "gpu_enabled" {
  description = "Whether to provision GPU node pool (for burst AI workloads)"
  type        = bool
  default     = false
}

variable "gpu_server_class" {
  description = "Server class for GPU nodes (check available classes with terraform data source)"
  type        = string
  default     = "gpu.vs1.large-iad"  # Placeholder - check actual GPU classes available
}

variable "gpu_bid_price" {
  description = "Bid price for GPU nodes (USD/hr)"
  type        = number
  default     = 0.50  # GPU nodes cost more
}

variable "gpu_node_count" {
  description = "Number of GPU nodes to provision"
  type        = number
  default     = 1
}

# =============================================================================
# Provider
# =============================================================================

provider "spot" {
  token = var.rackspace_spot_token
}

# =============================================================================
# Data Sources - Discover available server classes
# =============================================================================

# List all available server classes (useful for finding GPU classes)
data "spot_serverclasses" "all" {}

# Filter for GPU-capable server classes
data "spot_serverclasses" "gpu" {
  filters = [
    {
      name   = "category"
      values = ["GPU"]
    }
  ]
}

# =============================================================================
# Node Pools
# =============================================================================

# Base node pool - always running, runs Busibox infrastructure + APIs
# Note: This manages the existing node pool. If the cloudspace already exists,
# import this resource: terraform import spot_spotnodepool.base <nodepool-id>
resource "spot_spotnodepool" "base" {
  cloudspace_name      = var.cloudspace_name
  server_class         = var.base_server_class
  bid_price            = var.base_bid_price
  desired_server_count = var.base_node_count

  labels = {
    "busibox/role"    = "base"
    "busibox/tier"    = "always-on"
    "managed-by"      = "terraform"
  }
}

# GPU burst node pool - provisioned on-demand for heavy AI workloads
# Set gpu_enabled=true to create, gpu_enabled=false to destroy
resource "spot_spotnodepool" "gpu" {
  count = var.gpu_enabled ? 1 : 0

  cloudspace_name      = var.cloudspace_name
  server_class         = var.gpu_server_class
  bid_price            = var.gpu_bid_price
  desired_server_count = var.gpu_node_count

  labels = {
    "busibox/role"    = "gpu-burst"
    "busibox/tier"    = "on-demand"
    "managed-by"      = "terraform"
  }

  # Taint GPU nodes so only GPU workloads get scheduled there
  taints = [
    {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NoSchedule"
    }
  ]
}

# =============================================================================
# Outputs
# =============================================================================

output "available_server_classes" {
  description = "All available server classes"
  value       = data.spot_serverclasses.all.names
}

output "gpu_server_classes" {
  description = "Available GPU server classes"
  value       = data.spot_serverclasses.gpu.names
}

output "base_nodepool_status" {
  description = "Base node pool bid status"
  value       = spot_spotnodepool.base.bid_status
}

output "base_nodepool_won_count" {
  description = "Base node pool won bid count"
  value       = spot_spotnodepool.base.won_count
}

output "gpu_nodepool_status" {
  description = "GPU node pool bid status (if enabled)"
  value       = var.gpu_enabled ? spot_spotnodepool.gpu[0].bid_status : "disabled"
}
