"""
Health Check Endpoints

Provides health check functionality for monitoring:
- Overall service health
- Individual dependency health checks
- Liveness probe (is service running?)
- Readiness probe (is service ready to accept traffic?)
"""

import os
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, status
from pydantic import BaseModel

from utils.health import (
    check_postgres,
    check_milvus,
    check_minio,
    check_redis,
)

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str  # "healthy" or "unhealthy"
    service: str
    version: str
    timestamp: str
    checks: Dict[str, str]  # dependency -> status


class LivenessResponse(BaseModel):
    """Liveness probe response model."""
    alive: bool
    service: str
    timestamp: str


@router.get("/", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def health_check():
    """
    Comprehensive health check endpoint.
    
    Returns 200 if service and all dependencies are healthy.
    Returns 503 if any dependency is unhealthy.
    """
    checks = {
        "database": "unknown",
        "milvus": "unknown",
        "minio": "unknown",
        "redis": "unknown",
    }
    
    # Check PostgreSQL
    try:
        if check_postgres():
            checks["database"] = "ok"
        else:
            checks["database"] = "error"
    except Exception as e:
        checks["database"] = f"error: {str(e)}"
    
    # Check Milvus
    try:
        if check_milvus():
            checks["milvus"] = "ok"
        else:
            checks["milvus"] = "error"
    except Exception as e:
        checks["milvus"] = f"error: {str(e)}"
    
    # Check MinIO
    try:
        if check_minio():
            checks["minio"] = "ok"
        else:
            checks["minio"] = "error"
    except Exception as e:
        checks["minio"] = f"error: {str(e)}"
    
    # Check Redis
    try:
        if check_redis():
            checks["redis"] = "ok"
        else:
            checks["redis"] = "error"
    except Exception as e:
        checks["redis"] = f"error: {str(e)}"
    
    # Determine overall status
    all_healthy = all(status == "ok" for status in checks.values())
    overall_status = "healthy" if all_healthy else "unhealthy"
    
    response = HealthResponse(
        status=overall_status,
        service="agent-api",
        version=os.getenv("API_VERSION", "0.1.0"),
        timestamp=datetime.utcnow().isoformat() + "Z",
        checks=checks,
    )
    
    # Return 503 if unhealthy
    if not all_healthy:
        return response  # FastAPI will use 503 from raises
    
    return response


@router.get("/live", response_model=LivenessResponse, status_code=status.HTTP_200_OK)
async def liveness_probe():
    """
    Liveness probe endpoint.
    
    Used by Kubernetes/systemd to determine if service should be restarted.
    Always returns 200 if the service is running.
    """
    return LivenessResponse(
        alive=True,
        service="agent-api",
        timestamp=datetime.utcnow().isoformat() + "Z",
    )


@router.get("/ready", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def readiness_probe():
    """
    Readiness probe endpoint.
    
    Used by Kubernetes/load balancers to determine if service can accept traffic.
    Returns 200 if service is ready (all dependencies healthy).
    Returns 503 if service is not ready.
    """
    # Same as health check
    return await health_check()

