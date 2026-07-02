---
title: Email Integration for Custom Apps
category: developer
order: 21
description: How a custom app sends and receives email (incl. Outlook / Microsoft 365) through the Bridge API, with credentials stored in the Config API
published: true
---

# Email Integration for Custom Apps

**Created**: 2026-05-20
**Status**: Active
**Category**: Developer Guide

---

This guide is for developers building a custom app on top of busibox that needs to read or send email — for example, connecting to a user's Microsoft Outlook / Office 365 mailbox. It explains which services to call, how credentials are stored, and the concrete client helpers in `@jazzmind/busibox-app`.

> **Bottom line:** Busibox's email integration is **generic IMAP/SMTP** — there is no native Microsoft Graph / OAuth2 connector. To connect to Outlook you need an **Outlook app-specific password**, not an OAuth access token. This caveat shapes everything below.

## How the pieces fit

```
┌──────────────────┐
│  Custom Next.js  │
│       app        │
└─────┬────────────┘
      │ 1. Save email creds (encrypted at rest)
      ▼
┌──────────────────┐                ┌──────────────────┐
│   Config API     │ ◄──reads─────  │   Bridge API     │
│ (encrypted store)│                │  (send/receive)  │
└──────────────────┘                └──────┬───────────┘
                                           │ 2. SMTP send
                                           │ 3. IMAP poll (every ~30s)
                                           ▼
                                       Mail provider
                                       (Outlook / O365 / Gmail / …)
```

- **Config API** holds the credentials, encrypted at rest with `CONFIG_ENCRYPTION_KEY`.
- **Bridge API** does the actual sending, and runs a background poll over IMAP for inbound mail. It fetches credentials from Config API at runtime using the caller's JWT.
- **`@jazzmind/busibox-app`** (npm) ships typed helpers for both. There is **no high-level `BridgeClient` class** — it's a small set of functions.

## Bridge API — relevant endpoints

Source: [`srv/bridge/app/api.py`](../../../srv/bridge/app/api.py)

| Route                                     | Line | Purpose                                                |
| ----------------------------------------- | ---- | ------------------------------------------------------ |
| `POST /api/v1/email/send`                 | 377  | Generic send                                           |
| `POST /api/v1/email/send-magic-link`      | 341  | Magic-link auth flow                                   |
| `POST /api/v1/email/send-magic-link-simple` | 359 | Simple magic link                                      |
| `POST /api/v1/email/send-welcome`         | 394  | Welcome template                                       |
| `POST /api/v1/email/test`                 | 439  | Validate config (sends a test message)                 |
| `POST /api/v1/notify`                     | 458  | Multi-channel notification (email first in fallback)   |

**Authentication.** Bridge trusts requests that arrive on the internal Docker network. For *dynamic* credential lookup (the normal case for a custom app), pass the caller's session JWT in `Authorization: Bearer <token>` — Bridge will use that to fetch SMTP/IMAP settings from the Config API on your behalf.

**Inbound mail.** Bridge runs an `EmailInboundBot` ([`srv/bridge/app/main.py:1008-1202`](../../../srv/bridge/app/main.py)) that polls the configured IMAP mailbox every ~30 seconds (configurable). Each new message goes through a per-sender confirmation flow: the first email from an unknown address gets a 6-digit code reply; once the sender responds with the code, their session lasts about an hour. After confirmation, messages are forwarded to the Agent API and the agent's reply is sent back via SMTP.

A custom app does **not** poll IMAP itself. If you need custom hooks on inbound mail (storing it in your DB, triggering app-specific workflows), you have two options: have the agent call your app, or extend Bridge with a webhook callback.

**No Outlook-specific connector.** Outlook is treated like any other IMAP/SMTP provider. There is no MSAL, no OAuth2 device flow, no Graph API integration.

## Config API — credential storage

Source: [`srv/config/src/routes/`](../../../srv/config/src/routes/)

| Route                                      | File:Line                                                                     | Use                            |
| ------------------------------------------ | ----------------------------------------------------------------------------- | ------------------------------ |
| `GET /config/app/{app_id}/{key}`           | [`app_scoped.py:33`](../../../srv/config/src/routes/app_scoped.py)            | Masked read                    |
| `GET /config/app/{app_id}/{key}/raw`       | [`app_scoped.py:50`](../../../srv/config/src/routes/app_scoped.py)            | Decrypted read (runtime)       |
| `PUT /admin/config/{key}`                  | [`admin.py:213`](../../../srv/config/src/routes/admin.py)                     | Save one                       |
| `POST /admin/config/bulk`                  | [`admin.py:197`](../../../srv/config/src/routes/admin.py)                     | Save many                      |

**Access tiers** ([`srv/config/src/auth.py:64-182`](../../../srv/config/src/auth.py)):

| Tier            | Required auth                                                                       | Typical use                          |
| --------------- | ----------------------------------------------------------------------------------- | ------------------------------------ |
| `public`        | None                                                                                | Branding, feature flags              |
| `authenticated` | Any valid JWT                                                                       | App registry, non-secret settings    |
| `app`           | JWT carrying the matching `app_id` role/scope (see `require_app_access()`, line 141)| Per-app secrets — **this is what email creds use** |
| `admin`         | Admin role                                                                          | Full CRUD on all config              |

**Encryption.** Entries written with `encrypted: true` are AES-256-GCM at rest. The data key is derived from `CONFIG_ENCRYPTION_KEY` ([`srv/config/src/services/encryption.py`](../../../srv/config/src/services/encryption.py)). List and masked-read endpoints return `********`; only the explicit `/raw` GET decrypts.

> **`CONFIG_ENCRYPTION_KEY` is not safely rotatable** — same constraint as `LITELLM_SALT_KEY`. Rotating it makes existing encrypted values unrecoverable, with no migration path. Plan for a long lifespan, and back up the key.

## The `@jazzmind/busibox-app` package

Lives in the sibling repo: `busibox-frontend/packages/app/src/`.

**Email helpers** (`lib/bridge/email.ts`, lines 60-152):

- `sendEmail(to, subject, html, text, sessionJwt)` — calls Bridge `/api/v1/email/send`; passes the JWT so Bridge can look up SMTP config dynamically
- `sendMagicLinkEmail(...)`
- `sendTestEmail(...)`

**Config client** (`lib/config/client.ts`):

- `getConfigApiToken(userId, sessionJwt)` — exchange a session JWT for a token with `aud=config-api`
- `getAppConfig(token, appId)` — list keys (values masked)
- `getAppConfigRaw(token, appId, key)` — decrypted read of a single value
- `setConfig(token, key, data)` — write one (admin)
- `bulkSetConfigs(token, payload)` — write many (admin)

**Auth helpers** (package root): `SessionProvider`, `useSession`, `useAuthz`.

There is intentionally **no `BridgeClient` class** — the package keeps the helpers flat and lets you assemble them.

## Recipe: connecting an Outlook mailbox

### Step 1 — save the credentials once

This is typically done in an admin UI screen or a one-time setup script.

```ts
import { getConfigApiToken, setConfig } from '@jazzmind/busibox-app/lib/config';

const token = await getConfigApiToken(userId, sessionJwt);
const common = {
  tier: 'app',
  app_id: 'my-email-app',
  category: 'smtp',
  encrypted: true,
};

await setConfig(token, 'IMAP_HOST',     { ...common, value: 'outlook.office365.com' });
await setConfig(token, 'IMAP_PORT',     { ...common, value: '993', encrypted: false });
await setConfig(token, 'IMAP_USER',     { ...common, value: 'user@contoso.com' });
await setConfig(token, 'IMAP_PASSWORD', { ...common, value: '<outlook-app-password>' });

await setConfig(token, 'SMTP_HOST',     { ...common, value: 'smtp.office365.com' });
await setConfig(token, 'SMTP_PORT',     { ...common, value: '587', encrypted: false });
await setConfig(token, 'SMTP_USER',     { ...common, value: 'user@contoso.com' });
await setConfig(token, 'SMTP_PASSWORD', { ...common, value: '<outlook-app-password>' });
await setConfig(token, 'SMTP_SECURE',   { ...common, value: 'true', encrypted: false });
```

> Generate the Outlook app-specific password at: **Microsoft account → Security → App passwords**. It bypasses MFA for IMAP/SMTP clients, and revoking simply requires generating a new one and updating Config API.

### Step 2 — receive (nothing to do)

Bridge's `EmailInboundBot` picks up the credentials on its next poll cycle. Once `EMAIL_INBOUND_ENABLED=true` is set and the values above exist, inbound mail flows through the confirmation handshake into the Agent API automatically. The app doesn't poll.

### Step 3 — send from app code

```ts
import { sendEmail } from '@jazzmind/busibox-app/lib/bridge/email';

await sendEmail(
  'recipient@example.com',
  'Subject line',
  '<h1>HTML body</h1>',
  'Plain-text fallback',
  sessionJwt,    // critical — Bridge uses this to fetch SMTP creds
);
```

What Bridge does under the hood:

1. Looks for the JWT. If present, calls Config API for `category=smtp` keys scoped to the caller's app.
2. Falls back to environment-variable settings if Config API is unreachable or the JWT is missing.
3. Routes to SMTP (if configured) or to Resend (if `RESEND_API_KEY` is set).
4. Returns success / failure to the caller.

## Gotchas

1. **No OAuth / MSAL.** App-specific passwords only. These are the same credentials you'd give to a desktop IMAP client.
2. **Outlook rate limits.** Microsoft throttles outbound SMTP at roughly 4 messages/sec with a burst of 10. For higher volume, switch to a transactional provider — Bridge already supports Resend, and SendGrid would be a small add-on.
3. **Inbound attachments aren't handled yet.** Inbound emails are forwarded to the agent as plain text. If you need attachments piped through, that's a Bridge enhancement.
4. **Confirmation flow on first contact.** The first email from a new address gets a 6-digit code reply and must respond with it before any further messages reach the agent. Sessions last about an hour. Test users will hit this, so document it for them.
5. **Per-app scoping is enforced.** Always store credentials with `app_id: 'your-app-id'` and `tier: 'app'`. Other apps on the same busibox cannot read them; only admins and your app's JWT can.
6. **`CONFIG_ENCRYPTION_KEY` is non-rotatable** — see the warning above. Treat it like a long-lived root key.

## Related references

- Bridge API source: [`srv/bridge/app/api.py`](../../../srv/bridge/app/api.py)
- Bridge email client: [`srv/bridge/app/email_client.py`](../../../srv/bridge/app/email_client.py)
- Bridge inbound poller: [`srv/bridge/app/main.py`](../../../srv/bridge/app/main.py) (lines 1008-1202)
- Config API auth & tiers: [`srv/config/src/auth.py`](../../../srv/config/src/auth.py) (lines 64-182)
- Config API encryption: [`srv/config/src/services/encryption.py`](../../../srv/config/src/services/encryption.py)
- Bridge integrations admin guide: [`docs/administrators/10-bridge-api-integrations.md`](../../administrators/10-bridge-api-integrations.md)
- Custom (non-Next.js) services: [`custom-service-development.md`](./custom-service-development.md)
