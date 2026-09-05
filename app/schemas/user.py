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
    password: str = Field(..., min_length=8)


class UserUpdate(BaseModel):
    name: str | None = None
    initials: str | None = None
    group: str | None = None
    username: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = None
    preferred_timezone: str | None = None


class UserOut(UserBase):
    id: uuid.UUID
    is_active: bool
    created_at: datetime
    totp_enabled: bool = False
    preferred_timezone: str = "UTC"

    model_config = {"from_attributes": True}


# ── TOTP / 2FA schemas ────────────────────────────────────────────────────────

class TOTPSetupOut(BaseModel):
    """Returned when 2FA setup is initiated — contains the provisioning URI."""
    provisioning_uri: str
    secret: str  # shown once so user can manually enter into authenticator


class TOTPVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class TOTPVerifyResponse(BaseModel):
    backup_codes: list[str]  # returned only on first enable


class TOTPDisableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class TOTPLoginRequest(BaseModel):
    """Second-step login when TOTP is enabled."""
    username: str
    password: str
    totp_code: str | None = None  # omit if using backup code
    backup_code: str | None = None


class UserSettingsUpdate(BaseModel):
    """Self-service profile updates (non-admin)."""
    name: str | None = None
    initials: str | None = None
    preferred_timezone: str | None = None
    current_password: str | None = None  # required when changing password
    new_password: str | None = None


class UserPublic(BaseModel):
    """Minimal user info returned in ticket/timeline contexts."""
    id: uuid.UUID
    name: str
    initials: str
    group: str
    role: UserRole

    model_config = {"from_attributes": True}


class AccountOwner(BaseModel):
    """An internal staff member who owns a customer account.

    ``user_id`` is set when the owner is an existing helpdesk user; it stays
    None for owners entered as a plain name + email. Either way ``email`` is
    what the ticket emails are CC'd to.
    """
    user_id: uuid.UUID | None = None
    name: str = Field(default="", max_length=150)
    email: str = Field(..., min_length=3, max_length=255)

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    client_ip: str | None = None       # returned so the frontend can display it in login history
    session_id: str | None = None      # DB login_session UUID — used by frontend to mark logout
    must_change_password: bool = False  # set by admin force-reset; frontend redirects to change-password page
