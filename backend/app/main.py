from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db as db_module
from app.config import get_settings
from app.routers import analysis, auth, reference, samples, spectra


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_module.connect()
    try:
        yield
    finally:
        await db_module.disconnect()


settings = get_settings()

app = FastAPI(
    title="Graphene Benchmarker API",
    description=(
        "Upload Raman, XPS and property data for a graphene sample; get peak fits, "
        "chemical quantification, form classification, application recommendations "
        "and the closest commercial products."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(samples.router)
app.include_router(spectra.router)
app.include_router(analysis.router)
app.include_router(reference.router)


@app.get("/api/health", tags=["health"])
async def health() -> dict:
    """Liveness plus a real round trip to Mongo."""
    status = "ok"
    detail = None
    try:
        await db_module.get_db().command("ping")
    except Exception as exc:  # noqa: BLE001 - report any failure to the caller
        status = "degraded"
        detail = str(exc)
    return {"status": status, "database": detail or "connected", "version": app.version}
