"""Short-lived in-memory cache of the authenticated user.

Every API request used to re-read the user row from the (distant) database just
to check the account is active. We keep the column values for a few seconds and
rebuild a detached copy for the request. Any ORM update/delete of a user clears
its entry at once; other worker processes catch up within TTL_SECONDS.
"""
import time
import uuid

from sqlalchemy import event, inspect
from sqlalchemy.orm import make_transient_to_detached

from app.models.user import User

TTL_SECONDS = 15
_MAX_ENTRIES = 500
_cache: dict[uuid.UUID, tuple[float, dict]] = {}


def remember(user: User) -> None:
    if len(_cache) >= _MAX_ENTRIES:
        _cache.clear()
    _cache[user.id] = (
        time.monotonic() + TTL_SECONDS,
        {attr.key: getattr(user, attr.key) for attr in inspect(User).column_attrs},
    )


def recall(user_id: uuid.UUID) -> User | None:
    entry = _cache.get(user_id)
    if entry is None:
        return None
    expires, columns = entry
    if expires < time.monotonic():
        _cache.pop(user_id, None)
        return None
    user = User(**columns)
    make_transient_to_detached(user)
    return user


def forget(user_id: uuid.UUID) -> None:
    _cache.pop(user_id, None)


@event.listens_for(User, "after_update")
@event.listens_for(User, "after_delete")
def _forget_on_change(mapper, connection, target):
    forget(target.id)
