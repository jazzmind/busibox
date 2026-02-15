import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, agents, auth, chat, classify_tags, conversations, dispatcher, evals, execution_streams, health, insights, llm, runs, scores, streams, tasks, tools, webhooks, workflows
from app.config.settings import get_settings
from app.db.session import SessionLocal
from app.services.agent_registry import agent_registry
from app.services.scheduler import task_scheduler, run_scheduler
from app.utils.logging import setup_logging, setup_tracing, instrument_fastapi
from app.api.insights import init_insights_service

settings = get_settings()

# Initialize logging and tracing before creating app
setup_logging(settings)
setup_tracing(settings)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    # Startup
    # Note: Schema migrations are handled by Alembic (runs before uvicorn starts)
    # We only use create_all for tables not managed by migrations (if any)
    # Skip create_all since Alembic manages all tables
    async with SessionLocal() as session:
        await agent_registry.refresh(session)
    logger.info("Agent registry initialized")
    
    # Initialize insights service
    # Use dedicated embedding API if configured, otherwise fall back to data API
    embedding_url = settings.embedding_api_url or str(settings.data_api_url)
    insights_config = {
        "milvus_host": settings.milvus_host,
        "milvus_port": settings.milvus_port,
        "embedding_service_url": embedding_url,
    }
    init_insights_service(insights_config)
    logger.info("Insights service initialized")
    
    # Initialize task scheduler and restore task schedules from database
    try:
        await task_scheduler.restore_task_schedules(SessionLocal)
        logger.info("Task scheduler initialized and schedules restored")
    except Exception as e:
        logger.error(f"Failed to initialize task scheduler: {e}", exc_info=True)
    
    yield
    
    # Shutdown
    logger.info("Application shutting down")
    run_scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

# Instrument FastAPI with OpenTelemetry
instrument_fastapi(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(health.router)
app.include_router(classify_tags.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(agents.router)
app.include_router(tools.router)
app.include_router(workflows.router)
app.include_router(evals.router)
app.include_router(dispatcher.router)
app.include_router(runs.router)
app.include_router(streams.router)
app.include_router(execution_streams.router)
app.include_router(scores.router)
app.include_router(conversations.router)
app.include_router(insights.router)
app.include_router(tasks.router)
app.include_router(webhooks.router)
app.include_router(llm.router)
app.include_router(admin.router)


@app.get("/")
async def root() -> dict:
    return {"service": settings.app_name, "env": settings.environment}
