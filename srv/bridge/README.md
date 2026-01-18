# Bridge Service

A multi-channel communication bridge that connects various messaging platforms to the Busibox Agent API for AI-powered interactions.

**Status**: Under Development

## Overview

This service provides a unified interface for AI interactions across multiple communication channels:
- **Signal**: Secure messaging (currently implemented)
- **Email**: SMTP/IMAP integration (planned)
- **WhatsApp**: WhatsApp Business API (planned)
- **Webhooks**: Generic webhook endpoints (planned)

## Architecture

```
External Channel → Channel Adapter → Bridge Service → Agent API
    ↑                                       ↓
    └───────────────────────────────────────┘
```

### Current Implementation (Signal)

```
Signal App → signal-cli-rest-api → Bridge Service → Agent API
    ↑                                   ↓
    └───────────────────────────────────┘
```

## Quick Start

### Prerequisites

1. signal-cli-rest-api running with a registered phone number (for Signal channel)
2. Agent API accessible
3. Service account credentials for Agent API

### Environment Variables

```bash
# Application
APP_NAME=bridge
ENVIRONMENT=production
LOG_LEVEL=INFO

# Signal channel configuration
SIGNAL_CLI_URL=http://localhost:8080
SIGNAL_PHONE_NUMBER=+12025551234

# Agent API configuration
AGENT_API_URL=http://agent-lxc:8000
AUTH_TOKEN_URL=http://authz-lxc:8010/oauth/token
AUTH_CLIENT_ID=bridge-client
AUTH_CLIENT_SECRET=your-client-secret
SERVICE_USER_ID=bridge-service

# Bot behavior (Signal-specific)
ENABLE_WEB_SEARCH=true
ENABLE_DOC_SEARCH=false
DEFAULT_MODEL=auto

# Rate limiting
RATE_LIMIT_MESSAGES=30
RATE_LIMIT_WINDOW=60

# Polling
POLL_INTERVAL=1.0

# Allowed phone numbers (comma-separated, empty = all)
ALLOWED_PHONE_NUMBERS=
```

### Running Locally

```bash
# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Set environment variables
export SIGNAL_CLI_URL=http://localhost:8080
export AGENT_API_URL=http://localhost:8000
# ... other vars ...

# Run the service
python -m app.main
```

### Deployment

The service is deployed via Ansible as an optional, non-blocking component:

```bash
# Deploy bridge service (won't fail if host doesn't exist)
make bridge

# Or with specific inventory
make bridge INV=inventory/staging
```

## Signal Setup

### 1. Register Phone Number

After deployment, SSH to the bridge container and run:

```bash
/usr/local/bin/register-signal register +12025551234
```

Follow the SMS verification prompts.

### 2. Verify Registration

```bash
/usr/local/bin/register-signal status
```

### 3. Test Integration

```bash
# From Ansible directory
make test-bridge
```

## Adding New Channels

To add a new communication channel:

1. **Create Adapter**: Add a new adapter in `app/adapters/` (e.g., `email_adapter.py`, `whatsapp_adapter.py`)
2. **Implement Interface**: Follow the pattern from `signal_client.py`:
   - Poll for incoming messages
   - Format messages for Agent API
   - Send responses back through the channel
3. **Configuration**: Add channel-specific env vars to Ansible templates
4. **Register**: Update `app/main.py` to initialize and run the new adapter

## API Integration

The bridge service authenticates with the Agent API using OAuth2 client credentials:

```python
# Get access token
POST http://authz-lxc:8010/oauth/token
{
  "grant_type": "client_credentials",
  "client_id": "bridge-client",
  "client_secret": "..."
}

# Call Agent API
POST http://agent-lxc:8000/agents/{agent_id}/chat
Authorization: Bearer {access_token}
{
  "message": "User message",
  "user_id": "signal:+12025551234",
  "conversation_id": "optional-existing-id"
}
```

## Monitoring

### Check Service Status

```bash
systemctl status bridge
journalctl -u bridge -f
```

### Check Signal CLI

```bash
docker logs signal-cli-rest-api
curl http://localhost:8080/v1/about
```

### Status Script

```bash
/usr/local/bin/bridge-status
```

## Security

- Runs as dedicated `bridge` user
- Environment variables protected (mode 0600)
- OAuth2 authentication for API access
- Rate limiting per user
- Optional phone number whitelist

## Troubleshooting

### Service won't start

```bash
systemctl status bridge
journalctl -u bridge -n 50
```

### Signal messages not received

1. Check signal-cli-rest-api: `docker logs signal-cli-rest-api`
2. Verify registration: `/usr/local/bin/register-signal status`
3. Check bridge logs: `journalctl -u bridge -f`

### Authentication failures

1. Verify AuthZ service is running
2. Check client credentials in vault
3. Verify service user exists in AuthZ database

### Rate limiting

Default: 30 messages per 60 seconds per user. Adjust in Ansible defaults:
- `signal_bot_rate_limit_messages`
- `signal_bot_rate_limit_window`

## Development

### Local Testing

```bash
# Start signal-cli-rest-api locally
docker run -d --name signal-cli-rest-api \
  -p 8080:8080 \
  -v ~/signal-cli-data:/home/.local/share/signal-cli \
  bbernhard/signal-cli-rest-api:latest

# Run bridge service
python -m app.main
```

### Code Structure

```
app/
├── __init__.py
├── main.py              # Entry point, polling loop
├── config.py            # Configuration management
├── signal_client.py     # Signal channel adapter
├── agent_client.py      # Agent API client
└── adapters/            # Future: email, whatsapp, webhook adapters
```

## Future Enhancements

- [ ] Email channel (SMTP/IMAP)
- [ ] WhatsApp Business API integration
- [ ] Generic webhook endpoints
- [ ] Message queue for async processing
- [ ] Conversation persistence
- [ ] Multi-agent routing based on channel/user
- [ ] Rich media support (images, files)
- [ ] Typing indicators
- [ ] Read receipts
