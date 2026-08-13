from datetime import datetime, timezone
from typing import Annotated, Any

from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, ConfigDict

# Mongo _id serialised as a plain string on the wire.
PyObjectId = Annotated[str, BeforeValidator(lambda v: str(v))]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def oid(value: str) -> ObjectId:
    """Parse a client-supplied id, raising ValueError if malformed."""
    if not ObjectId.is_valid(value):
        raise ValueError(f"Malformed object id: {value!r}")
    return ObjectId(value)


class MongoModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    @classmethod
    def from_mongo(cls, doc: dict[str, Any] | None):
        if doc is None:
            return None
        doc = dict(doc)
        if "_id" in doc:
            doc["id"] = str(doc.pop("_id"))
        return cls.model_validate(doc)
