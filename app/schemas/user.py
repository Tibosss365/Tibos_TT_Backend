import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.user import UserRole


class UserBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    initials: str = Field(..., min_length=1, max_length=4)
    group: str = Field(default="")
    username: str = Field(..., min_length=2, max_length=50)
    role: UserRole = UserRole.technician


class UserCreate(UserBase):
    password: str = Field(..., min_length=4)


class UserUpdate(BaseModel):
    name: str | None = None
    initials: str | None = None
    group: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = None


class UserOut(UserBase):
    id: uuid.UUID
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPublic(BaseModel):
    """Minimal user info returned in ticket/timeline contexts."""
    id: uuid.UUID
    name: str
    initials: str
    group: str
    role: UserRole

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
