# Signal Bot Ansible Role

Deploys the Signal messenger bot and signal-cli-rest-api for AI Portal integration.

## Overview

This role installs:

1. **signal-cli-rest-api** - Docker container for Signal protocol communication
2. **signal-bot** - Python service that bridges Signal with the Agent API

## Prerequisites

- Docker installed and running
- Agent API accessible
- OAuth credentials configured in vault
- Phone number for Signal registration

## Configuration

### Vault Secrets

Add to `roles/secrets/vars/vault.yml`:

```yaml
secrets:
  signal_bot:
    phone_number: "+12025551234"
    service_user_id: "signal-bot-service"
    service_user_email: "signal-bot@internal.busibox.local"
```

### Role Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `signal_cli_port` | `8080` | Port for signal-cli-rest-api |
| `signal_bot_phone_number` | vault | Phone number for Signal |
| `signal_bot_enable_web_search` | `true` | Enable web search |
| `signal_bot_enable_doc_search` | `false` | Enable document search |
| `signal_bot_rate_limit_messages` | `30` | Max messages per window |
| `signal_bot_rate_limit_window` | `60` | Rate limit window (seconds) |
| `signal_bot_allowed_numbers` | `[]` | Allowed phone numbers (empty = all) |

## Deployment

### Deploy to agent container

```bash
cd provision/ansible
make signal-bot INV=inventory/production
```

### Manual deployment

```bash
ansible-playbook -i inventory/production/hosts.yml site.yml --tags signal-bot --limit agent-lxc
```

## Phone Number Registration

After deployment, register the phone number:

```bash
# On the agent container
ssh root@agent-lxc

# Start registration
register-signal register +12025551234

# If CAPTCHA required, complete it and get the signalcaptcha:// URL
register-signal captcha +12025551234 "signalcaptcha://..."

# Verify with SMS code
register-signal verify +12025551234 123456

# Check status
register-signal status
```

## Operations

### Check Status

```bash
signal-bot-status
```

### View Logs

```bash
journalctl -u signal-bot -f
```

### Restart Service

```bash
systemctl restart signal-bot
```

### Restart signal-cli

```bash
docker restart signal-cli-rest-api
```

## Troubleshooting

### Signal Registration Failed

1. Try voice verification instead of SMS
2. Use a different phone number (some VoIP numbers blocked)
3. Complete CAPTCHA challenge
4. Check signal-cli logs: `docker logs signal-cli-rest-api`

### Bot Not Responding

1. Check service status: `systemctl status signal-bot`
2. View logs: `journalctl -u signal-bot -n 100`
3. Test Agent API: `curl http://agent-lxc:8000/health`
4. Test signal-cli: `curl http://localhost:8080/v1/about`

### Authentication Errors

1. Verify OAuth credentials in vault
2. Check AuthZ service: `curl http://authz-lxc:8010/health`
3. Ensure service user exists in AuthZ

## Files

| Path | Description |
|------|-------------|
| `/srv/signal-bot/` | Bot application directory |
| `/srv/signal-bot/.env` | Environment configuration |
| `/srv/signal-cli/` | signal-cli data (Signal credentials) |
| `/etc/systemd/system/signal-bot.service` | Systemd service |
| `/usr/local/bin/register-signal` | Registration helper script |
| `/usr/local/bin/signal-bot-status` | Status check script |

## Security

- Signal credentials stored in `/srv/signal-cli/` (protected)
- Environment file restricted to service user
- Rate limiting prevents abuse
- Optional phone number allowlist
