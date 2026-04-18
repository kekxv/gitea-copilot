from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class AdminLoginRequest(BaseModel):
    username: str
    password: str
    otp_code: Optional[str] = None

class GiteaInstanceCreate(BaseModel):
    url: str
    client_id: str
    client_secret: str

class GiteaAccountCreate(BaseModel):
    instance_id: int
    gitea_user_id: str
    gitea_username: str
    access_token: str
    refresh_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None