import re
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.deps import get_current_user
from app.core.security import create_access_token, verify_password
from app.database import get_db
from app.models.user import User
from app.models.login_session import LoginSession
from app.schemas.user import LoginRequest, TokenResponse, UserOut, TOTPLoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_client_ip(request: Request) -> str | None:
    """Extract real client IP, respecting reverse-proxy headers."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return None


def _parse_browser(ua: str) -> str:
    if not ua:
        return "Unknown"
    if re.search(r"Edg/", ua):
        return "Edge"
    if re.search(r"OPR/|Opera/", ua):
        return "Opera"
    if re.search(r"Chrome/", ua):
        return "Chrome"
    if re.search(r"Firefox/", ua):
        return "Firefox"
    if re.search(r"Safari/", ua):
        return "Safari"
    return "Browser"


def _parse_os(ua: str) -> str:
    if not ua:
        return "Unknown"
    if re.search(r"Windows NT 1[01]\.", ua):
        return "Windows 10/11"
    if re.search(r"Windows NT", ua):
        return "Windows"
    if re.search(r"Mac OS X", ua):
        return "macOS"
    if re.search(r"Android", ua):
        return "Android"
    if re.search(r"iPhone|iPad", ua):
        return "iOS"
    if re.search(r"Linux", ua):
        return "Linux"
    return "Unknown OS"


async def _record_login_session(
    request: Request, user: User, db: AsyncSession
) -> LoginSession:
    """Insert a login session row and return it."""
    ua = request.headers.get("User-Agent", "")
    session = LoginSession(
        user_id=user.id,
        ip_address=_get_client_ip(request),
        user_agent=ua[:500] if ua else None,
        browser=_parse_browser(ua),
        os=_parse_os(ua),
        is_active=True,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == body.username))
    user: User | None = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    # Persist login session to DB
    session = await _record_login_session(request, user, db)

    token = create_access_token(str(user.id), extra={"role": user.role.value})
    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        client_ip=_get_client_ip(request),
        session_id=str(session.id),
        must_change_password=getattr(user, "must_change_password", False),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark the session as ended. Accepts optional session_id in the JSON body."""
    from datetime import datetime, timezone

    # Try to get session_id from request body (if provided by frontend)
    session_id_str: str | None = None
    try:
        body = await request.json()
        session_id_str = body.get("session_id")
    except Exception:
        pass

    if session_id_str:
        import uuid as _uuid
        try:
            sid = _uuid.UUID(session_id_str)
            result = await db.execute(
                select(LoginSession).where(
                    LoginSession.id == sid,
                    LoginSession.user_id == current_user.id,
                )
            )
            sess = result.scalar_one_or_none()
            if sess:
                sess.is_active = False
                sess.logged_out_at = datetime.now(timezone.utc)
                await db.commit()
        except Exception:
            pass  # don't fail logout if session update errors

    return


@router.post("/login/2fa", response_model=TokenResponse)
async def login_with_totp(
    request: Request,
    body: TOTPLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Second-step login for accounts that have TOTP enabled.
    Accepts either a 6-digit TOTP code or an 8-char backup code.
    """
    from app.services.totp_service import verify_code, consume_backup_code

    result = await db.execute(select(User).where(User.username == body.username))
    user: User | None = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")
    if not user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="2FA is not enabled on this account")

    # Try TOTP code first
    if body.totp_code:
        if not verify_code(user.totp_secret, body.totp_code):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid 2FA code")
    elif body.backup_code:
        matched, updated_codes = consume_backup_code(user.totp_backup_codes or [], body.backup_code)
        if not matched:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid backup code")
        user.totp_backup_codes = updated_codes
        await db.commit()
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="totp_code or backup_code required")

    # Persist login session to DB
    session = await _record_login_session(request, user, db)

    token = create_access_token(str(user.id), extra={"role": user.role.value})
    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user),
        client_ip=_get_client_ip(request),
        session_id=str(session.id),
        must_change_password=getattr(user, "must_change_password", False),
    )
