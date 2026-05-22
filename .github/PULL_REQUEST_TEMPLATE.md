<!--
Thanks for the PR! A few notes before you submit:

- Keep the change narrow. Unrelated cleanups belong in separate PRs.
- For substantial changes (new service, schema migration, new auth path),
  please open an issue first.
- Security issues: do NOT submit a PR — see SECURITY.md.
-->

## Summary

<!-- One or two sentences. What does this PR change, and why? -->

## Related issue

<!-- Closes #123 / refs #456. Delete if not applicable. -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor (no behaviour change)
- [ ] Documentation
- [ ] Build / CI / tooling

## Testing

<!--
What did you do to convince yourself this works?
Examples:
  - `cd cli && cargo test`
  - `make test-docker SERVICE=agent ARGS=tests/integration/test_X.py`
  - Manually uploaded a PDF, hit /v1/search, observed correct results
-->

## Checklist

- [ ] Scope is narrow and focused
- [ ] Tests added / updated where practical
- [ ] Docs updated for user-visible changes (env vars, flags, endpoints)
- [ ] No new service-to-service shared secrets (Zero-Trust JWT only)
- [ ] No secrets / vault files / `.env*` committed
- [ ] CHANGELOG.md updated under `## [Unreleased]` (for user-visible changes)
- [ ] If a new dependency was added, its license is permissive (MIT / BSD /
      Apache-2.0 / ISC). Otherwise, called out below.

## Notes for reviewers

<!-- Anything reviewers should know? Tricky bits, follow-ups, known limitations. -->
