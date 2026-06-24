import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.core.deps import get_current_user, require_admin
from app.core.security import hash_password
from app.database import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[UserOut])
async def list_agents(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(User).order_by(User.name))
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(
        name=body.name,
        initials=body.initials,
        group=body.group,
        username=body.username,
        hashed_password=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/{agent_id}", response_model=UserOut)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(User).where(User.id == agent_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Agent not found")
    return UserOut.model_validate(user)


@router.patch("/{agent_id}", response_model=UserOut)
async def update_agent(
    agent_id: uuid.UUID,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == agent_id))
    user: User | None = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = body.model_dump(exclude_unset=True)

    if "username" in update_data and update_data["username"] != user.username:
        conflict = await db.execute(select(User).where(User.username == update_data["username"]))
        if conflict.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Username already taken")

    if "name" in update_data and "initials" not in update_data:
        words = update_data["name"].split()
        update_data["initials"] = "".join(w[0] for w in words).upper()[:4]

    if "password" in update_data:
        update_data["hashed_password"] = hash_password(update_data.pop("password"))
    for key, val in update_data.items():
        setattr(user, key, val)

    await db.flush()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if agent_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    result = await db.execute(select(User).where(User.id == agent_id))
    user: User | None = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Agent not found")

    # ── Permanent (hard) delete ───────────────────────────────────────────────
    # The agent may be referenced by tickets (assignee/requester), timeline
    # authors, notifications, sessions, etc. The prod schema predates the
    # migration chain, so its actual ON DELETE rules can't be trusted — a plain
    # DELETE raises a foreign-key violation and rolls back (which is why the
    # agent kept reappearing). So we introspect *every* FK that points at
    # users.id and detach those rows first: nullable columns are set NULL
    # (e.g. a ticket just becomes unassigned), non-nullable columns have their
    # rows deleted (e.g. notifications, login sessions). Then the user row is
    # removed for good.
    refs = await db.execute(text("""
        SELECT tc.table_name, kcu.column_name, col.is_nullable
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema   = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
         AND tc.table_schema   = ccu.table_schema
        JOIN information_schema.columns col
          ON col.table_schema = tc.table_schema
         AND col.table_name   = tc.table_name
         AND col.column_name  = kcu.column_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND ccu.table_name  = 'users'
          AND ccu.column_name = 'id'
    """))

    for table_name, column_name, is_nullable in refs.fetchall():
        if is_nullable == 'YES':
            await db.execute(
                text(f'UPDATE "{table_name}" SET "{column_name}" = NULL WHERE "{column_name}" = :uid::uuid'),
                {"uid": str(agent_id)},
            )
        else:
            await db.execute(
                text(f'DELETE FROM "{table_name}" WHERE "{column_name}" = :uid::uuid'),
                {"uid": str(agent_id)},
            )

    await db.delete(user)
    await db.flush()
