from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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


# In production the built SPA is served from the same origin as the API: hashed
# assets straight off disk, every other path falling back to index.html so
# client-side routes survive a hard refresh.
_static_dir = Path(settings.static_dir).resolve() if settings.static_dir else None
if _static_dir and _static_dir.is_dir():
    _index = _static_dir / "index.html"

    if (_static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # Unmatched API paths are genuine 404s, not the SPA shell.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (_static_dir / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(_static_dir)
        ):
            return FileResponse(candidate)
        if not _index.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(_index)
