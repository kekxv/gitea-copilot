import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from ..models import GiteaInstance, GiteaAccount, OAuthState
import secrets
from datetime import datetime, timedelta
from typing import Optional
import logging
from ..utils.encryption import decrypt_sensitive_value, encrypt_sensitive_value

logger = logging.getLogger("uvicorn.error")


def create_oauth_state(instance_id: int, redirect_url: str, db: Session) -> str:
    """Create a unique state for OAuth flow and store it in database.
    
    Args:
        instance_id: The Gitea instance ID
        redirect_url: The OAuth redirect URL
        db: Database session
        
    Returns:
        str: The generated state token
    """
    state = secrets.token_urlsafe(32)
    
    oauth_state = OAuthState(
        state=state,
        instance_id=instance_id,
        redirect_url=redirect_url,
        created_at=datetime.utcnow()
    )
    db.add(oauth_state)
    db.commit()
    
    return state


def validate_oauth_state(state: str, db: Session) -> Optional[dict]:
    """Validate and return OAuth state data from database.
    
    Args:
        state: The state token to validate
        db: Database session
        
    Returns:
        dict with instance_id and redirect_url, or None if invalid/expired
    """
    oauth_state = db.query(OAuthState).filter(OAuthState.state == state).first()
    
    if not oauth_state:
        return None
    
    # State expires after 10 minutes
    if datetime.utcnow() - oauth_state.created_at > timedelta(minutes=10):
        db.delete(oauth_state)
        db.commit()
        return None
    
    # Return state data and delete it (one-time use)
    data = {
        "instance_id": oauth_state.instance_id,
        "redirect_url": oauth_state.redirect_url
    }
    
    db.delete(oauth_state)
    db.commit()
    
    return data


def get_gitea_instance(db: Session, instance_id: int) -> GiteaInstance:
    """Get Gitea instance by ID."""
    instance = db.query(GiteaInstance).filter(GiteaInstance.id == instance_id).first()
    if not instance:
        raise HTTPException(status_code=404, detail="Gitea instance not found")
    return instance


async def exchange_code_for_token(
    instance: GiteaInstance,
    code: str,
    redirect_uri: str
) -> dict:
    """Exchange OAuth code for access token."""
    token_url = f"{instance.url.rstrip('/')}/login/oauth/access_token"

    # Decrypt client secret before using
    client_secret = decrypt_sensitive_value(instance.client_secret_encrypted)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": instance.client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri
            },
            headers={"Accept": "application/json"}
        )

        if response.status_code != 200:
            logger.error(f"Token exchange failed: status={response.status_code}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token exchange failed"
            )

        return response.json()


async def get_gitea_user_info(instance: GiteaInstance, access_token: str) -> dict:
    """Get user info from Gitea using access token."""
    user_url = f"{instance.url.rstrip('/')}/api/v1/user"

    async with httpx.AsyncClient() as client:
        response = await client.get(
            user_url,
            headers={"Authorization": f"token {access_token}"}
        )

        if response.status_code != 200:
            logger.error(f"Failed to get user info: status={response.status_code}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to get user info"
            )

        return response.json()


def create_or_update_account(
    db: Session,
    instance: GiteaInstance,
    gitea_user_id: str,
    gitea_username: str,
    access_token: str,
    refresh_token: Optional[str] = None,
    expires_in: Optional[int] = None
) -> GiteaAccount:
    """Create or update Gitea account."""
    # Check if account already exists for this instance
    account = db.query(GiteaAccount).filter(
        GiteaAccount.instance_id == instance.id
    ).first()

    token_expires_at = None
    if expires_in:
        token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

    if account:
        account.gitea_user_id = gitea_user_id
        account.gitea_username = gitea_username
        account.access_token = access_token
        account.refresh_token = refresh_token
        account.token_expires_at = token_expires_at
    else:
        account = GiteaAccount(
            instance_id=instance.id,
            gitea_user_id=gitea_user_id,
            gitea_username=gitea_username,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at
        )
        db.add(account)

    db.commit()
    db.refresh(account)
    return account


def get_oauth_redirect_url(instance: GiteaInstance, state: str, redirect_uri: str) -> str:
    """Generate OAuth redirect URL for Gitea."""
    base_url = instance.url.rstrip('/')
    return (
        f"{base_url}/login/oauth/authorize"
        f"?client_id={instance.client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&state={state}"
    )


async def refresh_access_token(
    db: Session,
    account: GiteaAccount,
    instance: GiteaInstance
) -> bool:
    """Refresh access token using refresh token."""
    if not account.refresh_token:
        return False

    token_url = f"{instance.url.rstrip('/')}/login/oauth/access_token"

    # Decrypt client secret before using
    client_secret = decrypt_sensitive_value(instance.client_secret_encrypted)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": account.refresh_token,
                    "client_id": instance.client_id,
                    "client_secret": client_secret
                },
                headers={"Accept": "application/json"}
            )

            if response.status_code != 200:
                logger.error(f"Token refresh failed: status={response.status_code}")
                return False

            token_data = response.json()
            account.access_token = token_data.get("access_token")
            if token_data.get("refresh_token"):
                account.refresh_token = token_data.get("refresh_token")
            if token_data.get("expires_in"):
                account.token_expires_at = datetime.utcnow() + timedelta(seconds=token_data.get("expires_in"))

            db.commit()
            return True

    except Exception as e:
        logger.error(f"Token refresh error: {e}")
        return False