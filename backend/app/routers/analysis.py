import asyncio

from fastapi import APIRouter, HTTPException, Query, status

from app.analysis.pipeline import build_report_payload
from app.analysis.raman import analyse_raman
from app.analysis.xps import AL_KALPHA, analyse_xps
from app.deps import CurrentUser, Database, parse_object_id
from app.models.analysis import AnalysisReport
from app.models.common import utcnow
from app.models.sample import SampleProperties
from app.models.spectrum import SpectrumMeta
from app.parsers.tabular import ParseError
from app.routers.samples import get_owned_sample
from app.routers.spectra import load_spectrum_arrays

router = APIRouter(prefix="/api", tags=["analysis"])


async def _load_reference_data(db) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    spectra = await db.reference_spectra.find({}, {"curve_x": 0, "curve_y": 0}).to_list(500)
    properties = await db.reference_properties.find({}).to_list(500)
    applications = await db.applications.find({}).to_list(200)
    products = await db.commercial_products.find({}).to_list(1000)
    for collection in (spectra, properties, applications, products):
        for record in collection:
            record.pop("_id", None)
    return spectra, properties, applications, products


def _run_engines(raman_doc: dict | None, xps_docs: dict[str, dict]):
    """CPU-bound spectroscopy. Executed off the event loop by the caller."""
    raman_result = None
    warnings: list[str] = []

    if raman_doc is not None:
        meta = SpectrumMeta.model_validate(raman_doc["meta"])
        x, y = load_spectrum_arrays(raman_doc)
        raman_result = analyse_raman(x, y, meta.excitation_nm, str(raman_doc["_id"]))

    xps_result = None
    if xps_docs:
        regions = {}
        ids = []
        photon_energy = AL_KALPHA
        for region, document in xps_docs.items():
            x, y = load_spectrum_arrays(document)
            regions[region] = (x, y)
            ids.append(str(document["_id"]))
            hint = (document.get("meta") or {}).get("instrument_hints", {}).get("source_energy_ev")
            if hint:
                try:
                    photon_energy = float(hint)
                except ValueError:
                    pass
        xps_result = analyse_xps(regions, photon_energy, ids)

    return raman_result, xps_result, warnings


@router.post(
    "/samples/{sample_id}/analyse",
    response_model=AnalysisReport,
    status_code=status.HTTP_201_CREATED,
)
async def analyse_sample(sample_id: str, user: CurrentUser, db: Database) -> AnalysisReport:
    sample = await get_owned_sample(db, sample_id, user.id)
    spectra = await db.spectra.find({"sample_id": sample_id}).sort("created_at", 1).to_list(200)
    if not spectra:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload at least one Raman or XPS spectrum before running an analysis.",
        )

    raman_docs = [s for s in spectra if s["technique"] == "raman"]
    # Later uploads win: re-uploading a region is how a user corrects a bad scan.
    xps_docs: dict[str, dict] = {}
    for document in spectra:
        if document["technique"] == "xps" and document.get("region") in {"survey", "c1s", "o1s"}:
            xps_docs[document["region"]] = document

    extra_warnings: list[str] = []
    if len(raman_docs) > 1:
        extra_warnings.append(
            f"{len(raman_docs)} Raman spectra are attached to this sample; the most "
            "recent one was analysed. Create separate samples to compare acquisitions."
        )

    try:
        raman_result, xps_result, warnings = await asyncio.to_thread(
            _run_engines, raman_docs[-1] if raman_docs else None, xps_docs
        )
    except ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A stored spectrum could not be re-read: {exc}",
        ) from exc

    reference_spectra, reference_properties, applications, products = await _load_reference_data(db)
    if not applications or not products:
        extra_warnings.append(
            "The reference library is empty or partially seeded. Run "
            "`python -m seed.seed` to load reference datasets and producer specs."
        )

    properties = SampleProperties.model_validate(sample.get("properties") or {})
    payload = await asyncio.to_thread(
        build_report_payload,
        raman_result,
        xps_result,
        properties,
        reference_spectra,
        reference_properties,
        applications,
        products,
    )
    payload["warnings"] = extra_warnings + warnings + payload["warnings"]

    document = {
        "user_id": user.id,
        "sample_id": sample_id,
        "sample_name": sample["name"],
        "created_at": utcnow(),
        **{
            key: (value.model_dump() if hasattr(value, "model_dump") else value)
            for key, value in payload.items()
        },
    }
    for key in ("scorecard", "rankings", "applications", "peers"):
        document[key] = [
            item.model_dump() if hasattr(item, "model_dump") else item for item in payload[key]
        ]

    result = await db.analysis_reports.insert_one(document)
    document["_id"] = result.inserted_id
    return AnalysisReport.from_mongo(document)


@router.get("/samples/{sample_id}/reports", response_model=list[AnalysisReport])
async def list_reports(
    sample_id: str,
    user: CurrentUser,
    db: Database,
    limit: int = Query(20, ge=1, le=100),
) -> list[AnalysisReport]:
    await get_owned_sample(db, sample_id, user.id)
    cursor = (
        db.analysis_reports.find({"sample_id": sample_id}).sort("created_at", -1).limit(limit)
    )
    return [AnalysisReport.from_mongo(doc) async for doc in cursor]


@router.get("/reports", response_model=list[AnalysisReport])
async def list_all_reports(
    user: CurrentUser,
    db: Database,
    limit: int = Query(50, ge=1, le=200),
) -> list[AnalysisReport]:
    cursor = db.analysis_reports.find({"user_id": user.id}).sort("created_at", -1).limit(limit)
    return [AnalysisReport.from_mongo(doc) async for doc in cursor]


@router.get("/reports/{report_id}", response_model=AnalysisReport)
async def get_report(report_id: str, user: CurrentUser, db: Database) -> AnalysisReport:
    oid = parse_object_id(report_id, "report id")
    document = await db.analysis_reports.find_one({"_id": oid, "user_id": user.id})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found.")
    return AnalysisReport.from_mongo(document)
