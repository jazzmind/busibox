# Bridge Service Role

## Overview

The Bridge service provides multi-channel communication capabilities for Busibox, enabling AI interactions through various messaging platforms and protocols.

**Status**: Under Development - Deployment is optional and non-blocking

## Supported Channels

### Currently Implemented
- **Signal**: Secure messaging via Signal messenger

### Planned
- **Email**: SMTP/IMAP integration
- **WhatsApp**: WhatsApp Business API
- **Webhooks**: Generic webhook endpoints for custom integrations

## Architecture

The bridge service consists of:
1. **Channel Adapters**: Protocol-specific implementations (Signal CLI, Email clients, etc.)
2. **Message Router**: Routes messages to appropriate AI agents
3. **Response Handler**: Formats and delivers AI responses back through the appropriate channel

## Deployment

### Prerequisites
- Docker (for Signal CLI REST API container)
- Python 3.x
- Agent API must be deployed and accessible

### Deploy

```bash
# Deploy bridge service (optional, won't fail if host doesn't exist)
make bridge

# Or with specific inventory
make bridge INV=inventory/staging
```

### Configuration

Key variables in `defaults/main.yml`:
- `bridge_enabled`: Enable/disable the service
- `bridge_dir`: Installation directory
- `signal_bot_phone_number`: Phone number for Signal (from vault)
- `bridge_auth_client_id`: OAuth client ID

### Signal Setup

After deployment, register the Signal number:

```bash
ssh root@bridge-host
/usr/local/bin/register-signal
```

Follow the prompts to complete Signal registration.

## Testing

```bash
# Test bridge service
make test-bridge
```

## Monitoring

Check service status:

```bash
ssh root@bridge-host
/usr/local/bin/bridge-status
```

View logs:

```bash
journalctl -u bridge -f
```

## Development Notes

This service is being generalized from the original `signal-bot` to support multiple communication channels. The architecture is designed to make adding new channels straightforward:

1. Create a new adapter in `srv/bridge/app/adapters/`
2. Register the adapter in the main application
3. Add channel-specific configuration to Ansible defaults

## Security

- Service runs as dedicated `bridge` user
- Environment variables stored in protected `.env` file (mode 0600)
- OAuth2 authentication for API access
- Rate limiting to prevent abuse

## Troubleshooting

### Service won't start
```bash
systemctl status bridge
journalctl -u bridge -n 50
```

### Signal CLI issues
```bash
docker logs signal-cli-rest-api
curl http://localhost:8080/v1/about
```

### Authentication failures
Check that:
- AuthZ service is running
- Client credentials are correct in vault
- Service user ID exists in AuthZ database
