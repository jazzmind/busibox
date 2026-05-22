# Contributing to Busibox

Thanks for your interest in Busibox. This document covers how to set up a
local environment, where to put things, and what to expect when you open a
pull request. It is deliberately short — for deeper architectural context,
follow the links into `docs/`.

## Project status and expectations

Busibox is at **v0.1.x**. Interfaces, schemas, and CLI flags may change
between minor versions. We welcome contributions but cannot yet promise a
stability guarantee or a fixed roadmap.

## Ways to contribute

- **File an issue.** Bug reports and design discussions are useful even if
  you can't write the fix.
- **Open a PR.** Small, focused PRs land faster than large ones. If you are
  planning a substantial change (new service, schema change, new auth path),
  please open an issue first so we can sanity-check the direction.
- **Improve docs.** Anything in `docs/` is fair game. Doc-only PRs are
  reviewed faster.
- **Report security issues privately.** See [SECURITY.md](SECURITY.md) — do
  not open a public issue for vulnerabilities.

## Local development

The fastest path to a running stack is the Docker + cloud-key path:

```bash
git clone https://github.com/jazzmind/busibox.git
cd busibox
cp env.local.example .env.local
# Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env.local
make docker-up
```

See [QUICKSTART.md](QUICKSTART.md) for details. Local-model inference (vLLM,
Ollama, MLX) is optional — see
[docs/administrators/local-models-addon.md](docs/administrators/local-models-addon.md).

For Proxmox / Kubernetes / multi-host workflows, use the `busibox` CLI as
described in [docs/administrators/01-quickstart.md](docs/administrators/01-quickstart.md).

## Repository conventions

Before adding files, please skim:

- [CLAUDE.md](CLAUDE.md) — top-level project conventions
- `.cursor/rules/001-documentation-organization.md` — where docs go
- `.cursor/rules/002-script-organization.md` — where scripts go
- `.cursor/rules/003-zero-trust-authentication.md` — auth rules (no shared
  service-to-service secrets)
- `.cursor/rules/010-make-commands.md` — `make` target reference

Naming: `kebab-case` for filenames; descriptive prefixes for scripts
(`deploy-*`, `setup-*`, `test-*`, `check-*`).

## Pull request checklist

Before requesting review:

- [ ] The change has a clear, narrow scope. Unrelated cleanups belong in
      separate PRs.
- [ ] Tests are added or updated for behavioural changes, where practical.
      For Python services: `make test-docker SERVICE=<name>`.
      For the Rust CLI: `cd cli && cargo test`.
- [ ] Documentation is updated for user-facing changes (new env vars,
      flags, endpoints, or workflows).
- [ ] Secrets are not committed. The default `.gitignore` covers most
      cases; double-check vault files, `.env*`, and SSH keys.
- [ ] No new service-to-service shared secrets — use the JWT/JWKS
      Zero-Trust pattern (see rule 003).
- [ ] If you touched dependencies, you've checked the license is permissive
      (MIT/BSD/Apache-2.0/ISC). If it isn't, call it out in the PR
      description so we can update [NOTICE](NOTICE).
- [ ] CHANGELOG.md has an entry under `## [Unreleased]` for user-visible
      changes.

## Commit style

We don't enforce conventional commits, but informative commit messages
help. A useful pattern:

    <area>: <imperative summary>

    Why: one or two sentences on motivation.
    What changed: bullets if needed.

Examples already in `git log`:

    feat: add app usage statistics endpoint and analytics support
    fix: remove insecure default values and enhance vault sync logging

## Code style

- **Python**: target Python 3.11+. Each service has its own `requirements.txt`
  and may use `ruff` / `pytest` configuration in `pyproject.toml`. Run any
  formatters/linters that the service already configures before pushing.
- **Rust**: `cd cli && cargo fmt && cargo clippy && cargo test`.
- **TypeScript / JS**: follow the Next.js / app conventions; prefer the
  shared `@jazzmind/busibox-app` library over reinventing utilities.

## Code of conduct

Participation in this project is governed by our
[Code of Conduct](CODE_OF_CONDUCT.md). Please read it.

## License

By submitting a contribution, you agree that your contribution will be
licensed under the project's [MIT License](LICENSE). You also confirm that
you have the right to license the contribution under those terms (i.e. you
wrote it, or it is otherwise compatibly licensed and properly attributed).

We do not currently require a CLA. Inbound = outbound.
