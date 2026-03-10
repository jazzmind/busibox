"""
Config API — Busibox Configuration Service.

Centralised configuration management with tiered access control:
  - Public: branding, feature flags (no auth)
  - Authenticated: app registry, non-secret settings (any JWT)
  - App-scoped: app-specific secrets (JWT + app role binding)
  - Admin: full CRUD on all config and app registry (Admin role)
"""

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI

import asyncpg
from busibox_common import AsyncPGPoolManager

from config import config
from schema import get_config_schema
from services import config_store, app_registry
from seed import seed_defaults
from routes import public, authenticated, app_scoped, admin


async def _ensure_database():
    """
    Create the 'config' database if it doesn't exist.

    Connects to the default 'postgres' database to check/create, then
    disconnects. This runs once at startup so the service is self-provisioning
    on both Docker and Proxmox without requiring init-databases.sql or a
    separate Ansible task to pre-create the database.
    """
    db_name = config.postgres_db
    try:
        sys_conn = await asyncpg.connect(
            host=config.postgres_host,
            port=config.postgres_port,
            database="postgres",
            user=config.postgres_user,
            password=config.postgres_password,
        )
        try:
            exists = await sys_conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", db_name
            )
            if not exists:
                # Can't use parameterised DDL, but db_name comes from our own env
                await sys_conn.execute(f'CREATE DATABASE "{db_name}"')
                print(f"[CONFIG-API] Created database '{db_name}'")
            else:
                print(f"[CONFIG-API] Database '{db_name}' already exists")
        finally:
            await sys_conn.close()
    except Exception as e:
        # Non-fatal: the DB may already exist or user may lack CREATEDB.
        # The pool connect below will fail with a clear error if the DB
        # truly doesn't exist.
        print(f"[CONFIG-API] Could not ensure database (non-fatal): {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _ensure_database()

    pool_mgr = AsyncPGPoolManager.from_config(config.to_pool_config())
    await pool_mgr.connect()

    async with pool_mgr.acquire() as conn:
        schema = get_config_schema()
        await schema.apply(conn)
    print("[CONFIG-API] Database schema applied")

    config_store.set_pool(pool_mgr)
    app_registry.set_pool(pool_mgr)

    async with pool_mgr.acquire() as conn:
        await seed_defaults(conn)

    yield

    await pool_mgr.disconnect()
    print("[CONFIG-API] Shutdown complete")


app = FastAPI(title="Config API", version="1.0.0", lifespan=lifespan)


@app.get("/health/live")
async def live():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    return {"status": "ok"}


app.include_router(public.router)
app.include_router(authenticated.router)
app.include_router(app_scoped.router)
app.include_router(admin.router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=config.port, reload=False)
