# Visual Organization Guide (Authoritative)

**Created**: 2025-10-30  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Reference  
**Related Docs**:  
- `.cursor/rules/001-documentation-organization.md`  
- `.cursor/rules/002-script-organization.md`  
- `CLAUDE.md`

**Scope**: This file is the single canonical reference for how documentation and scripts are organized. `ORGANIZATION_RULES_SUMMARY.md` now simply points here.

## At a Glance
- Documentation lives under `docs/` by category (architecture, deployment, configuration, troubleshooting, reference, guides, session-notes).
- Scripts are organized by execution context: `provision/pct/` (Proxmox host), `scripts/` (admin workstation), `provision/ansible/roles/*/files` (in-container static), `provision/ansible/roles/*/templates` (in-container templated).
- All docs use kebab-case filenames and include metadata (Created, Last Updated, Status, Category, Related Docs).
- Use this guide plus `.cursor/rules/001` and `.cursor/rules/002` when creating or moving any file.

## Documentation Organization

```
┌─────────────────────────────────────────────────────────────┐
│                  docs/ (All Documentation)                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐ │
│  │ architecture/  │  │ deployment/    │  │configuration/ │ │
│  │                │  │                │  │               │ │
│  │ System Design  │  │ How to Deploy  │  │ How to Setup  │ │
│  │ ADRs           │  │ Infrastructure │  │ Configuration │ │
│  │ Testing        │  │ Services       │  │ Secrets       │ │
│  └────────────────┘  └────────────────┘  └───────────────┘ │
│                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐ │
│  │troubleshooting/│  │ reference/     │  │ guides/       │ │
│  │                │  │                │  │               │ │
│  │ Known Issues   │  │ API Specs      │  │ How-To        │ │
│  │ Debug Guides   │  │ Quick Ref      │  │ Tutorials     │ │
│  │ Fixes          │  │ Commands       │  │ Best Practice │ │
│  └────────────────┘  └────────────────┘  └───────────────┘ │
│                                                              │
│  ┌────────────────┐                                         │
│  │ session-notes/ │                                         │
│  │                │                                         │
│  │ Working Notes  │                                         │
│  │ Session Summry │                                         │
│  │ Migration Docs │                                         │
│  └────────────────┘                                         │
└─────────────────────────────────────────────────────────────┘
```

## Script Organization

```
┌───────────────────────────────────────────────────────────────────────┐
│                    Script Organization by Context                      │
├───────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  ADMIN WORKSTATION                                                    │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │ scripts/                                                 │         │
│  │                                                          │         │
│  │  • deploy-*.sh          Orchestrate Ansible             │         │
│  │  • setup-*.sh           Setup workstation/configs       │         │
│  │  • test-*.sh            Run tests from workstation      │         │
│  │  • upload-*.sh          Upload assets/certificates      │         │
│  │                                                          │         │
│  │  Execution: From admin workstation via SSH              │         │
│  │  Privileges: User (Ansible handles escalation)          │         │
│  └─────────────────────────────────────────────────────────┘         │
│                                                                        │
│                               │                                        │
│                               │ SSH                                    │
│                               ▼                                        │
│                                                                        │
│  PROXMOX HOST (PVE)                                                   │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │ provision/pct/                                           │         │
│  │                                                          │         │
│  │  • create_*.sh          Create LXC containers           │         │
│  │  • destroy_*.sh         Destroy containers              │         │
│  │  • configure-*.sh       Configure host/GPU              │         │
│  │  • setup-*.sh           Setup host resources            │         │
│  │  • check-*.sh           Validate host state             │         │
│  │  • list-*.sh            List resources                  │         │
│  │  • vars.env             Production config               │         │
│  │  • test-vars.env        Test config                     │         │
│  │                                                          │         │
│  │  Execution: ON Proxmox host (direct or SSH)             │         │
│  │  Privileges: root (requires pct/pvesm)                  │         │
│  └─────────────────────────────────────────────────────────┘         │
│                                                                        │
│                               │                                        │
│                               │ Ansible                                │
│                               ▼                                        │
│                                                                        │
│  LXC CONTAINERS                                                       │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │ provision/ansible/roles/{role}/files/                    │         │
│  │                                                          │         │
│  │  • *.sh                 Static utility scripts          │         │
│  │                         No environment variables        │         │
│  │                                                          │         │
│  │  Examples: deploywatch.sh, health-check.sh              │         │
│  │                                                          │         │
│  │  Execution: Inside container                            │         │
│  │  Deployed: Copied by Ansible                            │         │
│  └─────────────────────────────────────────────────────────┘         │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │ provision/ansible/roles/{role}/templates/                │         │
│  │                                                          │         │
│  │  • *.sh.j2              Templated scripts               │         │
│  │                         Uses Ansible variables          │         │
│  │                         Environment-specific            │         │
│  │                                                          │         │
│  │  Examples: check-cert-expiry.sh.j2, start.sh.j2         │         │
│  │                                                          │         │
│  │  Execution: Inside container (after templating)         │         │
│  │  Deployed: Templated then copied by Ansible             │         │
│  └─────────────────────────────────────────────────────────┘         │
└───────────────────────────────────────────────────────────────────────┘
```

## Decision Flow for New Documentation

```
                    "I need to create documentation"
                                │
                                ▼
                    ┌───────────────────────┐
                    │ What is the PRIMARY   │
                    │ purpose?              │
                    └───────────┬───────────┘
                                │
                ┌───────────────┼───────────────┐
                │               │               │
                ▼               ▼               ▼
        ┌───────────┐   ┌──────────┐   ┌──────────────┐
        │ Design?   │   │ Deploy?  │   │ Configure?   │
        │ Structure?│   │ Setup    │   │ Setup?       │
        └─────┬─────┘   │ Infra?   │   └──────┬───────┘
              │         └─────┬────┘          │
              ▼               ▼               ▼
       architecture/    deployment/    configuration/
              │               │               │
        ┌─────┴─────┐   ┌────┴────┐   ┌──────┴─────┐
        │           │   │         │   │            │
        ▼           ▼   ▼         ▼   ▼            ▼
    system.md   adr-*.md  service.md  vault.md  local.md
    design.md   testing.md env.md     secrets.md setup.md
                                                        
                ┌───────────────┬───────────────┐
                │               │               │
                ▼               ▼               ▼
        ┌───────────┐   ┌──────────┐   ┌──────────┐
        │ Fix       │   │ API/Ref  │   │ How-To?  │
        │ Issues?   │   │ Quick    │   │ Tutorial?│
        │ Debug?    │   │ Lookup?  │   │ Guide?   │
        └─────┬─────┘   └─────┬────┘   └────┬─────┘
              ▼               ▼              ▼
       troubleshooting/   reference/     guides/
              │               │              │
        ┌─────┴─────┐   ┌────┴────┐   ┌─────┴────┐
        │           │   │         │   │          │
        ▼           ▼   ▼         ▼   ▼          ▼
    debug.md  fixes.md  api.md  quick.md  how-to.md
    issues.md          command.md        tutorial.md
                                                        
                        │
                        ▼
                ┌──────────────┐
                │ Session      │
                │ Notes?       │
                │ WIP?         │
                └──────┬───────┘
                       ▼
                 session-notes/
                       │
                ┌──────┴───────┐
                │              │
                ▼              ▼
    session-DATE.md    migration.md
    implementation.md  refactoring.md
```

## Decision Flow for New Scripts

```
                    "I need to create a script"
                                │
                                ▼
                    ┌───────────────────────┐
                    │ WHERE will this       │
                    │ script execute?       │
                    └───────────┬───────────┘
                                │
                ┌───────────────┼───────────────┐
                │               │               │
                ▼               ▼               ▼
        ┌───────────┐   ┌──────────┐   ┌─────────────┐
        │ Proxmox   │   │ Admin    │   │ Inside      │
        │ Host?     │   │ Workst?  │   │ Container?  │
        │ (uses pct)│   │(Ansible) │   │             │
        └─────┬─────┘   └─────┬────┘   └──────┬──────┘
              │               │                │
              ▼               ▼                ▼
      provision/pct/      scripts/    ┌───────────────┐
              │               │        │ Needs Ansible │
        ┌─────┴─────┐   ┌────┴────┐  │ variables?    │
        │           │   │         │   └───┬───────┬───┘
        ▼           ▼   ▼         ▼       │       │
    create_*.sh  setup-*.sh   deploy-*.sh │       │
    config-*.sh  test-*.sh    setup-*.sh  │       │
    check-*.sh   upload-*.sh              │       │
                                          ▼       ▼
                                        YES      NO
                                          │       │
                                          ▼       ▼
                            roles/{role}/     roles/{role}/
                              templates/          files/
                                  │                 │
                            ┌─────┴─────┐    ┌──────┴──────┐
                            │           │    │             │
                            ▼           ▼    ▼             ▼
                        start.sh.j2  env.sh.j2  util.sh  health.sh
                        deploy.sh.j2          monitor.sh backup.sh
```

## Naming Conventions Visual

### Documentation Files

```
┌─────────────────────────────────────────┐
│ GOOD EXAMPLES ✅                        │
├─────────────────────────────────────────┤
│ docs/architecture/architecture.md       │
│ docs/architecture/zfs-storage.md        │
│ docs/deployment/ai-portal.md            │
│ docs/configuration/vault-secrets.md     │
│ docs/troubleshooting/deployment-debug.md│
│ docs/reference/quick-reference.md       │
│ docs/session-notes/session-2025-10-30.md│
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│ BAD EXAMPLES ❌                         │
├─────────────────────────────────────────┤
│ docs/DEPLOYMENT_SUMMARY.md              │  → Use kebab-case
│ docs/SessionNotes.md                    │  → Use kebab-case
│ docs/readme.txt                         │  → Use .md extension
│ AI_PORTAL_DEPLOY.md                     │  → Missing category dir
└─────────────────────────────────────────┘
```

### Script Files

```
┌─────────────────────────────────────────┐
│ GOOD EXAMPLES ✅                        │
├─────────────────────────────────────────┤
│ scripts/deploy-ai-portal.sh             │  Admin workstation
│ provision/pct/create_lxc_base.sh        │  Proxmox host
│ roles/nginx/files/health-check.sh       │  Container (static)
│ roles/nginx/templates/start.sh.j2       │  Container (template)
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│ BAD EXAMPLES ❌                         │
├─────────────────────────────────────────┤
│ scripts/create-containers.sh            │  → Should be in pct/
│ provision/pct/deploy-ansible.sh         │  → Should be in scripts/
│ roles/nginx/files/start.sh              │  → Needs vars, use .j2
│ scripts/DeployApp.sh                    │  → Use kebab-case
└─────────────────────────────────────────┘
```

## File Headers Visual

### Documentation Header

```markdown
┌─────────────────────────────────────────────────────┐
│ # Document Title                                    │
│                                                     │
│ **Created**: 2025-10-30                            │
│ **Last Updated**: 2025-10-30                       │
│ **Status**: Active                                 │
│ **Category**: Deployment                           │
│ **Related Docs**:                                  │
│ - [Architecture](../architecture/architecture.md)  │
│                                                     │
│ ## Overview                                        │
│ Brief description of what this document covers...  │
└─────────────────────────────────────────────────────┘
```

### Script Header

```bash
┌─────────────────────────────────────────────────────┐
│ #!/usr/bin/env bash                                 │
│ #                                                   │
│ # Deploy AI Portal                                  │
│ #                                                   │
│ # Purpose: Deploy ai-portal to test environment    │
│ #                                                   │
│ # Execution Context: Admin Workstation             │
│ # Required Privileges: user (Ansible escalates)    │
│ # Dependencies: ansible                            │
│ #                                                   │
│ # Usage:                                           │
│ #   bash deploy-ai-portal.sh [--skip-ssl]         │
│ #                                                   │
│                                                     │
│ set -euo pipefail                                   │
└─────────────────────────────────────────────────────┘
```

## Quick Reference Card

```
╔═══════════════════════════════════════════════════════════╗
║              BUSIBOX ORGANIZATION QUICK REF               ║
╠═══════════════════════════════════════════════════════════╣
║                                                           ║
║  DOCUMENTATION                SCRIPTS                     ║
║  ─────────────                ───────                     ║
║  docs/                        scripts/          Worksta.  ║
║   ├─ architecture/            provision/pct/    PVE Host  ║
║   ├─ deployment/              roles/*/files/    Container ║
║   ├─ configuration/           roles/*/templates/ + vars   ║
║   ├─ troubleshooting/                                     ║
║   ├─ reference/              NAMING                       ║
║   ├─ guides/                 ──────                       ║
║   └─ session-notes/          kebab-case.md                ║
║                              prefix-action.sh              ║
║  DECISION                                                 ║
║  ────────                    HEADERS                      ║
║  Purpose → Category          ───────                      ║
║  Context → Location          Metadata in docs             ║
║  Action  → Prefix            Context in scripts           ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
```

## Color-Coded Flow (Conceptual)

```
📘 ARCHITECTURE (Blue)     - System design, decisions, structure
   docs/architecture/

🚀 DEPLOYMENT (Green)      - How to deploy, infrastructure setup  
   docs/deployment/

⚙️  CONFIGURATION (Yellow)  - How to configure, setup guides
   docs/configuration/

🔧 TROUBLESHOOTING (Red)   - Fixes, debug, known issues
   docs/troubleshooting/

📖 REFERENCE (Purple)      - API docs, quick refs, commands
   docs/reference/

📚 GUIDES (Teal)           - How-to, tutorials, best practices
   docs/guides/

📝 SESSION NOTES (Gray)    - Working notes, session summaries
   docs/session-notes/
```

## Summary

This visual guide provides:

✅ **Clear categorization** - Visual representation of structure
✅ **Decision flows** - Easy-to-follow decision trees
✅ **Examples** - Good and bad examples for reference
✅ **Headers** - Templates for required headers
✅ **Quick reference** - One-page summary card

Use this guide alongside:
- `.cursor/rules/001-documentation-organization.md` - Detailed rules
- `.cursor/rules/002-script-organization.md` - Script rules
- `CLAUDE.md` - Complete project guide



