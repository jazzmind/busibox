import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agents, auth, health, runs, scores, streams
from app.config.settings import get_settings
from app.db.session import engine
from app.models.base import Base
from app.services.agent_registry import agent_registry
from app.db.session import SessionLocal
from app.utils.logging import setup_logging, setup_tracing, instrument_fastapi

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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await agent_registry.refresh(session)
    logger.info("Agent registry initialized")


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(runs.router)
app.include_router(streams.router)
app.include_router(scores.router)


@app.get("/")
async def root() -> dict:
    return {"service": settings.app_name, "env": settings.environment}
