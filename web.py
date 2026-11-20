"""Archival integration scaffold, NOT the deployed entrypoint. No provider calls are wired."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.httpx_client import close_shared_clients
from src.panel import router as panel_router
from src.router.antigravity.openai import router as openai_router
from src.router.antigravity.gemini import router as gemini_router
from src.router.antigravity.anthropic import router as anthropic_router
from src.router.antigravity.model_list import router as models_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_shared_clients()

app = FastAPI(title="AGY2API reviewed archive", lifespan=lifespan)
app.include_router(openai_router)
app.include_router(gemini_router)
app.include_router(anthropic_router)
app.include_router(models_router)
app.include_router(panel_router)
