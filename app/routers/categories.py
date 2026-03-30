"""
Category CRUD router.

GET    /categories          → list all categories (ordered by sort_order)
POST   /categories          → create a new custom category (admin only)
PATCH  /categories/{id}     → update name/color/description (admin only)
DELETE /categories/{id}     → delete a custom category (built-in protected)
POST   /categories/reorder  → update sort_order for multiple categories
"""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.category import Category
from app.models.user import User
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["categories"])


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")[:80]


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Category).order_by(Category.sort_order, Category.name)
    )
    return [CategoryOut.model_validate(c) for c in result.scalars().all()]


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    slug = _slugify(body.name)
    if not slug:
        raise HTTPException(status_code=400, detail="Cannot derive slug from name")

    existing = await db.execute(select(Category).where(Category.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Category slug '{slug}' already exists")

    cat = Category(
        slug=slug,
        name=body.name.strip(),
        color=body.color,
        description=body.description,
        sort_order=body.sort_order,
        is_builtin=False,
    )
    db.add(cat)
    await db.flush()
    await db.refresh(cat)
    return CategoryOut.model_validate(cat)


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: uuid.UUID,
    body: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(Category).where(Category.id == category_id))
    cat: Category | None = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, val in update_data.items():
        setattr(cat, key, val)

    await db.flush()
    await db.refresh(cat)
    return CategoryOut.model_validate(cat)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(Category).where(Category.id == category_id))
    cat: Category | None = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if cat.is_builtin:
        raise HTTPException(status_code=403, detail="Built-in categories cannot be deleted")
    await db.delete(cat)


@router.post("/reorder", response_model=list[CategoryOut])
async def reorder_categories(
    order: list[dict],   # [{"id": uuid, "sort_order": int}, ...]
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Batch-update sort_order for drag-and-drop reordering."""
    updated: list[Category] = []
    for item in order:
        cat_id = item.get("id")
        new_order = item.get("sort_order")
        if not cat_id or new_order is None:
            continue
        result = await db.execute(select(Category).where(Category.id == uuid.UUID(str(cat_id))))
        cat = result.scalar_one_or_none()
        if cat:
            cat.sort_order = int(new_order)
            updated.append(cat)
    await db.flush()
    return [CategoryOut.model_validate(c) for c in updated]
