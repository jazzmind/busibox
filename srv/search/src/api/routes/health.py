"""
Health check routes.

Uses the *app-level* service singletons from ``api.main`` rather than creating
separate instances.  Milvus and reranker checks report cached connection state
so the health endpoint never blocks the event loop with gRPC calls.
"""

import asyncio

import structlog
from fastapi import APIRouter, HTTPException

from shared.schemas import HealthResponse
from shared.config import config

logger = structlog.get_logger()

router = APIRouter()


@router.get("", response_model=HealthResponse)
async def health_check():
    """
    Check health of search service and its dependencies.

    Returns status for Milvus, PostgreSQL, reranker, embedding service,
    and Redis cache.  All checks are non-blocking.
    """
    try:
        from api.routes.search import milvus_service, embedding_service, reranking_service

        # Milvus — report cached connection state (no blocking gRPC call)
        milvus_healthy = milvus_service.connected
        milvus_status = "connected" if milvus_healthy else "unavailable"

        # PostgreSQL — async query with timeout
        postgres_healthy = False
        try:
            from api.main import pg_pool
            async with pg_pool.acquire() as conn:
                await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=5)
            postgres_healthy = True
        except Exception as e:
            logger.error("PostgreSQL health check failed", error=str(e))
        postgres_status = "connected" if postgres_healthy else "unavailable"

        # Reranker — just check if enabled/loaded (no model.predict() call)
        reranker_healthy = not reranking_service.enabled or reranking_service.model is not None
        reranker_status = "loaded" if reranker_healthy else "unavailable"

        # Embedding service — lightweight HTTP check with timeout
        try:
            embedder_healthy = await asyncio.wait_for(
                embedding_service.health_check(), timeout=5
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Embedding health check timed out", error=str(e))
            embedder_healthy = False
        embedder_status = "available" if embedder_healthy else "unavailable"

        # Redis cache
        cache_status = None
        if config.enable_caching and config.redis_host:
            try:
                import redis
                r = redis.Redis(
                    host=config.redis_host,
                    port=config.redis_port,
                    password=config.redis_password,
                    socket_connect_timeout=3,
                )
                r.ping()
                cache_status = "connected"
            except Exception as e:
                logger.error("Redis health check failed", error=str(e))
                cache_status = "unavailable"

        critical_healthy = milvus_healthy and postgres_healthy

        if critical_healthy:
            overall_status = "healthy"
        elif milvus_healthy or postgres_healthy:
            overall_status = "degraded"
        else:
            overall_status = "unhealthy"

        response = HealthResponse(
            status=overall_status,
            milvus=milvus_status,
            postgres=postgres_status,
            reranker=reranker_status,
            embedder=embedder_status,
            cache=cache_status,
        )

        if overall_status == "unhealthy":
            raise HTTPException(status_code=503, detail=response.dict())

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Health check failed", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Health check failed: {str(e)}"
        )

