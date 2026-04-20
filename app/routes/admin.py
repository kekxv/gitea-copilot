from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Admin
from ..auth import admin as admin_auth
from ..schemas import AdminLoginRequest
from ..utils.security import get_or_create_secret_key
from ..utils.audit import log_admin_action
from jose import jwt
from datetime import datetime, timedelta
import os

router = APIRouter()

def get_secret_key() -> str:
    """Get SECRET_KEY, initializing it if necessary."""
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        # Initialize secret key if not set
        secret_key = get_or_create_secret_key()
    return secret_key

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, get_secret_key(), algorithm=ALGORITHM)
    return encoded_jwt

@router.post("/login")
async def login(request: Request, login_data: AdminLoginRequest, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    
    try:
        admin = admin_auth.verify_admin_credentials(db, login_data.username, login_data.password)
        if not admin_auth.verify_admin_totp(admin, login_data.otp_code):
            # Log failed login (wrong TOTP)
            log_admin_action(
                db=db,
                action="ADMIN_LOGIN",
                username=login_data.username,
                ip_address=client_ip,
                status="FAILURE",
                details={"reason": "invalid_totp"}
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid TOTP code",
                headers={"WWW-Authenticate": "Unauthorized"},
            )

        access_token = create_access_token(data={"sub": admin.username})
        
        # Log successful login
        log_admin_action(
            db=db,
            action="ADMIN_LOGIN",
            username=admin.username,
            ip_address=client_ip,
            status="SUCCESS"
        )
        
        return {"access_token": access_token, "token_type": "bearer"}
        
    except HTTPException as e:
        if e.status_code == 401 and "Invalid TOTP" not in e.detail:
            # Log failed login (wrong credentials)
            log_admin_action(
                db=db,
                action="ADMIN_LOGIN",
                username=login_data.username,
                ip_address=client_ip,
                status="FAILURE",
                details={"reason": "invalid_credentials"}
            )
        raise

@router.get("/me")
async def get_me(token: str):
    # In a real app, we'd verify the token and return the admin
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return {"username": username}
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
