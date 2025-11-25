"""
Health check endpoint.

Checks connectivity to all dependencies:
- PostgreSQL
- MinIO
- Redis
- Milvus
- liteLLM

Returns status: healthy (all up), degraded (some down), unhealthy (critical down)
"""

import time
from typing import Dict, Optional

import structlog
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from api.services.minio_service import MinIOService
from api.services.redis_service import RedisService
from shared.config import Config

logger = structlog.get_logger()

router = APIRouter()


async def check_postgres() -> Dict[str, any]:
    """Check PostgreSQL connectivity."""
    try:
        import asyncpg
        config = Config().to_dict()
        
        start = time.time()
        conn = await asyncpg.connect(
            host=config.get("postgres_host", "10.96.200.203"),
            port=config.get("postgres_port", 5432),
            database=config.get("postgres_db", "busibox"),
            user=config.get("postgres_user", "postgres"),
            password=config.get("postgres_password", ""),
            timeout=2,
        )
        await conn.close()
        response_time = round((time.time() - start) * 1000, 2)
        
        return {
            "status": "healthy",
            "response_time_ms": response_time,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_minio() -> Dict[str, any]:
    """Check MinIO connectivity."""
    try:
        config = Config().to_dict()
        minio_service = MinIOService(config)
        
        start = time.time()
        await minio_service.check_health()
        response_time = round((time.time() - start) * 1000, 2)
        
        return {
            "status": "healthy",
            "response_time_ms": response_time,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_redis() -> Dict[str, any]:
    """Check Redis connectivity."""
    try:
        config = Config().to_dict()
        redis_service = RedisService(config)
        
        start = time.time()
        await redis_service.check_health()
        response_time = round((time.time() - start) * 1000, 2)
        
        return {
            "status": "healthy",
            "response_time_ms": response_time,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_milvus() -> Dict[str, any]:
    """Check Milvus connectivity."""
    try:
        from pymilvus import connections, utility
        
        config = Config().to_dict()
        
        start = time.time()
        connections.connect(
            "default",
            host=config.get("milvus_host", "10.96.200.204"),
            port=config.get("milvus_port", "19530"),
            timeout=2,
        )
        
        # Check if collections exist
        collections = utility.list_collections()
        connections.disconnect("default")
        
        response_time = round((time.time() - start) * 1000, 2)
        
        return {
            "status": "healthy",
            "response_time_ms": response_time,
            "collections": len(collections),
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def check_litellm() -> Dict[str, any]:
    """Check liteLLM connectivity."""
    try:
        import httpx
        
        config = Config().to_dict()
        litellm_url = config.get("litellm_base_url", "http://10.96.200.207:4000")
        
        start = time.time()
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{litellm_url}/health")
            response.raise_for_status()
        
        response_time = round((time.time() - start) * 1000, 2)
        
        return {
            "status": "healthy",
            "response_time_ms": response_time,
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


@router.get("")
async def health_check():
    """
    Health check endpoint.
    
    Returns status of all dependencies with response times.
    Overall status: healthy (all up), degraded (some down), unhealthy (critical down)
    """
    checks = {
        "postgres": await check_postgres(),
        "minio": await check_minio(),
        "redis": await check_redis(),
        "milvus": await check_milvus(),
        "litellm": await check_litellm(),
    }
    
    # Determine overall status
    healthy_count = sum(1 for c in checks.values() if c["status"] == "healthy")
    total_count = len(checks)
    
    # Critical services: postgres, minio, redis
    critical_services = ["postgres", "minio", "redis"]
    critical_down = any(
        checks[svc]["status"] != "healthy" for svc in critical_services
    )
    
    if critical_down:
        overall_status = "unhealthy"
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    elif healthy_count == total_count:
        overall_status = "healthy"
        http_status = status.HTTP_200_OK
    else:
        overall_status = "degraded"
        http_status = status.HTTP_200_OK
    
    response_data = {
        "status": overall_status,
        "checks": checks,
        "healthy": f"{healthy_count}/{total_count}",
    }
    
    return JSONResponse(
        status_code=http_status,
        content=response_data,
    )

