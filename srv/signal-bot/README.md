# Signal Bot Service

A Signal messenger bot that connects to the Busibox Agent API for AI-powered chat.

## Overview

This service:
- Polls for incoming Signal messages via signal-cli-rest-api
- Forwards messages to the Agent API
- Returns AI responses to the Signal user
- Maintains conversation context per user

## Architecture

```
Signal App → signal-cli-rest-api → Signal Bot → Agent API
    ↑                                   ↓
    └───────────────────────────────────┘
```

## Quick Start

### Prerequisites

1. signal-cli-rest-api running with a registered phone number
2. Agent API accessible
3. Service account credentials for Agent API

### Environment Variables

```bash
# Signal configuration
SIGNAL_CLI_URL=http://localhost:8080
SIGNAL_PHONE_NUMBER=+12025551234

# Agent API configuration
AGENT_API_URL=http://agent-lxc:8000
AUTH_TOKEN_URL=http://authz-lxc:8010/oauth/token
AUTH_CLIENT_ID=signal-bot-client
AUTH_CLIENT_SECRET=your-client-secret
SERVICE_USER_ID=signal-bot-service

# Bot behavior
ENABLE_WEB_SEARCH=true
ENABLE_DOC_SEARCH=false
DEFAULT_MODEL=auto

# Rate limiting
RATE_LIMIT_MESSAGES=30
RATE_LIMIT_WINDOW=60

# Logging
LOG_LEVEL=INFO
DEBUG=false
```

### Running Locally

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables (see above)
export SIGNAL_PHONE_NUMBER="+12025551234"
# ... other variables

# Run the bot
python -m app.main
```

### Running with Docker

```bash
# Build image
docker build -t signal-bot .

# Run container
docker run -d \
  --name signal-bot \
  --env-file .env \
  --network host \
  signal-bot
```

## Usage

### Commands

- `/help` - Show help message
- `/new` - Start a new conversation

### Examples

Send any message to the bot's Signal number:

```
User: What's the weather like today?
Bot: I don't have real-time weather data, but I can help you...

User: Search for the latest AI news
Bot: [Performs web search and returns results]
```

## Configuration

### Rate Limiting

Default: 30 messages per 60 seconds per user.

Adjust via:
- `RATE_LIMIT_MESSAGES` - Max messages in window
- `RATE_LIMIT_WINDOW` - Window duration in seconds

### Allowed Users

Restrict to specific phone numbers:
```bash
ALLOWED_PHONE_NUMBERS="+12025551234,+12025555678"
```

Empty = allow all users.

### Model Selection

- `auto` - Automatic model selection based on query
- `chat` - Standard chat model
- `research` - Research-oriented model
- `frontier` - Best available model

## Development

### Project Structure

```
srv/signal-bot/
├── app/
│   ├── __init__.py
│   ├── main.py           # Entry point and bot logic
│   ├── config.py         # Settings from environment
│   ├── signal_client.py  # signal-cli-rest-api client
│   └── agent_client.py   # Agent API client
├── requirements.txt
├── Dockerfile
└── README.md
```

### Testing

```bash
# Run tests (when available)
pytest tests/
```

### Debugging

Set `DEBUG=true` for verbose logging and streaming mode.

## Deployment

### With Ansible

```bash
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags signal-bot
```

### Manual Deployment

1. Deploy signal-cli-rest-api container
2. Register Signal phone number
3. Deploy signal-bot service
4. Configure systemd service

## Troubleshooting

### Bot Not Responding

1. Check signal-cli-rest-api logs
2. Verify Signal registration
3. Check Agent API connectivity
4. Review signal-bot logs

### Authentication Errors

1. Verify client credentials
2. Check auth token endpoint
3. Ensure service user exists

### Rate Limiting Issues

Adjust `RATE_LIMIT_MESSAGES` and `RATE_LIMIT_WINDOW` as needed.

## Security

- Bot runs inside trusted network
- All API calls authenticated
- Rate limiting prevents abuse
- Optional phone number allowlist

## Related Documentation

- [Signal Bot Setup Guide](../../docs/guides/signal-bot-setup.md)
- [Agent API Documentation](../../openapi/agent-api.yaml)
- [Tailscale Role](../../provision/ansible/roles/tailscale/README.md)
