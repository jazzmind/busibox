"""
Health check routes.
"""

import structlog
from fastapi import APIRouter, HTTPException

from shared.schemas import HealthResponse
from services.milvus_search import MilvusSearchService
from services.embedder import EmbeddingService
from services.reranker import RerankingService
from shared.config import config

logger = structlog.get_logger()

router = APIRouter()

# Initialize services
milvus_service = MilvusSearchService(config.to_dict())
embedding_service = EmbeddingService(config.to_dict())
reranking_service = RerankingService(config.to_dict())


@router.get("", response_model=HealthResponse)
async def health_check():
    """
    Check health of search service and its dependencies.
    
    Returns status for:
    - Milvus vector database
    - PostgreSQL database  
    - Reranker model
    - Embedding service
    - Redis cache (if enabled)
    """
    try:
        # Check Milvus
        milvus_healthy = milvus_service.health_check()
        milvus_status = "connected" if milvus_healthy else "unavailable"
        
        # Check PostgreSQL
        import asyncpg
        postgres_healthy = False
        try:
            db_url = (
                f"postgresql://{config.postgres_user}:{config.postgres_password}"
                f"@{config.postgres_host}:{config.postgres_port}/{config.postgres_db}"
            )
            conn = await asyncpg.connect(db_url, timeout=5.0)
            await conn.close()
            postgres_healthy = True
        except Exception as e:
            logger.error("PostgreSQL health check failed", error=str(e))
        
        postgres_status = "connected" if postgres_healthy else "unavailable"
        
        # Check reranker
        reranker_healthy = reranking_service.health_check()
        reranker_status = "loaded" if reranker_healthy else "unavailable"
        
        # Check embedding service
        embedder_healthy = await embedding_service.health_check()
        embedder_status = "available" if embedder_healthy else "unavailable"
        
        # Check cache (if enabled)
        cache_status = None
        if config.enable_caching and config.redis_host:
            try:
                import redis
                r = redis.Redis(
                    host=config.redis_host,
                    port=config.redis_port,
                    password=config.redis_password,
                    socket_connect_timeout=5,
                )
                r.ping()
                cache_status = "connected"
            except Exception as e:
                logger.error("Redis health check failed", error=str(e))
                cache_status = "unavailable"
        
        # Determine overall status
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
        logger.error(
            "Health check failed",
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail=f"Health check failed: {str(e)}"
        )

