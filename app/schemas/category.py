import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _to_slug(name: str) -> str:
    """Convert a display name to a URL-safe lowercase slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:80]


class CategoryOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    color: str
    description: str | None
    is_builtin: bool
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field(default="#6B7280", pattern=r"^#[0-9A-Fa-f]{6}$")
    description: str | None = None
    sort_order: int = 100

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be blank")
        return v.strip()

    @property
    def slug(self) -> str:
        return _to_slug(self.name)


class CategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    description: str | None = None
    sort_order: int | None = None
