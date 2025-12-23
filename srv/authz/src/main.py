import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager

from routes import admin, internal, oauth, keystore, users, auth, audit, bindings
from services.postgres import PostgresService
from config import Config

# Global PostgresService instance (singleton)
config = Config()
pg = PostgresService(config.to_dict())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database schema and bootstrap on startup."""
    print("[AUTHZ] Initializing database schema...")
    await pg.connect()
    await pg.ensure_schema()
    print("[AUTHZ] Database schema initialized")
    
    # Set shared PostgresService instance in route modules
    oauth.set_pg_service(pg)
    admin.set_pg_service(pg)
    internal.set_pg_service(pg)
    keystore.set_pg_service(pg)
    users.set_pg_service(pg)
    auth.set_pg_service(pg)
    audit.set_pg_service(pg)
    bindings.set_pg_service(pg)
    
    # Run bootstrap (creates signing keys and optional OAuth client)
    from routes.oauth import _ensure_bootstrap
    await _ensure_bootstrap()
    print("[AUTHZ] Bootstrap complete")
    
    yield
    
    # Cleanup on shutdown
    await pg.disconnect()
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





