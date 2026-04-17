"""
Groups CRUD router.

GET    /groups            → list all groups (ordered by name)
POST   /groups            → create a new group (admin only, slug auto-derived from name)
PATCH  /groups/{group_id} → update name / description / color (admin only)
DELETE /groups/{group_id} → delete a non-builtin group (admin only)
"""
import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.group import Group
from app.models.user import User
from app.schemas.group import GroupCreate, GroupOut, GroupUpdate

router = APIRouter(prefix="/groups", tags=["groups"])


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")[:80]


@router.get("", response_model=list[GroupOut])
async def list_groups(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return all groups ordered alphabetically."""
    result = await db.execute(select(Group).order_by(Group.name))
    return [GroupOut.model_validate(g) for g in result.scalars().all()]


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Create a new group. The slug/id is auto-derived from the name."""
    slug = _slugify(body.name)
    if not slug:
        raise HTTPException(status_code=400, detail="Cannot derive slug from name")

    existing = await db.execute(select(Group).where(Group.id == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Group '{slug}' already exists")

    group = Group(
        id=slug,
        name=body.name.strip(),
        description=body.description,
        color=body.color,
        is_builtin=False,
    )
    db.add(group)
    await db.flush()
    await db.refresh(group)
    return GroupOut.model_validate(group)


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: str,
    body: GroupUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Update a group's name, description, or color."""
    result = await db.execute(select(Group).where(Group.id == group_id))
    group: Group | None = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, val in update_data.items():
        setattr(group, key, val)

    await db.flush()
    await db.refresh(group)
    return GroupOut.model_validate(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Delete a group (built-in groups cannot be deleted)."""
    result = await db.execute(select(Group).where(Group.id == group_id))
    group: Group | None = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if group.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete built-in groups",
        )
    await db.delete(group)
