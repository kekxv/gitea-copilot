from fastapi import HTTPException, status, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Admin
from .utils import verify_password, hash_password
import pyotp


def verify_admin_credentials(db: Session, username: str, password: str) -> Admin:
    admin = db.query(Admin).filter(Admin.username == username).first()
    if not admin or not verify_password(password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Unauthorized"},
        )
    return admin


def verify_admin_totp(admin: Admin, otp_code: str) -> bool:
    """Verify TOTP code if TOTP is enabled for the admin."""
    if not admin.totp_enabled or not admin.totp_secret:
        return True  # TOTP not enabled, skip verification
    totp = pyotp.TOTP(admin.totp_secret)
    return totp.verify(otp_code, valid_window=1)


def generate_totp_secret() -> str:
    """Generate a new TOTP secret."""
    return pyotp.random_base32()


def get_totp_uri(username: str, secret: str) -> str:
    """Generate TOTP URI for QR code."""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="GiteaCopilot")


def change_admin_password(db: Session, admin: Admin, new_password: str) -> None:
    """Change admin password."""
    admin.password_hash = hash_password(new_password)
    db.commit()


def enable_admin_totp(db: Session, admin: Admin) -> tuple[str, str]:
    """Enable TOTP for admin and return secret and URI."""
    secret = generate_totp_secret()
    admin.totp_secret = secret
    admin.totp_enabled = True
    db.commit()
    uri = get_totp_uri(admin.username, secret)
    return secret, uri


def disable_admin_totp(db: Session, admin: Admin) -> None:
    """Disable TOTP for admin."""
    admin.totp_enabled = False
    admin.totp_secret = None
    db.commit()


def is_totp_enabled(admin: Admin) -> bool:
    """Check if TOTP is enabled for admin."""
    return admin.totp_enabled and admin.totp_secret is not None