"""TOTP / 2-Factor Authentication helpers using pyotp."""
import secrets
import string

try:
    import pyotp
    _PYOTP_AVAILABLE = True
except ImportError:
    _PYOTP_AVAILABLE = False


_BACKUP_CODE_LENGTH = 8
_BACKUP_CODE_COUNT = 8
_ISSUER_NAME = "TibosTT"


def _require_pyotp() -> None:
    if not _PYOTP_AVAILABLE:
        raise RuntimeError(
            "pyotp is not installed. Run: pip install pyotp"
        )


def generate_secret() -> str:
    """Generate a new base32 TOTP secret."""
    _require_pyotp()
    return pyotp.random_base32()


def get_provisioning_uri(secret: str, username: str) -> str:
    """Return the otpauth:// URI for QR code generation."""
    _require_pyotp()
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=username, issuer_name=_ISSUER_NAME)


def verify_code(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code. Allows ±1 window for clock drift."""
    if not _PYOTP_AVAILABLE:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def generate_backup_codes() -> list[str]:
    """Generate a list of one-time backup codes (plain text, unhashed)."""
    alphabet = string.ascii_uppercase + string.digits
    codes = []
    for _ in range(_BACKUP_CODE_COUNT):
        code = "".join(secrets.choice(alphabet) for _ in range(_BACKUP_CODE_LENGTH))
        codes.append(code)
    return codes


def consume_backup_code(stored_codes: list[str], provided: str) -> tuple[bool, list[str]]:
    """
    Check if *provided* is in *stored_codes* (case-insensitive).
    Returns (matched, updated_codes_list) — the used code is removed.
    """
    upper = provided.upper().strip()
    for code in stored_codes:
        if code.upper() == upper:
            updated = [c for c in stored_codes if c.upper() != upper]
            return True, updated
    return False, stored_codes
