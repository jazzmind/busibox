---
title: "Agent Manager Auth Redirect"
category: "administrator"
order: 50
description: "Fix Agent Manager auth redirect to localhost instead of correct domain"
published: true
---

# Agent-Manager Auth Redirect Issue

## Problem

Agent-manager on Proxmox/staging was redirecting authentication to `localhost:3000` instead of `staging.ai.localhost`, causing auth to fail.

## Root Cause

The `AuthContext.tsx` component uses this code:

```typescript
const portalUrl = process.env.NEXT_PUBLIC_AI_PORTAL_URL || 'http://localhost:3000';
```

In Next.js, `NEXT_PUBLIC_*` environment variables are **embedded at build time**, not runtime. The Ansible configuration was setting `AI_PORTAL_URL` but **not** `NEXT_PUBLIC_AI_PORTAL_URL`, so the build fell back to the default `localhost:3000`.

### Verification

Checked the deployed `.env` file on staging:

```bash
ssh root@10.96.201.201 "grep 'NEXT_PUBLIC' /srv/apps/agent-manager/.env"

NEXT_PUBLIC_AGENT_API_URL="http://10.96.201.202:8000"
NEXT_PUBLIC_APP_URL="https://staging.ai.localhost/agents"
NEXT_PUBLIC_BASE_PATH="/agents"
# ❌ NEXT_PUBLIC_AI_PORTAL_URL was missing!
```

Even though `AI_PORTAL_URL` was set correctly in the file, Next.js couldn't access it because it doesn't have the `NEXT_PUBLIC_` prefix.

## Solution

Updated `provision/ansible/group_vars/all/apps.yml` to include both variables:

```yaml
env:
  AI_PORTAL_URL: "https://{{ full_domain }}"           # For server-side code
  NEXT_PUBLIC_AI_PORTAL_URL: "https://{{ full_domain }}" # For Next.js build time
  NEXT_PUBLIC_APP_URL: "https://{{ full_domain }}/agents"
  NEXT_PUBLIC_BASE_PATH: "/agents"
```

## Why Both Variables?

- **`AI_PORTAL_URL`**: Available at runtime on the server (for API routes, etc.)
- **`NEXT_PUBLIC_AI_PORTAL_URL`**: Embedded at build time and available in browser code

Next.js requires the `NEXT_PUBLIC_` prefix for any environment variables that need to be accessible in client-side code.

## Fix Applied

**File:** `provision/ansible/group_vars/all/apps.yml`

**Change:** Added `NEXT_PUBLIC_AI_PORTAL_URL: "https://{{ full_domain }}"` to the agent-manager env section (line 132)

## Testing

After redeploying agent-manager with the updated configuration:

1. The `.env` file will include `NEXT_PUBLIC_AI_PORTAL_URL`
2. The build will embed the correct domain
3. Auth redirects will go to `https://staging.ai.localhost` instead of `localhost:3000`

### To Deploy the Fix

```bash
cd /Users/wsonnenreich/Code/busibox
git add provision/ansible/group_vars/all/apps.yml
git commit -m "fix: add NEXT_PUBLIC_AI_PORTAL_URL for agent-manager auth redirect"
git push

# Then on Proxmox
cd /root/busibox
git pull
cd provision/ansible
ansible-playbook -i inventory/staging/hosts.yml site.yml --tags app_deployer -e "deploy_app=agent-manager"
```

Or trigger a redeploy from the web UI once the code is pushed.

## Related Issues

This same issue could affect other Next.js applications if they reference environment variables in client-side code without the `NEXT_PUBLIC_` prefix.

### Best Practice

For Next.js applications in Busibox:

1. **Server-side only** (API routes, getServerSideProps): Use regular env vars
2. **Client-side** (components, hooks): Use `NEXT_PUBLIC_*` prefix
3. **Both**: Set both versions in Ansible if the variable is needed in both contexts

### Example

```yaml
# ai-portal env configuration (already correct)
env:
  # Server-side
  LITELLM_BASE_URL: "http://{{ litellm_ip }}:{{ litellm_port }}/v1"
  
  # Client-side (browser)
  NEXT_PUBLIC_APP_URL: "https://{{ full_domain }}/portal"
  NEXT_PUBLIC_BASE_PATH: "/portal"
  
  # Both server and client need this
  BASE_URL: "https://{{ full_domain }}/portal"          # Server
  # (ai-portal doesn't currently need NEXT_PUBLIC_BASE_URL for auth)
```

## Documentation

See Next.js environment variables documentation:
- https://nextjs.org/docs/app/building-your-application/configuring/environment-variables

Key points:
- `NEXT_PUBLIC_*` variables are embedded at build time
- Regular env vars are only available server-side
- Changes to `NEXT_PUBLIC_*` variables require rebuild

## Related Files

- `provision/ansible/group_vars/all/apps.yml` - Application environment configuration
- `/Users/wsonnenreich/Code/agent-manager/components/auth/AuthContext.tsx:68` - Where the redirect URL is used
- `/Users/wsonnenreich/Code/agent-manager/env.example` - Example configuration showing correct variable names
