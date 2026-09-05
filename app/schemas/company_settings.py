from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class CompanySettingsUpdate(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    logo: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    session_timeout_minutes: Optional[int] = None


class CompanySettingsOut(BaseModel):
    name: str
    website: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    logo: Optional[str] = None
    language: str
    timezone: str
    session_timeout_minutes: int
    updated_at: datetime

    model_config = {"from_attributes": True}
