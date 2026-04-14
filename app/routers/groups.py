from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/groups", tags=["groups"])


@router.get("")
async def list_groups(
    _: User = Depends(get_current_user),
):
    """
    Groups are managed client-side (no DB backing yet).
    Returns an empty list so the frontend falls back to its built-in defaults.
    """
    return []
