"""Idempotent seeding of the reference library.

Run with:  python -m seed.seed          (from the backend/ directory)

Every record is upserted on its `key`, so re-running refreshes values without
duplicating documents. Records carry provenance (source, DOI, URL, version,
retrieval date) so the report generator can cite them.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.db import DatabaseUnavailable, connect, disconnect

DATA_DIR = Path(__file__).parent / "data"

# Producer specs are compiled by hand from public datasheets. They are nominal
# grade values, not batch certificates, and vendors revise them without notice.
PRODUCER_PROVENANCE = {
    "source": "Compiled from public producer datasheets and product pages",
    "doi": None,
    "url": None,
    "version": "2026.1",
    "licence": "Public marketing/technical literature, quoted for comparison",
    "retrieved": "2026-08-12",
    "note": (
        "Nominal grade specifications, not batch certificates. Values not quoted "
        "on a datasheet (typically I(D)/I(G) and C/O) are representative estimates "
        "for the product class. Always confirm current-batch specifications with "
        "the vendor before making a purchasing decision."
    ),
}

RAMAN_CURVE_RANGE = (1000.0, 3200.0, 1200)
XPS_CURVE_RANGE = (280.0, 296.0, 800)


def _lorentzian(x: np.ndarray, centre: float, height: float, fwhm: float) -> np.ndarray:
    return height / (1.0 + ((x - centre) / (fwhm / 2.0)) ** 2)


def _gaussian(x: np.ndarray, centre: float, height: float, fwhm: float) -> np.ndarray:
    sigma = fwhm / 2.35482
    return height * np.exp(-((x - centre) ** 2) / (2.0 * sigma**2))


def build_curve(record: dict) -> tuple[list[float], list[float]]:
    """Synthesise a representative trace from published peak parameters.

    These are not raw dataset traces - the peak parameters are what the
    literature actually reports. Curves exist so the overlay plot has a
    meaningful comparison line; `synthetic_curve` is set True on every record
    so the UI can say so.
    """
    peaks = record.get("peaks") or []
    if not peaks:
        return [], []

    if record["technique"] == "raman":
        lo, hi, n = RAMAN_CURVE_RANGE
        x = np.linspace(lo, hi, n)
        y = np.zeros_like(x)
        for peak in peaks:
            y += _lorentzian(
                x, peak["center_cm1"], peak.get("relative_height", 1.0), peak["fwhm_cm1"]
            )
        return x.round(2).tolist(), y.round(5).tolist()

    lo, hi, n = XPS_CURVE_RANGE
    x = np.linspace(lo, hi, n)
    y = np.zeros_like(x)
    for peak in peaks:
        fwhm = peak["fwhm_ev"]
        # Fraction is an area fraction; convert to a peak height for the trace.
        height = peak.get("fraction", 1.0) / fwhm
        y += _gaussian(x, peak["binding_energy_ev"], height, fwhm)
    peak_max = float(np.max(y))
    if peak_max > 0:
        y = y / peak_max
    return x.round(3).tolist(), y.round(5).tolist()


def _load(name: str) -> list[dict]:
    path = DATA_DIR / name
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


async def _upsert_all(
    db: AsyncIOMotorDatabase, collection: str, records: list[dict]
) -> tuple[int, int]:
    inserted = updated = 0
    for record in records:
        result = await db[collection].update_one(
            {"key": record["key"]}, {"$set": record}, upsert=True
        )
        if result.upserted_id is not None:
            inserted += 1
        elif result.modified_count:
            updated += 1
    return inserted, updated


async def seed(db: AsyncIOMotorDatabase) -> dict[str, tuple[int, int]]:
    summary: dict[str, tuple[int, int]] = {}

    spectra = _load("reference_spectra.json")
    for record in spectra:
        curve_x, curve_y = build_curve(record)
        record["curve_x"] = curve_x
        record["curve_y"] = curve_y
        record["synthetic_curve"] = True
    summary["reference_spectra"] = await _upsert_all(db, "reference_spectra", spectra)

    summary["reference_properties"] = await _upsert_all(
        db, "reference_properties", _load("reference_properties.json")
    )

    products = _load("commercial_products.json")
    for record in products:
        record.setdefault("provenance", PRODUCER_PROVENANCE)
    summary["commercial_products"] = await _upsert_all(db, "commercial_products", products)

    summary["applications"] = await _upsert_all(db, "applications", _load("applications.json"))
    return summary


async def main() -> int:
    settings = get_settings()
    print(f"Seeding {settings.mongodb_db} at {settings.mongodb_uri.split('@')[-1]}")
    try:
        db = await connect()
    except DatabaseUnavailable as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    try:
        summary = await seed(db)
    finally:
        await disconnect()
    for collection, (inserted, updated) in summary.items():
        print(f"  {collection:24s} {inserted:3d} inserted, {updated:3d} updated")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
