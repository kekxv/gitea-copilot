import httpx
from sqlalchemy.orm import Session
from ..database import SessionLocal
from ..models import GiteaAccount, GiteaInstance
from datetime import datetime, timedelta
import logging
from .notification_poller import run_polling_task

from ..utils.encryption import decrypt_sensitive_value

logger = logging.getLogger("uvicorn.error")


async def refresh_token(account: GiteaAccount, instance: GiteaInstance) -> bool:
    """Attempt to refresh an expired token."""
    if not account.refresh_token:
        logger.warning(f"Account {account.id} has no refresh token")
        return False

    token_url = f"{instance.url.rstrip('/')}/login/oauth/access_token"
    
    # Decrypt client secret before sending to Gitea
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
                logger.error(f"Token refresh failed for account {account.id}: status {response.status_code}")
                return False

            token_data = response.json()
            account.access_token = token_data.get("access_token")
            account.refresh_token = token_data.get("refresh_token")

            expires_in = token_data.get("expires_in")
            if expires_in:
                account.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)

            return True

    except Exception as e:
        logger.error(f"Token refresh error for account {account.id}: {e}")
        return False


def check_and_refresh_tokens():
    """Scheduled task to check and refresh expired tokens."""
    db = SessionLocal()
    try:
        # Find accounts with tokens expiring within the next 10 minutes
        threshold = datetime.utcnow() + timedelta(minutes=10)

        expiring_accounts = db.query(GiteaAccount).filter(
            GiteaAccount.token_expires_at <= threshold,
            GiteaAccount.refresh_token.isnot(None)
        ).all()

        logger.info(f"Found {len(expiring_accounts)} accounts with expiring tokens")

        for account in expiring_accounts:
            instance = db.query(GiteaInstance).filter(
                GiteaInstance.id == account.instance_id
            ).first()

            if not instance:
                logger.warning(f"Instance {account.instance_id} not found for account {account.id}")
                continue

            import asyncio
            success = asyncio.run(refresh_token(account, instance))

            if success:
                logger.info(f"Successfully refreshed token for account {account.id}")
                db.commit()
            else:
                logger.warning(f"Failed to refresh token for account {account.id}")

    except Exception as e:
        logger.error(f"Token check task error: {e}")
    finally:
        db.close()


def start_scheduler():
    """Start the APScheduler for token refresh and notification polling."""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    
    # Token refresh every 5 minutes
    scheduler.add_job(check_and_refresh_tokens, 'interval', minutes=5)
    
    # Notification polling every 1 minute
    scheduler.add_job(run_polling_task, 'interval', minutes=1)
    
    scheduler.start()
    logger.info("Background scheduler started (Token Refresh + Notification Polling)")

    return scheduler