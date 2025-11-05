# Next.js App Deployment Role

Ansible role for deploying Next.js applications with PM2 process management.

## Features

- Node.js installation via NodeSource
- PM2 process manager with cluster mode
- Git-based deployment
- Environment variable management
- Health check verification
- Automatic restarts on failure

## Requirements

- Debian/Ubuntu-based system
- Git repository with Next.js app
- Internet access for package installation

## Role Variables

### Required Variables

```yaml
app_git_repo: "https://github.com/your-org/your-app.git"
```

### Optional Variables

```yaml
app_name: "ai-portal"              # Application name
app_user: "appuser"                # System user for running app
app_group: "appuser"               # System group
app_home: "/opt/{{ app_name }}"    # Application directory
app_port: 3000                     # Port to listen on
nodejs_version: "20"               # Node.js major version
app_git_branch: "main"             # Git branch to deploy

# PM2 configuration
pm2_instances: 2                   # Number of instances (cluster mode)
pm2_max_memory_restart: "1G"       # Restart if memory exceeds limit

# Build configuration
app_build_command: "npm run build"
app_start_command: "npm start"

# Health check
app_health_check_path: "/api/health"
app_health_check_timeout: 30

# Environment variables
app_env_vars:
  DATABASE_URL: "postgresql://..."
  API_KEY: "secret"
```

## Dependencies

None

## Example Playbook

```yaml
- hosts: apps
  roles:
    - role: nextjs_app
      vars:
        app_name: "ai-portal"
        app_git_repo: "https://github.com/sonnenreich/ai-portal.git"
        app_git_branch: "main"
        app_port: 3000
        app_database_url: "postgresql://user:pass@pg-host:5432/dbname"
        app_env_vars:
          LITELLM_BASE_URL: "http://10.96.201.207:4000/v1"
          LITELLM_API_KEY: "sk-litellm-master-key"
          BETTER_AUTH_SECRET: "{{ vault_better_auth_secret }}"
          BETTER_AUTH_URL: "https://test.ai.jaycashman.com"
```

## Tags

- `nextjs_install` - Installation tasks only
- `nextjs_configure` - Configuration tasks only
- `nextjs_deploy` - Deployment tasks only

## License

Proprietary

## Author

Busibox Infrastructure Team

