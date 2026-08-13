from fastapi import APIRouter, HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.deps import CurrentUser, Database
from app.models.common import utcnow
from app.models.user import TokenResponse, UserCreate, UserLogin, UserPublic
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: Database) -> TokenResponse:
    now = utcnow()
    document = {
        "email": payload.email.lower(),
        "password_hash": hash_password(payload.password),
        "full_name": payload.full_name,
        "organisation": payload.organisation,
        "created_at": now,
    }
    try:
        result = await db.users.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        ) from None

    document["_id"] = result.inserted_id
    token, expires_in = create_access_token(str(result.inserted_id))
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserPublic.from_mongo(document),
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: UserLogin, db: Database) -> TokenResponse:
    document = await db.users.find_one({"email": payload.email.lower()})
    # Verify against a dummy hash when the user is missing so that a wrong
    # email and a wrong password take the same time to answer.
    stored = document["password_hash"] if document else "$2b$12$" + "." * 53
    if not verify_password(payload.password, stored) or document is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    token, expires_in = create_access_token(str(document["_id"]))
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserPublic.from_mongo(document),
    )


@router.get("/me", response_model=UserPublic)
async def me(user: CurrentUser) -> UserPublic:
    return user
