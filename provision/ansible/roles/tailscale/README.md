# Tailscale Role

Installs and configures [Tailscale](https://tailscale.com/) mesh VPN for secure remote access to Busibox infrastructure.

## Overview

Tailscale provides a zero-config VPN that creates a secure mesh network between your devices. This allows you to:

- Access Busibox services from your mobile device
- Connect securely without port forwarding
- Use end-to-end encryption (WireGuard)

## Prerequisites

1. Create a Tailscale account at https://tailscale.com/
2. Generate an auth key at https://login.tailscale.com/admin/settings/keys
   - Use a **reusable** key for server automation
   - Set appropriate expiry (or no expiry for production servers)

## Configuration

Add to your vault.yml:

```yaml
secrets:
  tailscale:
    auth_key: "tskey-auth-XXXX-XXXXXXXXXX"
```

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `tailscale_auth_key` | `""` | Tailscale auth key for authentication |
| `tailscale_hostname` | `{{ inventory_hostname }}` | Hostname in Tailscale admin |
| `tailscale_advertise_routes` | `false` | Advertise routes to other nodes |
| `tailscale_routes` | `{{ internal_network }}` | Routes to advertise |
| `tailscale_exit_node` | `false` | Act as exit node |
| `tailscale_accept_routes` | `true` | Accept routes from other nodes |
| `tailscale_accept_dns` | `true` | Accept DNS from Tailscale |
| `tailscale_tags` | `[]` | ACL tags for this node |

## Usage

### Deploy Tailscale to proxy container

```bash
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags tailscale --limit proxy-lxc
```

### Manual setup on mobile device

1. Install Tailscale app from App Store / Play Store
2. Sign in with the same Tailscale account
3. Your phone will get a 100.x.x.x IP address
4. Access Busibox services via Tailscale IPs

### Verify connection

On the server:
```bash
/usr/local/bin/check-tailscale.sh
```

From your phone, try accessing:
- `http://100.x.x.x:8000/health` (Agent API)

## Network Architecture

```
Mobile Phone (100.x.x.1)
    ↓ Tailscale VPN (WireGuard)
Proxy-LXC (100.x.x.2)
    ↓ Internal Network (10.96.200.x)
Busibox Services
```

## Security Notes

- Tailscale uses WireGuard for encryption
- Authentication via Tailscale identity (no passwords)
- ACL policies can restrict access between nodes
- Consider using tags for RBAC (e.g., `tag:admin`, `tag:user`)
