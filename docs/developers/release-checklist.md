---
title: "Release Checklist"
category: "developer"
order: 99
description: "Step-by-step checklist for cutting a Busibox release."
published: true
---

# Release Checklist

This is the canonical procedure for tagging a Busibox release. v0.1.0 is
the first public release.

> **Important:** Releases are **only** cut by a maintainer with push
> rights to `main` and the upstream remote. AI agents must not push tags
> or publish GitHub releases unsupervised.

---

## Pre-flight

- [ ] All target PRs merged to `main`. No open PRs labelled `release-blocker`.
- [ ] CHANGELOG.md has a section for the new version with date, populated
      from the `## [Unreleased]` queue.
- [ ] The version string is consistent across:
    - `CHANGELOG.md` (top entry)
    - `cli/*/Cargo.toml` (`version = "0.1.0"` in each crate that ships a binary)
    - `tools/mcp-*/package.json` (`"version": "0.1.0"`)
    - `QUICKSTART.md` ("Version" footer)
- [ ] `LICENSE` and `NOTICE` are in repo root and up to date. If any new
      non-permissively-licensed dependency landed since the last release,
      it is captured in `NOTICE`.
- [ ] `SECURITY.md` contact details are correct.
- [ ] CI is green on `main`.

## Sanity checks

Run these locally before tagging:

```bash
# Rust workspace builds and tests
cd cli && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
cd ..

# Quickstart works end-to-end with a cloud key
cp env.local.example .env.local
# (edit: set OPENAI_API_KEY, GITHUB_AUTH_TOKEN)
make docker-up
# Confirm: localhost:3000 loads, signup works, file upload works,
#          agent answers a RAG question with citations.

# Security test suite, if applicable to the change set
make test-security
```

## Tag and release

> Do not run these unless you are the maintainer with intent to publish.

```bash
# 1. Make sure main is clean
git checkout main && git pull --ff-only

# 2. Confirm the changelog entry is present
grep -n "^## \[0.1.0\]" CHANGELOG.md

# 3. Create an annotated, signed tag
git tag -s v0.1.0 -m "Busibox v0.1.0"

# 4. Push the tag
git push origin v0.1.0

# 5. Draft the GitHub release from the tag, using the v0.1.0 section of
#    CHANGELOG.md as the body. Mark as "latest release".
gh release create v0.1.0 \
    --title "Busibox v0.1.0" \
    --notes-file <(awk '/^## \[0.1.0\]/{flag=1} /^## \[/{if (flag && !/^## \[0.1.0\]/) exit} flag' CHANGELOG.md) \
    --verify-tag
```

If you'd rather draft in the UI, paste the v0.1.0 section of CHANGELOG.md
into the release body and tick "Set as the latest release".

## Post-release

- [ ] Announce the release on the channels we use (README badges,
      Discussions, Twitter / Mastodon if applicable).
- [ ] Open a "Post-release" issue listing follow-ups discovered during
      the release process so they don't get lost.
- [ ] Reset CHANGELOG.md to have an empty `## [Unreleased]` section
      under the new version.

## Rollback

If a critical issue is found post-release:

1. **Don't delete the tag** — that breaks consumers who already pulled.
2. Cut a `0.1.1` patch release with the fix, following this same
   checklist. Reference the original release in the CHANGELOG.
3. If the original release is dangerous to use (security or data-loss),
   mark it as a pre-release in the GitHub UI and update the release
   notes with a banner pointing to the patch.
