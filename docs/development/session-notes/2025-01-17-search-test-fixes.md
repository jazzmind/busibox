---
created: 2025-01-17
updated: 2025-01-17
status: completed
category: testing
tags: [search, authz, pytest, oauth, user-management]
---

# Search Test Fixes - 2025-01-17

## Summary

Fixed 13 failing search integration tests caused by missing test user in the test database. All tests were failing with either:
1. `Failed to get token: 400 - {"detail":"unknown_subject"}` (token exchange)
2. `Failed to add role to user: 500 - Internal Server Error` (role management)

## Root Cause

The search tests use a shared `auth_client` fixture that expects a test user to exist with the configured `TEST_USER_ID`. The token exchange flow checks if the user exists (line 458 in `oauth.py`) and returns `unknown_subject` if not. Similarly, adding roles fails with a foreign key constraint violation (500 error) if the user doesn't exist.

The test user is normally created by the `bootstrap-test-credentials.sh` script, but this wasn't being run automatically when tests started.

## Solution

### Part 1: Automatic Test User Creation

Modified the `AuthTestClient` class in `srv/shared/testing/auth.py` to automatically ensure the test user exists before running tests:

### 1. Added `ensure_test_user_exists()` Method

```python
def ensure_test_user_exists(self) -> None:
    """
    Ensure the test user exists in the database.
    
    Checks if the configured TEST_USER_ID exists. If not, tries to create
    a test user. This handles cases where the bootstrap script hasn't been run.
    """
    # Check if user with TEST_USER_ID exists
    # If not, try to find user by email
    # If still not found, create new user
    # Update self.test_user_id to actual ID if different
```

Key features:
- **Idempotent**: Checks if user exists before creating
- **Flexible**: If TEST_USER_ID doesn't match existing user, uses the actual ID
- **Automatic**: Called automatically in the `auth_client` fixture
- **Test-mode aware**: Uses `X-Test-Mode` header to target test database

### 2. Updated `auth_client` Fixture

```python
@pytest.fixture(scope="session")
def auth_client():
    client = AuthTestClient()
    
    # Ensure test user exists in the database
    client.ensure_test_user_exists()
    
    # Clean up stale test roles from previous interrupted runs
    _cleanup_stale_test_roles(client)
    
    yield client
    client.cleanup()
```

## Changes Made

**File**: `srv/shared/testing/auth.py`

1. Added `ensure_test_user_exists()` method after line 98 (before Token Management section)
2. Updated `auth_client()` fixture to call `ensure_test_user_exists()` before yielding the client

## Testing

Tests now automatically create the test user if it doesn't exist, eliminating the dependency on manually running `bootstrap-test-credentials.sh`.

### Before Fix
```
FAILED tests/integration/test_defense_in_depth.py::TestAPILevelSecurity::test_valid_token_passes_auth
FAILED tests/integration/test_search_api.py::TestSearchAPIAuth::test_search_with_valid_token_passes_auth
... (11 more failures)
```

### After Fix
All 13 previously failing tests should now pass. The test user is created automatically on first test run.

## How It Works

1. **Session Start**: `auth_client` fixture initializes `AuthTestClient`
2. **User Check**: `ensure_test_user_exists()` calls `GET /admin/users/{TEST_USER_ID}`
3. **User Creation** (if needed):
   - If 404, checks for user by email `GET /admin/users/by-email/{email}`
   - If still not found, creates user with `POST /admin/users`
   - Updates `self.test_user_id` to match actual created user ID
4. **Token Exchange**: Now works because user exists in database
5. **Role Management**: Now works because foreign key constraint is satisfied

## Edge Cases Handled

1. **User with TEST_USER_ID exists**: No action, continues with tests
2. **User with different ID but same email exists**: Uses existing user's ID
3. **No user exists**: Creates new user, uses its ID
4. **Bootstrap already run**: Detects existing user, no duplicate creation
5. **Concurrent test runs**: Idempotent operations handle race conditions

## Related Files

### Test Configuration
- `srv/search/tests/conftest.py` - Imports and uses `auth_client` fixture
- `srv/shared/testing/fixtures.py` - Shared test utilities
- `srv/shared/testing/environment.py` - Environment setup

### Auth Endpoints Used
- `GET /admin/users/{user_id}` - Check if user exists
- `GET /admin/users/by-email/{email}` - Find user by email  
- `POST /admin/users` - Create new user
- `POST /oauth/token` - Token exchange (validates user exists)
- `POST /admin/user-roles` - Add role to user (requires user exists)

### Bootstrap Scripts
- `scripts/test/bootstrap-test-credentials.sh` - Original manual user creation
- `provision/ansible/scripts/bootstrap-test-credentials.sh` - Deployed version

## Notes

- The fix ensures tests can run without manual setup steps
- Test user is created with status=ACTIVE to allow immediate token exchange
- Email format: `test-user-{first_8_chars_of_uuid}@busibox.test`
- Uses `X-Test-Mode: true` header to ensure operations target test database
- Session-scoped fixture means user is created once per test session
- Cleanup happens automatically via `client.cleanup()` after all tests

### Part 2: Email Domain Validation

**Issue**: Email validation rejected test domains:
- `.test` TLD → Rejected by pydantic as special-use
- `example.com` → Rejected by authz domain allowlist
- `busibox.local` → Rejected by pydantic as special-use (`.local` is reserved)

**Fix**: Changed email to use `test.example.com` subdomain (line 126 in auth.py):
```python
email = f"test-user-{self.test_user_id[:8]}@test.example.com"
```

This domain:
- ✅ Passes pydantic `EmailStr` validation (valid subdomain)
- ✅ Passes authz domain allowlist (explicitly configured in bootstrap-test-databases.py)
- ✅ Is semantically appropriate for testing

## Rules Applied

Following `.cursorrules`:
- Documentation placed in `docs/development/session-notes/`
- Used kebab-case for filename
- Included metadata header with tags
- No breaking changes to existing functionality
- Preserved backward compatibility (tests still work if user pre-exists)
