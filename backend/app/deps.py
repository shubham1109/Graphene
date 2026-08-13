from typing import Annotated

from bson import ObjectId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_db
from app.models.common import oid
from app.models.user import UserPublic
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncIOMotorDatabase, Depends(get_db)],
) -> UserPublic:
    if creds is None:
        raise CREDENTIALS_ERROR
    subject = decode_access_token(creds.credentials)
    if subject is None or not ObjectId.is_valid(subject):
        raise CREDENTIALS_ERROR
    doc = await db.users.find_one({"_id": ObjectId(subject)})
    if doc is None:
        raise CREDENTIALS_ERROR
    return UserPublic.from_mongo(doc)


CurrentUser = Annotated[UserPublic, Depends(current_user)]
Database = Annotated[AsyncIOMotorDatabase, Depends(get_db)]


def parse_object_id(value: str, what: str = "id") -> ObjectId:
    try:
        return oid(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {what}."
        ) from None
