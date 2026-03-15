---
title: "npm Package Registry"
category: "developer"
order: 110
description: "npm registry setup for @jazzmind/busibox-app"
published: true
---

# npm Package Registry for @jazzmind/busibox-app

## Current Setup

`@jazzmind/busibox-app` is published as a **public package on npmjs.org**. No authentication is required to install it.

```bash
npm install @jazzmind/busibox-app
```

No `.npmrc` configuration, GitHub tokens, or special registry setup is needed.

## Publishing

The package is published to npmjs.org via:

- **GitHub Actions**: `publish-busibox-app.yml` or `release-frontend.yml` workflows (uses `NPM_TOKEN` secret)
- **Manual**: `bash packages/app/publish.sh` (uses `NPM_TOKEN` env var or `npm login`)

### Setting Up NPM_TOKEN for CI

1. Create an npm access token at [npmjs.com/settings/tokens](https://www.npmjs.com/settings/tokens)
2. Choose "Automation" token type
3. Add it as a repository secret named `NPM_TOKEN` in the GitHub repo settings

### Publishing Manually

```bash
cd packages/app
NPM_TOKEN=<your-token> bash publish.sh
```

Or log in interactively:

```bash
npm login
cd packages/app
bash publish.sh
```

## Historical Note

Prior to the migration, this package was hosted on GitHub Packages (`npm.pkg.github.com`), which required authentication even for public packages. The package was moved to npmjs.org to eliminate the authentication requirement for consumers.

If you encounter old `.npmrc` files referencing `npm.pkg.github.com` for the `@jazzmind` scope, they can be safely removed.
