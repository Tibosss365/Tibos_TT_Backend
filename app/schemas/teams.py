from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TeamsConfigUpdate(BaseModel):
    enabled: bool = False
    tenant_id: Optional[str] = None
    app_id: Optional[str] = None
    app_password: Optional[str] = None   # write-only; never echoed back
    default_category: str = "other"
    default_priority: str = "medium"


class TeamsConfigOut(BaseModel):
    enabled: bool
    tenant_id: Optional[str]
    app_id: Optional[str]
    app_password_set: bool = False       # True if a secret is stored, raw value never returned
    default_category: str
    default_priority: str
    messaging_endpoint: str              # e.g. https://tibos-tt-api.azurewebsites.net/api/messages
    updated_at: datetime

    model_config = {"from_attributes": True}


class TeamsTestResult(BaseModel):
    ok: bool
    message: str
