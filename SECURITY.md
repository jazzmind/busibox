# Security Policy

## Project status

Busibox is early-stage software (v0.1.x). It is intended for evaluation,
self-hosted lab use, and design-partner pilots. **It has not yet been
audited by an independent security firm.** See [CHANGELOG.md](CHANGELOG.md)
for known limitations.

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅ — current development line, fixes go here |
| < 0.1.0 | ❌ — pre-release, not supported |

We will publish a clearer support window once the project reaches 1.0.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

Email the maintainers at:

    security@jazzmind.com

(or, if that bounces, `wes@sonnenreich.com`).

Please include:

- A description of the issue and the affected component (e.g. AuthZ API,
  Data API, CLI, Docker image, etc.)
- Steps to reproduce, ideally with a minimal proof-of-concept
- The version / commit SHA you tested against
- Any logs, request traces, or scanner output that helps us reproduce
- Your name / handle if you'd like credit in the release notes

We will acknowledge receipt within **3 business days** and aim to provide an
initial assessment (severity, fix plan, expected timeline) within **10 business
days**. For high-severity issues, we will coordinate a disclosure timeline
with you before publishing details.

## Scope

In scope:

- The Busibox source code in this repository (`srv/`, `cli/`, `tools/`,
  `scripts/`, `provision/`, default `docker-compose.yml`)
- Default configurations shipped in `env.local.example` and the Ansible roles

Out of scope:

- Vulnerabilities in upstream third-party dependencies that we vendor
  unmodified — please report those upstream and copy us
- Findings that require operator misconfiguration explicitly warned against
  in the documentation (e.g. running with default `devpassword` values in
  production, exposing internal-only services to the public internet)
- Denial-of-service from unbounded user-supplied AI workloads — the platform
  ships rate limits and cost ceilings, but tuning them is the operator's
  responsibility

## Hardening guidance

Operators preparing a production deployment should review:

- `docs/administrators/03-configure.md` — secret rotation, vault setup
- `docs/developers/architecture/` — Zero-Trust auth model, RLS, JWT scopes
- `tests/security/` — OWASP API Security Top 10 test suite (run with
  `make test-security`)

## Credit

We maintain a list of researchers who have responsibly disclosed issues in
the release notes for each version. Let us know if you'd prefer to remain
anonymous.
