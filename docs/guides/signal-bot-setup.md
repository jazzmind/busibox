---
created: 2026-01-13
updated: 2026-01-13
status: draft
category: guides
---

# Signal Bot Setup Guide

This guide explains how to set up the Signal messenger bot for accessing AI Portal chat from your mobile device.

## Overview

The Signal bot allows you to:
- Send messages to AI Portal from Signal messenger
- Receive AI responses directly in Signal
- Access your conversations on the go via secure VPN

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Signal App    │────▶│  signal-cli     │────▶│  Signal Bot     │
│   (Mobile)      │◀────│  REST API       │◀────│  Service        │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        │
                                                        ▼
                                                ┌─────────────────┐
                                                │   Agent API     │
                                                │   (agent-lxc)   │
                                                └─────────────────┘
```

## Prerequisites

1. **Tailscale VPN** - For secure remote access
2. **VoIP Phone Number** - Dedicated number for the bot
3. **Agent API Access** - Service account credentials

## Step 1: Set Up Tailscale

### On Busibox Server

Deploy Tailscale to the proxy container:

```bash
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags tailscale --limit proxy-lxc
```

### On Your Mobile Device

1. Install Tailscale from App Store / Play Store
2. Sign in with the same Tailscale account
3. Enable the VPN connection

### Verify Connection

From your phone, you should be able to ping the Tailscale IP of your proxy container.

## Step 2: Obtain a VoIP Phone Number

You need a phone number that can receive SMS for Signal registration.

### Option A: Twilio

1. Sign up at https://www.twilio.com/
2. Purchase a phone number with SMS capability
3. Configure SMS webhook to receive verification codes

### Option B: Google Voice

1. Sign up at https://voice.google.com/
2. Select a phone number
3. Use this number for Signal registration

### Option C: TextNow or Similar

1. Sign up for a free VoIP service
2. Get a phone number with SMS capability

**Note**: Signal may block some VoIP numbers. Twilio numbers generally work well.

## Step 3: Register Signal Number

The Signal bot uses `signal-cli-rest-api` for communication. Registration is a one-time process.

### Start signal-cli-rest-api

```bash
# On agent-lxc container
docker run -d --name signal-cli \
  -p 8080:8080 \
  -v /srv/signal-cli:/home/.local/share/signal-cli \
  bbernhard/signal-cli-rest-api
```

### Register Phone Number

```bash
# Register (replace with your phone number in E.164 format)
curl -X POST "http://localhost:8080/v1/register/+12025551234" \
  -H "Content-Type: application/json" \
  -d '{"use_voice": false}'

# Complete CAPTCHA if required
# Visit the URL provided in the response

# Verify with SMS code
curl -X POST "http://localhost:8080/v1/register/+12025551234/verify/123456"
```

### Verify Registration

```bash
curl "http://localhost:8080/v1/about"
```

## Step 4: Configure Vault Secrets

Add Signal bot secrets to your vault:

```yaml
secrets:
  signal_bot:
    phone_number: "+12025551234"
    service_user_id: "signal-bot-service"
    service_user_email: "signal-bot@internal.busibox.local"
```

## Step 5: Deploy Signal Bot Service

```bash
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags signal-bot
```

## Step 6: Test the Integration

### Send a Test Message

From your Signal app, send a message to the bot's phone number:

```
Hello, can you help me?
```

### Expected Response

The bot should respond with an AI-generated reply within a few seconds.

## Troubleshooting

### Bot Not Responding

1. Check signal-cli-rest-api logs:
   ```bash
   docker logs signal-cli
   ```

2. Check signal-bot service logs:
   ```bash
   journalctl -u signal-bot -f
   ```

3. Verify Agent API connectivity:
   ```bash
   curl -X GET "http://agent-lxc:8000/health"
   ```

### Signal Registration Failed

- Try using voice verification instead of SMS
- Use a different phone number (some VoIP numbers are blocked)
- Complete the CAPTCHA challenge

### Messages Not Delivered

- Ensure Tailscale VPN is connected
- Check firewall rules
- Verify signal-cli-rest-api is running

## Security Considerations

1. **Phone Number Privacy**: The bot's phone number is visible to anyone who messages it
2. **User Mapping**: Consider mapping Signal phone numbers to Busibox users
3. **Rate Limiting**: The bot includes rate limiting to prevent abuse
4. **VPN Required**: All traffic goes through Tailscale VPN

## Advanced Configuration

### Enable Web Search

The bot can use web search for queries. Enable in the agent selection:

```python
# In signal_bot config
enable_web_search = True
```

### Custom Agent Selection

You can configure which agents the bot uses:

```python
# In signal_bot config
selected_agents = ["chat", "web_search"]
```

## Related Documentation

- [Tailscale Role](../../provision/ansible/roles/tailscale/README.md)
- [Agent API Documentation](../reference/agent-api.md)
- [Authentication](../architecture/authentication.md)
