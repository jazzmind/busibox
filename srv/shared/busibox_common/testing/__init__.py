"""
Busibox Test Utilities  (``busibox_common.testing``)
=====================================================

Shared helpers for writing authenticated integration tests against Busibox
services.  Install with::

    pip install busibox-common[testing]

Quick-start
-----------
In your service's ``tests/conftest.py``::

    from pathlib import Path
    from busibox_common.testing.environment import load_env_files, create_service_auth_fixture

    # 1. Load .env files BEFORE importing app code
    load_env_files(Path(__file__).parent.parent)

    # 2. Import shared fixtures so pytest discovers them
    from busibox_common.testing.auth import auth_client, clean_test_user  # noqa: F401
    from busibox_common.testing.database import event_loop  # noqa: F401

    # 3. Create an autouse fixture that sets AUTHZ_AUDIENCE for this service
    set_auth_env = create_service_auth_fixture("myservice")

Then in your tests::

    def test_authenticated_endpoint(auth_client):
        token = auth_client.get_token(audience="myservice-api")
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Test-Mode": "true",
        }
        # ... make requests with headers ...

Architecture
------------
* All integration tests talk to a **real authz service** running against
  the ``test_authz`` database.
* The ``X-Test-Mode: true`` header routes each service's DB queries to
  its dedicated test database (``test_agent``, ``test_files``, etc.).
* ``AuthTestClient`` obtains real JWTs via the magic-link login flow,
  then exchanges them for service-scoped access tokens.  No mocking.
* Pure unit tests that don't hit the network may use mock principals,
  but those should be clearly labelled *"unit test only"*.

Submodules
----------
``auth``
    ``AuthTestClient`` — token acquisition, role management, cleanup.
``database``
    ``DatabasePool``, ``RLSEnabledPool`` — async connection pools for tests.
``environment``
    ``load_env_files``, ``create_service_auth_fixture`` — env setup helpers.
``fixtures``
    ``require_env``, ``get_env``, ``get_authz_base_url`` — tiny utilities.
``clients``
    ``create_async_client``, ``create_sync_client`` — HTTP client factories.
``pytest_failed_filter``
    Pytest plugin that writes a filter file for re-running only failed tests.
"""

from .auth import AuthTestClient, auth_client, clean_test_user
from .fixtures import require_env, get_authz_base_url, get_env
from .database import (
    DatabasePool,
    RLSEnabledPool,
    db_pool,
    db_conn,
    rls_pool,
    check_postgres_connection,
    wait_for_postgres,
)
from .environment import (
    load_env_files,
    get_test_doc_repo_path,
    TEST_DOC_REPO_PATH,
    set_auth_env,
    create_service_auth_fixture,
    get_service_config,
)
from .clients import (
    create_async_client,
    create_async_client_no_auth,
    create_sync_client,
    async_test_client,
)

__all__ = [
    # Auth utilities
    "AuthTestClient",
    "auth_client",
    "clean_test_user",
    # Environment utilities (legacy)
    "require_env",
    "get_authz_base_url",
    "get_env",
    # Environment utilities (new)
    "load_env_files",
    "get_test_doc_repo_path",
    "TEST_DOC_REPO_PATH",
    "set_auth_env",
    "create_service_auth_fixture",
    "get_service_config",
    # Database utilities
    "DatabasePool",
    "RLSEnabledPool",
    "db_pool",
    "db_conn",
    "rls_pool",
    "check_postgres_connection",
    "wait_for_postgres",
    # Test client utilities
    "create_async_client",
    "create_async_client_no_auth",
    "create_sync_client",
    "async_test_client",
]
