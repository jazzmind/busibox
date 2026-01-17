import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agents, auth, chat, conversations, dispatcher, evals, health, insights, runs, scores, streams, tools, workflows
from app.config.settings import get_settings
from app.db.session import SessionLocal
from app.services.agent_registry import agent_registry
from app.utils.logging import setup_logging, setup_tracing, instrument_fastapi
from app.api.insights import init_insights_service

settings = get_settings()

# Initialize logging and tracing before creating app
setup_logging(settings)
setup_tracing(settings)

logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name, debug=settings.debug)

# Instrument FastAPI with OpenTelemetry
instrument_fastapi(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event() -> None:
    # Note: Schema migrations are handled by Alembic (runs before uvicorn starts)
    # We only use create_all for tables not managed by migrations (if any)
    # Skip create_all since Alembic manages all tables
    async with SessionLocal() as session:
        await agent_registry.refresh(session)
    logger.info("Agent registry initialized")
    
    # Initialize insights service
    insights_config = {
        "milvus_host": settings.milvus_host,
        "milvus_port": settings.milvus_port,
        "embedding_service_url": str(settings.ingest_api_url),
    }
    init_insights_service(insights_config)
    logger.info("Insights service initialized")


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(agents.router)
app.include_router(tools.router)
app.include_router(workflows.router)
app.include_router(evals.router)
app.include_router(dispatcher.router)
app.include_router(runs.router)
app.include_router(streams.router)
app.include_router(scores.router)
app.include_router(conversations.router)
app.include_router(insights.router)


@app.get("/")
async def root() -> dict:
    return {"service": settings.app_name, "env": settings.environment}
