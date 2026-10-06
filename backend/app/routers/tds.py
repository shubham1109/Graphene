"""TDS application finder: type in a datasheet, get the applications."""

import json

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.analysis.tds_match import build_profiles, match_applications, parameter_metadata
from app.deps import CurrentUser, Database, parse_object_id
from app.models.common import utcnow
from app.models.tds import ApplicationProfile, TdsMatchRecord, TdsMatchReport, TdsSpecInput
from seed.seed import DATA_DIR, TDS_PDF_DIR

MY_TDS_PATH = DATA_DIR / "my_tds.json"

router = APIRouter(prefix="/api/tds", tags=["tds"])


def _clean(records: list[dict]) -> list[dict]:
    for record in records:
        record.pop("_id", None)
    return records


async def _load(db) -> tuple[list[dict], list[dict], list[dict]]:
    taxonomy = _clean(await db.application_taxonomy.find({}).to_list(100))
    products = _clean(await db.application_products.find({}, {"datasheet_text": 0}).to_list(2000))
    market = _clean(await db.market_reference.find({}).to_list(5000))
    return taxonomy, products, market


@router.get("/parameters")
async def list_parameters() -> list[dict]:
    """The spec fields the finder understands, with labels, units and hints."""
    return parameter_metadata()


@router.get("/template")
async def get_my_tds_template() -> dict:
    """The user's own TDS as transcribed, used to prefill the finder form."""
    if not MY_TDS_PATH.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No TDS template on file.")
    return json.loads(MY_TDS_PATH.read_text(encoding="utf-8"))


@router.post("/preview", response_model=TdsMatchReport)
async def preview_tds(spec: TdsSpecInput, user: CurrentUser, db: Database) -> TdsMatchReport:
    """Match without saving, so the form can re-rank live as values are edited."""
    taxonomy, products, market = await _load(db)
    if not taxonomy or not products:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The application database is not seeded. Run `python -m seed.seed`.",
        )
    return match_applications(spec, taxonomy, products, market)


@router.get("/applications", response_model=list[ApplicationProfile])
async def list_application_profiles(db: Database) -> list[ApplicationProfile]:
    """Each application with the spec envelope the market currently covers."""
    taxonomy, products, _ = await _load(db)
    return build_profiles(taxonomy, products)


@router.get("/products")
async def list_application_products(
    db: Database,
    application: str | None = Query(None, description="Filter by application tag"),
    company: str | None = None,
    include_text: bool = Query(False, description="Include the extracted datasheet text"),
) -> list[dict]:
    query: dict = {}
    if application:
        query["application_tags"] = application
    if company:
        query["company"] = company
    projection = None if include_text else {"datasheet_text": 0}
    return _clean(await db.application_products.find(query, projection).sort("company", 1).to_list(2000))


@router.get("/datasheets/{filename}")
async def get_datasheet(filename: str, db: Database) -> FileResponse:
    """Serve a vendor TDS PDF that is referenced by a product record."""
    record = await db.application_products.find_one({"datasheet_file": filename}, {"_id": 1})
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Datasheet not found.")
    path = (TDS_PDF_DIR / filename).resolve()
    if not path.is_file() or not path.is_relative_to(TDS_PDF_DIR.resolve()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Datasheet file is missing.")
    return FileResponse(path, media_type="application/pdf", filename=filename)


@router.post("/match", response_model=TdsMatchRecord, status_code=status.HTTP_201_CREATED)
async def match_tds(spec: TdsSpecInput, user: CurrentUser, db: Database) -> TdsMatchRecord:
    taxonomy, products, market = await _load(db)
    if not taxonomy or not products:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The application database is not seeded. Run `python -m seed.seed`.",
        )
    report: TdsMatchReport = match_applications(spec, taxonomy, products, market)
    document = {
        "user_id": user.id,
        "created_at": utcnow(),
        "input": spec.model_dump(),
        "report": report.model_dump(),
    }
    result = await db.tds_matches.insert_one(document)
    document["_id"] = result.inserted_id
    return TdsMatchRecord.from_mongo(document)


@router.get("/matches", response_model=list[TdsMatchRecord])
async def list_matches(
    user: CurrentUser, db: Database, limit: int = Query(20, ge=1, le=100)
) -> list[TdsMatchRecord]:
    cursor = db.tds_matches.find({"user_id": user.id}).sort("created_at", -1).limit(limit)
    return [TdsMatchRecord.from_mongo(doc) async for doc in cursor]


@router.get("/matches/{match_id}", response_model=TdsMatchRecord)
async def get_match(match_id: str, user: CurrentUser, db: Database) -> TdsMatchRecord:
    oid = parse_object_id(match_id, "match id")
    document = await db.tds_matches.find_one({"_id": oid, "user_id": user.id})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found.")
    return TdsMatchRecord.from_mongo(document)


@router.delete("/matches/{match_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_match(match_id: str, user: CurrentUser, db: Database) -> None:
    oid = parse_object_id(match_id, "match id")
    result = await db.tds_matches.delete_one({"_id": oid, "user_id": user.id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found.")
