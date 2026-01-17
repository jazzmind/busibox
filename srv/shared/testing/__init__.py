# Shared testing utilities for busibox services
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
