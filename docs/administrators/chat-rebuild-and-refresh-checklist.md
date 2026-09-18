---
title: "Chat Rebuild & Post-Deploy Checklist"
category: "administrator"
order: 12
description: "Runbook for rebuilding the chat frontend in place and re-applying settings that deploys overwrite"
published: true
---

# Chat Rebuild & Post-Deploy Checklist

Two situations this runbook covers:

- **A. Rebuild the chat frontend in place** (change branding/env without a full deploy).
- **B. After any deploy or system refresh** — re-apply the settings that deploys silently overwrite.

Container map (production / staging): core-apps **201 / 301**, agent **202 / 302**,
postgres **203 / 303**, milvus+search **204 / 304**, data+embedding **206 / 306**,
litellm **207 / 307**, vllm **208 / 308**, authz **210 / 310**. Confirm with `pct list`
— docs drift from reality.

> Golden rule learned the hard way: **every manual edit below is erased by the next
> deploy of that service.** The durable home for each setting is listed under "Make it
> permanent." Do the manual step to unblock, then make it permanent.

---

## A. Rebuild the chat frontend in place

The chat app is a Next.js **standalone** build. `NEXT_PUBLIC_*` variables are compiled
into the bundle — editing `.env` and restarting does **nothing**; you must rebuild, and
you must copy static assets into the standalone tree afterward.

```bash
pct enter 201                                  # 301 for staging
cd /srv/apps/busibox-frontend
git log -1 --oneline                           # confirm the commit you expect
# git fetch && git pull                        # only if you need newer frontend code

nano /srv/apps/busibox-chat/.env               # edit vars — QUOTE every value with spaces
```

Branding block (all values quoted; JSON line in single quotes):

```
NEXT_PUBLIC_CHAT_BRAND="marine"
NEXT_PUBLIC_CHAT_PRODUCT_NAME="Marine AI"
NEXT_PUBLIC_CHAT_TAGLINE="Jay Cashman, Inc."
NEXT_PUBLIC_CHAT_SIDEBAR_TITLE="MARINE AI"
NEXT_PUBLIC_CHAT_COMPOSER_PLACEHOLDER="Ask about policies, projects, equipment, or anything else"
NEXT_PUBLIC_CHAT_DISCLAIMER="AI can make mistakes. Verify important details against source documents."
NEXT_PUBLIC_CHAT_EMPTY_HEADING="What can I help you with today?"
NEXT_PUBLIC_CHAT_SUGGESTED_PROMPTS='[{"description":"...","prompt":"..."}]'
```

A prompt may contain one `{{placeholder}}` — the new-chat screen then shows an
inline text box in its place and sends the completed sentence, e.g.
`{"description":"Deep research on any subject.","prompt":"Deep dive into the topic {{topic}}"}`.

Build, sync assets, restart:

```bash
cd /srv/apps/busibox-frontend
set -a; source /srv/apps/busibox-chat/.env; set +a      # errors like "AI: command not found" = unquoted value, fix and redo
NODE_ENV=production pnpm --filter @busibox/chat run build

cd /srv/apps/busibox-chat
APP=.next/standalone/apps/chat
rm -rf $APP/.next/static && cp -r .next/static $APP/.next/static
rm -rf $APP/public       && cp -r public       $APP/public
systemctl restart busibox-chat
systemctl status busibox-chat --no-pager | head -3
```

Verify:

- `grep -rl "Jay Cashman" .next/standalone/apps/chat/.next/server | head -1` → a file = branding compiled in.
- Browser: **incognito window**, log in, open `/chat` (not the portal's built-in chat). JS 404s in the console = static copy step was skipped.

**Make it permanent:** put the same vars in `provision/ansible/group_vars/all/apps.yml`
under `busibox-chat → env:`, commit, deploy `busibox-chat` via the CLI. The deploy
builds with them baked in and writes them into the systemd unit.

**Never build from `/srv/apps/busibox-chat` directly** (the symlink) — it produces
duplicate-React `useContext` errors. Always `--filter` from the monorepo root.

---

## B. After a deploy or system refresh

Run the block for each service that was deployed/restarted. Each item: *what gets
wiped → how to check → how to restore → permanent fix.*

### B1. agent-api (202 / 302) — `.env` is regenerated

```bash
grep -E "SEMANTIC_ROUTER|LLM_BACKEND|CLOUD_ROUTED" /srv/agent/.env
```
If missing, re-add and restart:
```bash
cat >> /srv/agent/.env << 'EOF'
SEMANTIC_ROUTER_ENABLED=true
SEMANTIC_ROUTER_MODE=live
LLM_BACKEND=cloud
EOF
systemctl restart agent-api
```
`LLM_BACKEND=cloud` is a stopgap that stops vLLM-only params reaching Bedrock; remove it
once the `cloud_routed_aliases` code fix is deployed.
**Permanent:** add these to the Ansible vault + `roles/agent_api/templates/agent-api.env.j2`.

Also expected after every agent deploy: the PVT `test_health_live` failure (health check
races service startup). Code still deployed if "Copy agent service source code" was
`changed`. Confirm: `systemctl status agent-api` + `curl localhost:8000/health`.

### B2. LiteLLM (207 / 307) — `config.yaml` is regenerated

```bash
curl -s http://<litellm-ip>:4000/v1/models -H "Authorization: Bearer <master_key>" | grep -o '"id":"[^"]*"'
```
Expect: `fast chat agent tool_calling research parsing cleanup vision frontier fallback`.
If aliases are missing, the deploy ran without `model_config.yml` and rendered an empty
`model_list`. Restore by re-adding the vLLM aliases to `/etc/litellm/config.yaml`
(`openai/<served-model-name>`, `api_base: http://<vllm-ip>:800X/v1`, `api_key: "none"`),
then:
```bash
/opt/litellm/venv/bin/python3 -c "import yaml; yaml.safe_load(open('/etc/litellm/config.yaml'))" && systemctl restart litellm
```
**Permanent:** copy `model_config.yml` from the Proxmox host
(`/root/busibox/provision/ansible/group_vars/all/`) into the same path in the checkout
that runs deploys; put `chat: "claude-sonnet-4-6"` (or current choice) and
`parsing/cleanup: "agent"` in `model_registry.yml`; keep `drop_params: true` in the
litellm template.

Notes: LiteLLM binds to the container IP, not localhost — curl the IP. `/v1/models`
needs the master key. Startup can take ~60s (Prisma migrations).

### B3. Web search providers — `tool_configs` table (agent DB on 203 / 303)

```bash
pct exec 203 -- su - postgres -c "psql -d agent -c \"SELECT scope, config::jsonb->'providers'->'tavily'->>'enabled', LEFT(config::jsonb->'providers'->'tavily'->>'api_key',8) FROM tool_configs WHERE tool_name='web_search';\""
```
Expect a `system` row with tavily `true` and a key prefix. Missing → configure via the
admin UI's Web Search settings (creates the row correctly). Symptom when broken: log shows
`Running 1 search tasks: ['duckduckgo']` and answers say "I can't browse the internet."

### B4. Frontend deploys (core-apps) — nginx role runs

Before any frontend deploy: `ls -la /root/busibox/ssl/` on the Proxmox host — real certs,
not `CHANGE_ME`. After: `nginx -t` on the proxy container; hard-refresh browsers.
Portal `.env` allowlist: `grep ALLOWED_EMAIL /srv/apps/busibox-portal/.env` — re-add
domains if reset. Chat branding: see section A / `apps.yml`.

### B5. vLLM (208 / 308)

```bash
systemctl status vllm-8000 vllm-8001 --no-pager | grep Active
curl -s localhost:8000/v1/models | head -c 200
```
`vllm-8002`/`8003` are **masked on purpose** (GPU collision + stale flags). Do not unmask.
Never pip-install into `/opt/vllm/venv` outside a maintenance window — a nightly vLLM
pulled a torch/torchaudio CUDA mismatch and took all local inference down.

### B6. Staging retrieval stack (304 / 306)

```bash
pct exec 304 -- systemctl status milvus search-api --no-pager | grep Active
pct exec 304 -- curl -s http://10.96.201.206:8005/health      # embedding-api reachable cross-container
```
`document_search` 500s in staging chat = one of these is down.

### B7. Smoke test (both environments)

In the chat UI: `hi` (real reply persists) · `What is PREC?` (glossary → Patriot
Renewable Energy Capital) · `is tomorrow a company holiday?` (neutral canned ack, then
grounded answer) · `what's the latest with spacex?` (Tavily results with citations).
Then on the agent container: `journalctl -u agent-api -n 50 --no-pager | grep -iE "error|400|500"` should be quiet.

---

## Quick reference: where each setting *should* live

| Setting | Manual (wiped by deploy) | Permanent |
|---|---|---|
| Router flags, `LLM_BACKEND` | `/srv/agent/.env` | vault + `agent-api.env.j2` |
| Model aliases (`fast`, `chat`, …) | `/etc/litellm/config.yaml` | `model_registry.yml` + `model_config.yml` in deploy checkout |
| Web search keys | `tool_configs` DB row | admin UI (DB is the durable store) |
| Chat branding | `/srv/apps/busibox-chat/.env` + rebuild | `apps.yml` chat `env:` block |
| Email domain allowlist | portal `.env` | vault `allowed_email_domains` |
| Staging vault password | — | `/root/.busibox-vault-pass-<profile>` on Proxmox host |
