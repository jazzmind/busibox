import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager

from routes import admin, internal, oauth, keystore, users, auth, audit, bindings
from services.postgres import PostgresService
from config import Config

# Import shared test mode utilities
try:
    from busibox_common import TestModeConfig, init_database_router
    HAS_BUSIBOX_COMMON = True
except ImportError:
    HAS_BUSIBOX_COMMON = False

# Global configuration
config = Config()

# Production PostgresService instance (singleton)
pg = PostgresService(config.to_dict())

# Test PostgresService instance (only created if test mode is enabled)
pg_test = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database schema and bootstrap on startup."""
    global pg_test
    
    print("[AUTHZ] Initializing database schema...")
    await pg.connect()
    await pg.ensure_schema()
    print("[AUTHZ] Database schema initialized")
    
    # Initialize test database if test mode is enabled
    if config.test_mode_enabled:
        print("[AUTHZ] Test mode enabled - initializing test database...")
        test_config = config.to_dict()
        test_config["postgres_db"] = config.test_db_name
        test_config["postgres_user"] = config.test_db_user
        test_config["postgres_password"] = config.test_db_password
        pg_test = PostgresService(test_config)
        await pg_test.connect()
        await pg_test.ensure_schema()
        print(f"[AUTHZ] Test database '{config.test_db_name}' initialized")
    
    # Initialize the shared database router for test mode support
    if HAS_BUSIBOX_COMMON:
        test_mode_config = TestModeConfig(
            enabled=config.test_mode_enabled,
            test_db_name=config.test_db_name,
            test_db_user=config.test_db_user,
            test_db_password=config.test_db_password,
        )
        init_database_router(pg, pg_test, test_mode_config)
    
    # Set shared PostgresService instance in route modules
    oauth.set_pg_service(pg, pg_test)
    admin.set_pg_service(pg, pg_test)
    internal.set_pg_service(pg, pg_test)
    keystore.set_pg_service(pg, pg_test)
    users.set_pg_service(pg, pg_test)
    auth.set_pg_service(pg, pg_test)
    audit.set_pg_service(pg, pg_test)
    bindings.set_pg_service(pg, pg_test)
    
    # Run bootstrap for production (creates signing keys and optional OAuth client)
    from routes.oauth import _ensure_bootstrap
    await _ensure_bootstrap()
    
    print("[AUTHZ] Bootstrap complete")
    
    yield
    
    # Cleanup on shutdown
    await pg.disconnect()
    if pg_test:
        await pg_test.disconnect()
    print("[AUTHZ] Shutdown complete")


app = FastAPI(title="Authz Service", version="1.0.0", lifespan=lifespan)


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    return {"status": "ok"}


app.include_router(oauth.router)
app.include_router(internal.router)
app.include_router(admin.router)
app.include_router(keystore.router)
app.include_router(users.router)
app.include_router(auth.router)
app.include_router(audit.router)
app.include_router(bindings.router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8010, reload=False)





