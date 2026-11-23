from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import create_all_tables
from app.routers import organizations, business_units, leads, ingest, stats
from app.routers import auth, users, blog, marketing, onboarding, reporting, public_blogs
from app.routers import gsc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mugogo")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables. Shutdown: cleanup."""
    logger.info("🚀 Mugogo Inc. Engine starting up — env=%s", settings.app_env)
    await create_all_tables()
    logger.info("✅ Database tables verified / created")
    yield
    logger.info("👋 Mugogo Inc. Engine shutting down")


# 
app = FastAPI(
    title="Mugogo Inc. Lead Engine & Automated Growth OS",
    description=(
        "Multi-tenant lead capture, server-side retargeting, keyword monitoring, "
        "and automated subdirectory blog distribution engine."
    ),
    version="1.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return a CORS-visible JSON 500 for unexpected errors.

    Starlette's built-in 500 response is produced by the outermost
    ``ServerErrorMiddleware``, *outside* ``CORSMiddleware``.  That means
    unhandled exceptions reach the browser with no CORS headers, and the
    frontend only sees ``TypeError: Failed to fetch`` instead of the real
    error.  Adding the CORS headers here keeps the failure diagnosable.
    """
    logger.exception(
        "Unhandled error on %s %s: %s", request.method, request.url.path, exc
    )
    headers: dict[str, str] = {}
    origin = request.headers.get("origin")
    if origin and origin in settings.allowed_origins:
        headers["access-control-allow-origin"] = origin
        headers["access-control-allow-credentials"] = "true"
        headers["vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers=headers,
    )
# 
# # ── Routers ───────────────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"
# 
app.include_router(organizations.router, prefix=API_PREFIX)
app.include_router(business_units.router, prefix=API_PREFIX)
app.include_router(leads.router, prefix=API_PREFIX)
app.include_router(ingest.router, prefix=API_PREFIX)
app.include_router(stats.router, prefix=API_PREFIX)
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(users.router, prefix=API_PREFIX)
app.include_router(marketing.router, prefix=API_PREFIX)
app.include_router(onboarding.router, prefix=API_PREFIX)
app.include_router(reporting.router, prefix=API_PREFIX)
app.include_router(gsc.router, prefix=API_PREFIX)
# 
# # Blog Routers (Mounted with public & internal paths)
app.include_router(blog.public_router)
app.include_router(blog.internal_router, prefix=API_PREFIX)
app.include_router(blog.internal_router, prefix="/api")
app.include_router(public_blogs.router, prefix=API_PREFIX)
app.include_router(public_blogs.router)
# 
# 
# 
@app.get("/", tags=["Health"])
async def health() -> dict:
    return {
        "service": "Mugogo Inc. Lead Engine",
        "version": "1.0.0",
        "status": "operational",
        "env": settings.app_env,
    }
