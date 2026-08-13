from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, status

from app.deps import CurrentUser, Database, parse_object_id
from app.models.common import utcnow
from app.models.sample import Sample, SampleCreate, SampleUpdate
from app.storage import get_storage

router = APIRouter(prefix="/api/samples", tags=["samples"])


async def get_owned_sample(db, sample_id: str, user_id: str) -> dict:
    oid = parse_object_id(sample_id, "sample id")
    document = await db.samples.find_one({"_id": oid, "user_id": user_id})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample not found.")
    return document


@router.post("", response_model=Sample, status_code=status.HTTP_201_CREATED)
async def create_sample(payload: SampleCreate, user: CurrentUser, db: Database) -> Sample:
    now = utcnow()
    document = {
        **payload.model_dump(),
        "user_id": user.id,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.samples.insert_one(document)
    document["_id"] = result.inserted_id
    return Sample.from_mongo(document)


@router.get("", response_model=list[Sample])
async def list_samples(
    user: CurrentUser,
    db: Database,
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
) -> list[Sample]:
    cursor = (
        db.samples.find({"user_id": user.id}).sort("created_at", -1).skip(skip).limit(limit)
    )
    return [Sample.from_mongo(doc) async for doc in cursor]


@router.get("/{sample_id}", response_model=Sample)
async def get_sample(sample_id: str, user: CurrentUser, db: Database) -> Sample:
    return Sample.from_mongo(await get_owned_sample(db, sample_id, user.id))


@router.patch("/{sample_id}", response_model=Sample)
async def update_sample(
    sample_id: str, payload: SampleUpdate, user: CurrentUser, db: Database
) -> Sample:
    document = await get_owned_sample(db, sample_id, user.id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return Sample.from_mongo(document)
    changes["updated_at"] = utcnow()
    await db.samples.update_one({"_id": document["_id"]}, {"$set": changes})
    return Sample.from_mongo(await db.samples.find_one({"_id": document["_id"]}))


@router.delete("/{sample_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sample(sample_id: str, user: CurrentUser, db: Database) -> None:
    document = await get_owned_sample(db, sample_id, user.id)
    sample_oid: ObjectId = document["_id"]

    # Remove the stored raw files before the metadata that points at them, so a
    # failure here cannot orphan objects in the bucket.
    storage = get_storage()
    async for spectrum in db.spectra.find({"sample_id": sample_id}):
        key = spectrum.get("storage_key")
        if key:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001 - deletion is best-effort
                pass

    await db.spectra.delete_many({"sample_id": sample_id})
    await db.analysis_reports.delete_many({"sample_id": sample_id})
    await db.samples.delete_one({"_id": sample_oid})
