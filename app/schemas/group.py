from pydantic import BaseModel, Field


class GroupOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    color: str
    is_builtin: bool

    model_config = {"from_attributes": True}


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    color: str = "#6B7280"


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    color: str | None = None
