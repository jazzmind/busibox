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


@app.get("/health/live")
async def live():
    """Liveness probe"""
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
