from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.common import MongoModel


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: str | None = Field(default=None, max_length=200)
    organisation: str | None = Field(default=None, max_length=200)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserPublic(MongoModel):
    id: str
    email: EmailStr
    full_name: str | None = None
    organisation: str | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic
