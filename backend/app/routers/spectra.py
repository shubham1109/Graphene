from typing import Annotated

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.config import get_settings
from app.deps import CurrentUser, Database, parse_object_id
from app.models.common import utcnow
from app.models.sample import Sample
from app.models.spectrum import SpectrumMeta, SpectrumPublic, Technique, XPSRegion
from app.parsers.raman import parse_raman
from app.parsers.spec import parse_spec
from app.parsers.tabular import ParseError
from app.parsers.xps import parse_xps
from app.routers.samples import get_owned_sample
from app.storage import build_key, get_storage

router = APIRouter(prefix="/api/samples/{sample_id}", tags=["spectra"])

PREVIEW_BUCKETS = 600


def downsample(x: np.ndarray, y: np.ndarray, buckets: int = PREVIEW_BUCKETS):
    """Min/max decimation for plotting.

    Taking every Nth point would drop narrow bands entirely - a monolayer 2D
    peak can be a handful of samples wide. Emitting the minimum and maximum of
    each bucket preserves the visual envelope at a bounded point count.
    """
    n = x.size
    if n <= buckets * 2:
        return x.tolist(), y.tolist()

    edges = np.linspace(0, n, buckets + 1).astype(int)
    out_x: list[float] = []
    out_y: list[float] = []
    for start, end in zip(edges[:-1], edges[1:]):
        if end <= start:
            continue
        segment = y[start:end]
        lo = start + int(np.argmin(segment))
        hi = start + int(np.argmax(segment))
        first, second = (lo, hi) if lo <= hi else (hi, lo)
        out_x.extend([float(x[first]), float(x[second])])
        out_y.extend([float(y[first]), float(y[second])])
    return out_x, out_y


async def _read_upload(file: UploadFile) -> bytes:
    settings = get_settings()
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File is {len(raw) / 1e6:.1f} MB; the limit is "
                f"{settings.max_upload_bytes / 1e6:.0f} MB."
            ),
        )
    return raw


@router.post("/spectra", response_model=list[SpectrumPublic], status_code=status.HTTP_201_CREATED)
async def upload_spectrum(
    sample_id: str,
    user: CurrentUser,
    db: Database,
    file: Annotated[UploadFile, File()],
    technique: Annotated[Technique, Form()],
    region: Annotated[XPSRegion | None, Form()] = None,
    excitation_nm: Annotated[float | None, Form()] = None,
) -> list[SpectrumPublic]:
    await get_owned_sample(db, sample_id, user.id)
    raw = await _read_upload(file)
    filename = file.filename or "upload.dat"

    if technique == "dft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload property spec sheets to /api/samples/{sample_id}/spec instead.",
        )

    try:
        if technique == "raman":
            x, y, meta = parse_raman(raw, filename, excitation_nm)
            parsed = [(x, y, meta)]
        else:
            parsed = parse_xps(raw, filename, region if region != "unknown" else None)
    except ParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    storage = get_storage()
    storage_key = build_key(user.id, sample_id, filename)
    storage.put(storage_key, raw)

    now = utcnow()
    created: list[SpectrumPublic] = []
    for x, y, meta in parsed:
        preview_x, preview_y = downsample(np.asarray(x), np.asarray(y))
        document = {
            "sample_id": sample_id,
            "user_id": user.id,
            "technique": technique,
            "region": meta.region,
            "meta": meta.model_dump(),
            "storage_key": storage_key,
            "preview_x": preview_x,
            "preview_y": preview_y,
            "created_at": now,
        }
        result = await db.spectra.insert_one(document)
        document["_id"] = result.inserted_id
        created.append(SpectrumPublic.from_mongo(document))
    return created


@router.post("/spec", response_model=Sample)
async def upload_spec_sheet(
    sample_id: str,
    user: CurrentUser,
    db: Database,
    file: Annotated[UploadFile, File()],
) -> Sample:
    """Parse a property/DFT spec sheet and merge it into the sample."""
    document = await get_owned_sample(db, sample_id, user.id)
    raw = await _read_upload(file)
    try:
        properties, _pairs, warnings = parse_spec(raw, file.filename or "spec.json")
    except ParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    # Merge rather than replace: a spec sheet usually covers only some fields,
    # and values the user typed in by hand should survive an upload.
    existing = document.get("properties") or {}
    merged = {**existing}
    merged.update({k: v for k, v in properties.model_dump().items() if v is not None})

    await db.samples.update_one(
        {"_id": document["_id"]},
        {"$set": {"properties": merged, "updated_at": utcnow(), "spec_warnings": warnings}},
    )
    return Sample.from_mongo(await db.samples.find_one({"_id": document["_id"]}))


@router.get("/spectra", response_model=list[SpectrumPublic])
async def list_spectra(sample_id: str, user: CurrentUser, db: Database) -> list[SpectrumPublic]:
    await get_owned_sample(db, sample_id, user.id)
    cursor = db.spectra.find({"sample_id": sample_id}).sort("created_at", 1)
    return [SpectrumPublic.from_mongo(doc) async for doc in cursor]


@router.delete("/spectra/{spectrum_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_spectrum(
    sample_id: str, spectrum_id: str, user: CurrentUser, db: Database
) -> None:
    await get_owned_sample(db, sample_id, user.id)
    oid = parse_object_id(spectrum_id, "spectrum id")
    document = await db.spectra.find_one({"_id": oid, "sample_id": sample_id})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Spectrum not found.")

    key = document.get("storage_key")
    await db.spectra.delete_one({"_id": oid})
    # A VAMAS file yields several spectra backed by one object; only delete the
    # object once nothing else references it.
    if key and await db.spectra.count_documents({"storage_key": key}, limit=1) == 0:
        try:
            get_storage().delete(key)
        except Exception:  # noqa: BLE001 - deletion is best-effort
            pass


def load_spectrum_arrays(document: dict) -> tuple[np.ndarray, np.ndarray]:
    """Re-parse the stored raw file to recover full-resolution data."""
    meta = SpectrumMeta.model_validate(document["meta"])
    raw = get_storage().get(document["storage_key"])
    if document["technique"] == "raman":
        x, y, _ = parse_raman(raw, meta.filename, meta.excitation_nm)
        return x, y
    for x, y, parsed_meta in parse_xps(raw, meta.filename):
        if parsed_meta.region == document.get("region"):
            return x, y
    x, y, _ = parse_xps(raw, meta.filename)[0]
    return x, y
