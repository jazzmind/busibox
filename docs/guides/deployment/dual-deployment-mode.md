---
title: Dual Deployment Mode Architecture
category: deployment
created: 2025-11-22
updated: 2025-11-22
status: active
tags: [deployment, vercel, busibox, architecture]
---

# Dual Deployment Mode Architecture

## Overview

Applications in the Busibox ecosystem support **dual deployment modes**, allowing the same codebase to be deployed either:
1. **Vercel** - Cloud platform with external services (OpenAI, Resend, Neon)
2. **Busibox** - Local infrastructure with self-hosted services (liteLLM, SMTP, PostgreSQL)

This architecture provides flexibility for development, testing, and production deployments while maintaining a single codebase.

## Deployment Modes Comparison

| Feature | Vercel Mode | Busibox Mode |
|---------|-------------|--------------|
| **AI/LLM** | OpenAI API directly | liteLLM proxy (local models) |
| **Email** | Resend | Local SMTP (or Resend fallback) |
| **Database** | Neon/External PostgreSQL | Local PostgreSQL |
| **File Storage** | Vercel Blob/S3 | MinIO (S3-compatible) |
| **Vector DB** | External Pinecone/etc | Local Milvus |
| **Deployment** | `vercel deploy` | Ansible + PM2 |
| **Scaling** | Automatic | Manual (container resources) |
| **Cost** | Pay per use | Fixed infrastructure |
| **Privacy** | Data leaves premises | All data local |

## Applications Supporting Dual Mode

### Current Applications
1. **foundation** - Donation analysis and AI insights
2. **project-analysis** - Project data visualization and analysis

### Future Applications
- Any Next.js application can be adapted to support dual mode
- Requires minimal code changes (deployment config abstraction)

## Implementation Architecture

### 1. Deployment Configuration Module

Each application includes `lib/deployment-config.ts`:

```typescript
export type DeploymentMode = 'vercel' | 'busibox';

export function getDeploymentMode(): DeploymentMode {
  const mode = process.env.DEPLOYMENT_MODE?.toLowerCase();
  return mode === 'busibox' ? 'busibox' : 'vercel';
}

export function getOpenAIConfig() {
  if (getDeploymentMode() === 'busibox') {
    return {
      baseURL: process.env.LITELLM_BASE_URL,
      apiKey: process.env.LITELLM_API_KEY,
      model: process.env.LITELLM_MODEL || 'gpt-4o',
    };
  }
  return {
    baseURL: 'https://api.openai.com/v1',
    apiKey: process.env.OPENAI_API_KEY,
    model: 'gpt-4o',
  };
}

export function getEmailConfig() {
  if (getDeploymentMode() === 'busibox') {
    // Prefer SMTP, fallback to Resend
    if (process.env.SMTP_HOST) {
      return { provider: 'smtp', /* ... */ };
    }
  }
  return { provider: 'resend', /* ... */ };
}
```

### 2. Environment Variable Structure

**Vercel Deployment (.env.local):**
```bash
DEPLOYMENT_MODE=vercel
OPENAI_API_KEY=sk-...
RESEND_API_KEY=re_...
DATABASE_URL=postgresql://neon.tech/...
```

**Busibox Deployment (Ansible-generated):**
```bash
DEPLOYMENT_MODE=busibox
LITELLM_BASE_URL=http://10.96.200.207:4000/v1
LITELLM_API_KEY=...
SMTP_HOST=smtp.gmail.com
SMTP_USER=...
SMTP_PASS=...
DATABASE_URL=postgresql://10.96.200.203/...
```

### 3. Prisma Schema Configuration

**Support both deployment modes:**
```prisma
generator client {
  provider = "prisma-client-js"
  // Multiple binary targets for different platforms
  binaryTargets = ["native", "rhel-openssl-3.0.x", "debian-openssl-3.0.x"]
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
  // Optional direct URL for connection pooling (Neon, PgBouncer)
  directUrl = env("DIRECT_URL")
}
```

### 4. Application Code Usage

**API Route Example:**
```typescript
import { getOpenAIConfig } from '@/lib/deployment-config';
import { OpenAI } from 'openai';

export async function POST(req: Request) {
  const config = getOpenAIConfig();
  const openai = new OpenAI({
    baseURL: config.baseURL,
    apiKey: config.apiKey,
  });
  
  const response = await openai.chat.completions.create({
    model: config.model,
    messages: [/* ... */],
  });
  
  return Response.json(response);
}
```

**Email Example:**
```typescript
import { getEmailConfig } from '@/lib/deployment-config';
import nodemailer from 'nodemailer';
import { Resend } from 'resend';

export async function sendEmail(to: string, subject: string, html: string) {
  const config = getEmailConfig();
  
  if (config.provider === 'smtp') {
    const transporter = nodemailer.createTransport(config.smtp);
    await transporter.sendMail({ from: config.from, to, subject, html });
  } else {
    const resend = new Resend(config.apiKey);
    await resend.emails.send({ from: config.from, to, subject, html });
  }
}
```

## Busibox Deployment Configuration

### Application Definition (apps.yml)

```yaml
- name: foundation
  github_repo: jazzmind/foundation
  container: apps-lxc
  container_ip: "{{ apps_ip }}"
  port: 3003
  deploy_path: /srv/apps/foundation
  health_endpoint: /api/health
  build_command: "npm run build"
  routes:
    - type: subdomain
      subdomain: "foundation{{ env_subdomain_suffix | default('') }}"
    - type: path
      domain: "{{ full_domain }}"
      path: /foundation
      strip_path: true
  secrets:
    - database_url
    - litellm_api_key
    - allowed_email_domains
    - email_from
    - smtp_host
    - smtp_port
    - smtp_user
    - smtp_password
    - smtp_secure
  optional_secrets:
    - resend_api_key  # Fallback if SMTP fails
  env:
    NODE_ENV: "{{ node_env }}"
    LITELLM_BASE_URL: "http://{{ litellm_ip }}:{{ litellm_port }}/v1"
    NEXT_PUBLIC_APP_URL: "https://{{ full_domain }}/foundation"
    PORT: "3003"
    DEPLOYMENT_MODE: "busibox"  # Critical: enables Busibox mode
```

### Secrets Configuration (vault.yml)

```yaml
secrets:
  foundation:
    database_url: "postgresql://busibox_user:password@10.96.200.203/foundation"
    litellm_api_key: "sk-local-key"
    allowed_email_domains: "user1@example.com,user2@example.com"
    email_from: "foundation@ai.jaycashman.com"
    smtp_host: "smtp.gmail.com"
    smtp_port: "587"
    smtp_user: "notifications@jaycashman.com"
    smtp_password: "app-specific-password"
    smtp_secure: "false"
    # Optional fallback
    resend_api_key: "re_..."
```

## Vercel Deployment Configuration

### Environment Variables (Vercel Dashboard)

```bash
# Deployment Mode
DEPLOYMENT_MODE=vercel

# AI Configuration
OPENAI_API_KEY=sk-...

# Database (Neon)
DATABASE_URL=postgresql://...@neon.tech/foundation
DIRECT_URL=postgresql://...@neon.tech/foundation?sslmode=require

# Email (Resend)
RESEND_API_KEY=re_...
EMAIL_FROM=noreply@foundation.vercel.app

# Authentication
ALLOWED_USERS=user1@example.com,user2@example.com
NEXT_PUBLIC_APP_URL=https://foundation.vercel.app

# Node Environment
NODE_ENV=production
```

### Deployment

```bash
# Install Vercel CLI
npm install -g vercel

# Deploy
cd foundation
vercel

# Deploy to production
vercel --prod
```

## Adding Dual Mode to Existing Application

### Step 1: Create Deployment Config

Create `lib/deployment-config.ts`:

```typescript
export type DeploymentMode = 'vercel' | 'busibox';

export function getDeploymentMode(): DeploymentMode {
  const mode = process.env.DEPLOYMENT_MODE?.toLowerCase();
  return mode === 'busibox' ? 'busibox' : 'vercel';
}

export function isBusibox(): boolean {
  return getDeploymentMode() === 'busibox';
}

export function isVercel(): boolean {
  return getDeploymentMode() === 'vercel';
}

export function getOpenAIConfig() {
  const mode = getDeploymentMode();
  
  if (mode === 'busibox') {
    return {
      baseURL: process.env.LITELLM_BASE_URL || 'http://localhost:4000/v1',
      apiKey: process.env.LITELLM_API_KEY || 'dummy-key',
      model: process.env.LITELLM_MODEL || 'gpt-4o',
    };
  }
  
  return {
    baseURL: 'https://api.openai.com/v1',
    apiKey: process.env.OPENAI_API_KEY,
    model: 'gpt-4o',
  };
}

export function getEmailConfig() {
  const mode = getDeploymentMode();
  
  if (mode === 'busibox') {
    const hasSmtp = process.env.SMTP_HOST && 
                    process.env.SMTP_USER && 
                    process.env.SMTP_PASS;
    
    if (hasSmtp) {
      return {
        provider: 'smtp' as const,
        from: process.env.EMAIL_FROM || 'noreply@localhost',
        smtp: {
          host: process.env.SMTP_HOST!,
          port: parseInt(process.env.SMTP_PORT || '587'),
          secure: process.env.SMTP_SECURE === 'true',
          auth: {
            user: process.env.SMTP_USER!,
            pass: process.env.SMTP_PASS!,
          },
        },
      };
    }
  }
  
  return {
    provider: 'resend' as const,
    from: process.env.EMAIL_FROM || 'noreply@vercel.app',
    apiKey: process.env.RESEND_API_KEY,
  };
}

export function getAppURL(): string {
  if (process.env.NEXT_PUBLIC_APP_URL) {
    return process.env.NEXT_PUBLIC_APP_URL;
  }
  if (process.env.VERCEL_URL) {
    return `https://${process.env.VERCEL_URL}`;
  }
  return 'http://localhost:3000';
}

export function getAllowedUsers(): string[] {
  const allowedUsers = process.env.ALLOWED_USERS || 
                       process.env.ALLOWED_EMAIL_DOMAINS || '';
  return allowedUsers
    .split(',')
    .map(email => email.trim())
    .filter(email => email.length > 0);
}
```

### Step 2: Update Prisma Schema

```prisma
generator client {
  provider = "prisma-client-js"
  binaryTargets = ["native", "rhel-openssl-3.0.x", "debian-openssl-3.0.x"]
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
  directUrl = env("DIRECT_URL")
}
```

### Step 3: Update Environment Files

**env.example:**
```bash
# ============================================================================
# Deployment Mode
# ============================================================================
DEPLOYMENT_MODE=vercel  # or "busibox"

# ============================================================================
# AI Configuration
# ============================================================================
# For Vercel:
OPENAI_API_KEY=sk-...

# For Busibox:
LITELLM_BASE_URL=http://localhost:4000/v1
LITELLM_API_KEY=...
LITELLM_MODEL=gpt-4o

# ============================================================================
# Database
# ============================================================================
DATABASE_URL=postgresql://...
DIRECT_URL=postgresql://...  # Optional

# ============================================================================
# Email
# ============================================================================
EMAIL_FROM=noreply@domain.com

# Resend (Vercel):
RESEND_API_KEY=re_...

# SMTP (Busibox):
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURE=false
SMTP_USER=...
SMTP_PASS=...
```

### Step 4: Update API Routes

Replace direct OpenAI usage:

**Before:**
```typescript
import { OpenAI } from 'openai';

const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});
```

**After:**
```typescript
import { OpenAI } from 'openai';
import { getOpenAIConfig } from '@/lib/deployment-config';

const config = getOpenAIConfig();
const openai = new OpenAI({
  baseURL: config.baseURL,
  apiKey: config.apiKey,
});
```

### Step 5: Add to Busibox

Add application to `provision/ansible/group_vars/apps.yml`:

```yaml
- name: my-app
  github_repo: jazzmind/my-app
  container: apps-lxc
  container_ip: "{{ apps_ip }}"
  port: 3005
  deploy_path: /srv/apps/my-app
  health_endpoint: /api/health
  build_command: "npm run build"
  routes:
    - type: subdomain
      subdomain: "myapp{{ env_subdomain_suffix | default('') }}"
  secrets:
    - database_url
    - litellm_api_key
    - smtp_host
    - smtp_port
    - smtp_user
    - smtp_password
  env:
    NODE_ENV: "{{ node_env }}"
    LITELLM_BASE_URL: "http://{{ litellm_ip }}:{{ litellm_port }}/v1"
    DEPLOYMENT_MODE: "busibox"
```

### Step 6: Deploy

**Busibox:**
```bash
cd provision/ansible
make test  # Test first
make production
```

**Vercel:**
```bash
vercel --prod
```

## Benefits of Dual Mode

### Development Flexibility
- Develop locally with either mode
- Test with production-like setup (Busibox)
- Quick iteration on Vercel

### Cost Optimization
- Use Vercel for public demos (pay per use)
- Use Busibox for internal use (fixed cost)
- Choose based on usage patterns

### Data Privacy
- Keep sensitive data on Busibox (local)
- Use Vercel for non-sensitive applications
- Comply with data residency requirements

### Vendor Independence
- Not locked into Vercel
- Not locked into OpenAI
- Can switch providers easily

### Testing
- Test with local models (Busibox)
- Test with production models (Vercel)
- Compare model performance

## Troubleshooting

### Wrong Mode Detected

**Check:**
```typescript
import { getDeploymentMode, logDeploymentConfig } from '@/lib/deployment-config';

console.log('Deployment mode:', getDeploymentMode());
logDeploymentConfig();
```

**Verify environment variable:**
```bash
echo $DEPLOYMENT_MODE
```

### OpenAI API Errors on Busibox

**Check liteLLM is running:**
```bash
ssh root@10.96.200.207
systemctl status litellm
curl http://localhost:4000/health
```

**Check model availability:**
```bash
curl http://10.96.200.207:4000/v1/models
```

### Email Not Sending on Busibox

**Check SMTP configuration:**
```bash
# In application container
cat /srv/apps/foundation/.env | grep SMTP
```

**Test SMTP connection:**
```typescript
// In API route or script
import { getEmailConfig } from '@/lib/deployment-config';

const config = getEmailConfig();
console.log('Email config:', config);
```

### Database Connection Issues

**Busibox - Check PostgreSQL:**
```bash
ssh root@10.96.200.203
systemctl status postgresql
psql -U busibox_user -d foundation -c "SELECT 1"
```

**Vercel - Check Neon:**
```bash
# Test connection string
psql "postgresql://...@neon.tech/foundation?sslmode=require" -c "SELECT 1"
```

## Best Practices

### 1. Environment Variable Naming
- Use same variable names across modes when possible
- Prefix mode-specific variables clearly (LITELLM_, SMTP_)
- Document all variables in env.example

### 2. Graceful Fallbacks
- Provide fallback values for optional features
- Log which mode/provider is being used
- Handle missing configuration gracefully

### 3. Testing Both Modes
- Test application in both modes before release
- Verify all features work in both modes
- Document mode-specific limitations

### 4. Configuration Validation
- Validate required variables on startup
- Log configuration (without secrets) for debugging
- Fail fast if critical config missing

### 5. Documentation
- Document deployment mode in README
- Provide env.example for both modes
- Include troubleshooting for both modes

## Related Documentation

- [Application Configuration Architecture](./app-configuration-architecture.md)
- [Deployment Procedures](./deployment-procedures.md)
- [Secrets Management](../configuration/secrets-management.md)
- [liteLLM Configuration](../configuration/litellm.md)

## Summary

The dual deployment mode architecture provides:
- **Flexibility** - Deploy to Vercel or Busibox with same code
- **Privacy** - Keep data local on Busibox when needed
- **Cost Control** - Choose deployment based on usage
- **Vendor Independence** - Not locked into any platform
- **Development Speed** - Test locally with production setup

This architecture enables applications to run anywhere while maintaining a single, maintainable codebase.


