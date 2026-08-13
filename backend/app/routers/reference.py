from fastapi import APIRouter, Query

from app.deps import Database

router = APIRouter(prefix="/api/reference", tags=["reference"])


def _clean(records: list[dict]) -> list[dict]:
    for record in records:
        record.pop("_id", None)
    return records


@router.get("/spectra")
async def list_reference_spectra(
    db: Database,
    technique: str | None = Query(None, pattern="^(raman|xps)$"),
    material_class: str | None = None,
    include_curves: bool = Query(True, description="Include the synthesised overlay traces"),
) -> list[dict]:
    query: dict = {}
    if technique:
        query["technique"] = technique
    if material_class:
        query["material_class"] = material_class
    projection = None if include_curves else {"curve_x": 0, "curve_y": 0}
    return _clean(await db.reference_spectra.find(query, projection).to_list(500))


@router.get("/properties")
async def list_reference_properties(db: Database) -> list[dict]:
    return _clean(await db.reference_properties.find({}).to_list(500))


@router.get("/applications")
async def list_applications(db: Database) -> list[dict]:
    return _clean(await db.applications.find({}).to_list(200))


@router.get("/products")
async def list_products(
    db: Database,
    form: str | None = None,
    producer: str | None = None,
) -> list[dict]:
    query: dict = {}
    if form:
        query["form"] = form
    if producer:
        query["producer"] = producer
    return _clean(await db.commercial_products.find(query).sort("producer", 1).to_list(1000))
