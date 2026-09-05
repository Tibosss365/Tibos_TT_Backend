"""
Org-wide company profile + system settings (singleton row).

  GET /admin/company-settings   → any logged-in user (sidebar branding,
                                   date/timezone formatting, session-timeout
                                   policy all depend on this for every role)
  PUT /admin/company-settings   → admin only
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.company_settings import CompanySettings
from app.models.user import User
from app.schemas.company_settings import CompanySettingsOut, CompanySettingsUpdate

router = APIRouter(prefix="/admin/company-settings", tags=["company-settings"])


async def _get_or_create(db: AsyncSession) -> CompanySettings:
    result = await db.execute(select(CompanySettings).limit(1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = CompanySettings(id=1)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


@router.get("", response_model=CompanySettingsOut)
async def get_company_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await _get_or_create(db)


@router.put("", response_model=CompanySettingsOut)
async def update_company_settings(
    body: CompanySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    settings = await _get_or_create(db)
    for key, val in body.model_dump(exclude_unset=True).items():
        setattr(settings, key, val)
    await db.commit()
    await db.refresh(settings)
    return settings
