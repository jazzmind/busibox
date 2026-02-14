---
title: "ADR-0001: Container Isolation"
category: "developer"
order: 80
description: "Architecture decision on deployment-specific configuration and vault usage"
published: true
---

# Architecture Decision: Multi-Deployment Generic Template

**Date**: 2025-10-23  
**Decision**: Vault contains deployment-specific configuration (networks, domains, secrets)

## Context

The busibox repository is designed as a **generic, reusable infrastructure template** that can be deployed across multiple customer environments, each with:
- Different network ranges
- Different domain names
- Different secrets

## Decision

**Deployment-specific values (IPs, domains, secrets) are stored in `vault.yml`, NOT in version control.**

### What Goes in Vault (Deployment-Specific)
```yaml
# vault.yml - encrypted, gitignored
network_base_octets_production: "10.96.200"
network_base_octets_staging: "10.96.201"
base_domain: "customer1.com"
ssl_email: "admin@customer1.com"
secrets:
  postgresql:
    password: "customer1_password"
  # ...
```

### What Stays in Version Control (Generic)
```yaml
# inventory/production/group_vars/all.yml - version controlled
network_base_octets: "{{ network_base_octets_production }}"
domain: "ai.{{ base_domain }}"
proxy_ip: "{{ network_base_octets }}.200"  # .200 is generic pattern
```

## Rationale

### Why Vault for IPs/Domains?

1. **Multi-Tenancy**: Same repo deploys to different customers
2. **No Hardcoding**: No customer-specific values in generic code
3. **Flexibility**: Each deployment has completely different infrastructure
4. **Updates**: Update generic patterns, deploy to all customers
5. **Security**: All deployment config encrypted together

### Alternative Considered: IPs/Domains in `all.yml`

**Rejected because**:
- Would require separate git branches per customer
- Or separate repos per customer (defeats purpose of generic template)
- Customer data would be in version control
- Can't easily distribute generic updates

## Usage Pattern

### Single Repo → Multiple Deployments

```
Generic Repo (github.com/yourorg/busibox)
    ↓ clone
    ├── /deployments/customer1/
    │   └── vault.yml (network: 10.96.200.x, domain: customer1.com)
    │
    ├── /deployments/customer2/
    │   └── vault.yml (network: 192.168.100.x, domain: customer2.com)
    │
    └── /deployments/customer3/
        └── vault.yml (network: 172.16.50.x, domain: customer3.io)
```

### Update Workflow

```bash
# Update generic infrastructure
git pull origin main

# Vault is preserved (gitignored)
# Deploy with customer-specific config
ansible-playbook -i inventory/production site.yml --ask-vault-pass
```

## Implementation

### vault.yml (Deployment-Specific)
```yaml
# Network - deployment-specific
network_base_octets_production: "10.96.200"
network_base_octets_staging: "10.96.201"

# Domain - deployment-specific
base_domain: "localhost"
ssl_email: "admin@localhost"

# Secrets - deployment-specific
secrets:
  postgresql:
    password: "..."
```

### all.yml (Generic Pattern)
```yaml
# References vault variables
network_base_octets: "{{ network_base_octets_production }}"
domain: "ai.{{ base_domain }}"

# Generic patterns
proxy_ip: "{{ network_base_octets }}.200"
apps_ip: "{{ network_base_octets }}.201"
```

## Benefits

1. **One Source of Truth**: Single repo for all deployments
2. **Easy Updates**: Fix bug once, deploy everywhere
3. **Isolated Config**: Each deployment fully isolated
4. **No Branches**: No need for customer-specific branches
5. **Secure**: All deployment config encrypted
6. **Flexible**: Customers can have radically different setups

## Trade-offs

### Pros
- ✅ True multi-tenancy
- ✅ Generic codebase
- ✅ Easy distribution of updates
- ✅ No customer data in git

### Cons
- ⚠️ IPs/domains in encrypted file (less visible in diffs)
- ⚠️ Need to decrypt vault to see deployment config
- ⚠️ Team members need vault password

### Mitigation
- `vault.example.yml` documents structure
- Comments in `all.yml` explain where values come from
- `DEPLOYMENT_SPECIFIC.md` documents approach

## Comparison with Alternatives

### Option 1: IPs/Domains in Version Control
```yaml
# all.yml - version controlled
network_base_octets: "10.96.200"  # Customer-specific!
base_domain: "customer1.com"      # Customer-specific!
```
**Problem**: Can't reuse repo for multiple customers

### Option 2: Separate Branches Per Customer
```bash
main branch: generic code
customer1 branch: customer1 config
customer2 branch: customer2 config
```
**Problem**: Hard to merge updates across branches

### Option 3: Separate Repos Per Customer
```bash
busibox-customer1 repo
busibox-customer2 repo
```
**Problem**: Updates must be manually synced

### Option 4 (CHOSEN): Vault for All Deployment Config
```yaml
# vault.yml - deployment-specific
network_base_octets_production: "10.96.200"
base_domain: "customer1.com"
```
**Solution**: Generic repo + deployment-specific vault

## Consistency Check

This decision is consistent with:
- ✅ Security best practices (encrypt deployment config)
- ✅ Multi-tenancy patterns
- ✅ Infrastructure-as-Code principles
- ✅ DRY (Don't Repeat Yourself)
- ✅ Separation of concerns (generic vs specific)

## Documentation

- See `DEPLOYMENT_SPECIFIC.md` for usage
- See `vault.example.yml` for template
- See `README_REFACTORING.md` for implementation

## Conclusion

Storing deployment-specific configuration (networks, domains, secrets) in `vault.yml` enables:
1. Generic, reusable infrastructure code
2. Multiple isolated deployments from one repo
3. Easy distribution of infrastructure updates
4. Secure, encrypted deployment configuration

This is the correct architectural approach for a multi-deployment infrastructure template.

