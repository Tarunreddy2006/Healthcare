
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import get_settings
from db import create_pool, close_pool, init_schema
from routes.auth import router as auth_router
from routes.visits import router as visits_router
from routes.register import router as register_router
from routes.summary import router as summary_router
from routes.conflicts import router as conflicts_router

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: boot DB pool, teardown on exit."""
    logger.info("🚀 Guardian AI Backend starting up…")
    await create_pool()
    await init_schema()
    logger.info("✅ Database ready.")
    yield
    logger.info("🛑 Guardian AI Backend shutting down…")
    await close_pool()


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Clinical Memory Bridge — Powered by AlphaNimble Membrain "
        "semantic memory engine and Google Gemini."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS (adjust origins for production) ─────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # restrict to your frontend domain in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(register_router)
app.include_router(visits_router)
app.include_router(summary_router)
app.include_router(conflicts_router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"], summary="System health probe")
async def health_check():
    """
    Lightweight endpoint for load-balancers and uptime monitors.
    Returns 200 if the application is running.
    """
    return JSONResponse(
        content={
            "status": "healthy",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
        }
    )


# ── Root ──────────────────────────────────────────────────────────────────────
@app.get("/", tags=["System"], include_in_schema=False)
async def root():
    return {"message": "Guardian AI Backend is running. Visit /docs for the API reference."}