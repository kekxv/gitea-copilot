import logging
import sys

# Configure logging to use Uvicorn's style
def setup_logging():
    # Force root logger level and handlers
    root_logger = logging.getLogger()

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Standard Uvicorn-style format: "LEVEL:    Message"
    fmt = "%(levelname)-8s %(message)s"

    # Try to find uvicorn's specific handlers if already initialized
    uvicorn_error = logging.getLogger("uvicorn.error")
    if uvicorn_error.handlers:
        for handler in uvicorn_error.handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(uvicorn_error.level)
    else:
        # Fallback create a matching handler
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(fmt)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

setup_logging()
logger = logging.getLogger("uvicorn.error")

# Now import the rest
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .database import engine, Base, SessionLocal
from .routes import admin, pages
from .tasks import start_scheduler
from .utils.security import get_or_create_secret_key, validate_secret_key
from .database_migration import apply_migrations
import os
import httpx


def init_token_mode_account(db) -> bool:
    """Initialize Token mode Gitea account from environment variables.

    Returns True if Token mode account was initialized/updated.
    """
    gitea_url = os.getenv("GITEA_URL")
    gitea_token = os.getenv("GITEA_TOKEN")

    if not gitea_url or not gitea_token:
        return False

    gitea_url = gitea_url.rstrip("/")
    logger.info(f"Token mode enabled: GITEA_URL={gitea_url}")

    from .models import GiteaInstance, GiteaAccount
    from .utils.encryption import encrypt_sensitive_value

    # Check if instance already exists for this URL
    instance = db.query(GiteaInstance).filter(GiteaInstance.url == gitea_url).first()

    # Check for OAuth client credentials from env
    client_id = os.getenv("GITEA_CLIENT_ID", "")
    client_secret = os.getenv("GITEA_CLIENT_SECRET", "")

    if not instance:
        instance = GiteaInstance(
            url=gitea_url,
            client_id=client_id,
            client_secret_encrypted=encrypt_sensitive_value(client_secret) if client_secret else ""
        )
        db.add(instance)
        db.commit()
        db.refresh(instance)
        logger.info(f"Created Gitea instance for Token mode: id={instance.id}")

    # Check if token-mode account already exists
    account = db.query(GiteaAccount).filter(
        GiteaAccount.instance_id == instance.id,
        GiteaAccount.auth_mode == "token"
    ).first()

    if account:
        # Update token if changed
        if account.access_token != gitea_token:
            account.access_token = gitea_token
            db.commit()
            logger.info(f"Updated token for Token mode account: @{account.gitea_username}")
        return True

    # Fetch user info from Gitea to get username and user_id
    try:
        with httpx.Client() as client:
            response = client.get(
                f"{gitea_url}/api/v1/user",
                headers={"Authorization": f"token {gitea_token}"}
            )
            if response.status_code == 200:
                user_info = response.json()
                gitea_user_id = str(user_info.get("id", "0"))
                gitea_username = user_info.get("login", "unknown")

                account = GiteaAccount(
                    instance_id=instance.id,
                    gitea_user_id=gitea_user_id,
                    gitea_username=gitea_username,
                    access_token=gitea_token,
                    auth_mode="token"
                )
                db.add(account)
                db.commit()
                db.refresh(account)
                logger.info(f"Created Token mode account: @{gitea_username} (user_id={gitea_user_id})")
                return True
            else:
                logger.error(f"Failed to fetch user info from Gitea: status={response.status_code}")
                return False
    except Exception as e:
        logger.error(f"Error initializing Token mode account: {e}")
        return False

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    # Re-sync logging once uvicorn is definitely started
    setup_logging()

    # Startup
    Base.metadata.create_all(bind=engine)

    # Run database migrations
    with SessionLocal() as db:
        try:
            apply_migrations(db)
        except Exception as e:
            logger.error(f"Critical: Database migration failed: {e}")
            # In production you might want to exit here

        # Initialize Token mode account from environment variables
        init_token_mode_account(db)

    # Initialize and validate SECRET_KEY for JWT
    get_or_create_secret_key()
    if not validate_secret_key():
        logger.error("SECRET_KEY validation failed! Check logs for details.")
    else:
        logger.info("SECRET_KEY validated successfully")

    start_scheduler()
    logger.info("Application started and scheduler running")
    yield
    # Shutdown (if needed)
    logger.info("Application shutting down")


app = FastAPI(title="GiteaCopilot", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# Include routers
# Pages router handles frontend HTML (no prefix, handles root)
app.include_router(pages.router, tags=["Pages"])
# API routers
app.include_router(admin.router, prefix="/admin", tags=["Admin"])


@app.get("/api")
async def api_root():
    return {"message": "Welcome to GiteaCopilot API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
