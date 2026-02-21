"""
Busibox Deployment Service

Separate service for app deployment, database provisioning, and nginx configuration.
Runs on port 8011 in the authz container.
"""

import uvicorn
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from .routes import router
from .system_routes import router as system_router
from .service_routes import router as service_router
from .config_routes import router as config_router, ensure_config_database
from .github_routes import router as github_router
from .deployment_config_routes import router as deployment_config_router
from .deployment_history_routes import router as deployment_history_router
from .secrets_routes import router as secrets_router
from .releases_routes import router as releases_router
from .test_routes import router as test_router
from .config import config

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if config.debug else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    logger.info(f"[DEPLOY] Starting deployment service on port {config.port}")
    logger.info(f"[DEPLOY] Authz URL: {config.authz_url}")
    logger.info(f"[DEPLOY] Ansible dir: {config.ansible_dir}")
    logger.info(f"[DEPLOY] PostgreSQL host: {config.postgres_host}")
    logger.info(f"[DEPLOY] Apps container: {config.apps_container_ip}")
    logger.info(f"[DEPLOY] Nginx host: {config.nginx_host}")
    logger.info(f"[DEPLOY] Config database: {config.config_database}")
    
    # Ensure config database and table exist
    try:
        await ensure_config_database()
    except Exception as e:
        logger.warning(f"[DEPLOY] Failed to ensure config database (non-fatal): {e}")
    
    yield
    
    logger.info("[DEPLOY] Shutting down deployment service")


app = FastAPI(
    title="Busibox Deployment Service",
    description="API for deploying apps, provisioning databases, and configuring nginx",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router)
app.include_router(system_router)
app.include_router(service_router)
app.include_router(config_router)
app.include_router(github_router)
app.include_router(deployment_config_router)
app.include_router(deployment_history_router)
app.include_router(secrets_router)
app.include_router(releases_router)
app.include_router(test_router)


@app.get("/health")
@app.get("/health/live")
async def live():
    """Liveness probe - /health alias for deploy-api self-check"""
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    """Readiness probe - checks if service can handle requests"""
    # TODO: Check authz connectivity, SSH keys, etc.
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=config.port,
        reload=config.debug
    )
