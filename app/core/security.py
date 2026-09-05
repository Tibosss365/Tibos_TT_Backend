import re
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings

settings = get_settings()


def validate_password_strength(password: str) -> None:
    """Raise ValueError with a user-facing message if the password is too weak.

    Only call this on write paths (an agent/admin being created, a password
    being changed) — never on login/verify, so existing accounts with an
    older, weaker password can still log in (and are prompted to change it
    going forward, not locked out retroactively).
    """
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    if not re.search(r"[A-Za-z]", password):
        raise ValueError("Password must contain at least one letter.")
    if not re.search(r"[0-9]", password):
        raise ValueError("Password must contain at least one number.")


def hash_password(plain: str) -> str:
    # bcrypt has a 72-byte limit; encode to UTF-8 and truncate explicitly
    secret = plain.encode("utf-8")[:72]
    return bcrypt.hashpw(secret, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    secret = plain.encode("utf-8")[:72]
    return bcrypt.checkpw(secret, hashed.encode("utf-8"))


def create_access_token(subject: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict = {"sub": subject, "iat": now, "exp": expire}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        raise ValueError("Invalid or expired token")
