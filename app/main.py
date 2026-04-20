import logging
import sys

# Configure logging BEFORE any imports that create loggers
# This ensures all loggers use our configuration
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
    force=True  # Override any existing configuration
)

# Now import the rest
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .database import engine, Base, SessionLocal
from .routes import admin, pages
from .tasks import start_scheduler
from .utils.security import get_or_create_secret_key, validate_secret_key
from .database_migration import apply_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    # Startup
    Base.metadata.create_all(bind=engine)

    # Run database migrations
    with SessionLocal() as db:
        try:
            apply_migrations(db)
        except Exception as e:
            logger.error(f"Critical: Database migration failed: {e}")
            # In production you might want to exit here
    
    # Initialize and validate SECRET_KEY for JWT
    get_or_create_secret_key()
    if not validate_secret_key():
        logger.error("SECRET_KEY validation failed! Check logs for details.")
        # Don't fail startup, but log the issue
    else:
        logger.info("SECRET_KEY validated successfully")
    
    start_scheduler()
    logger.info("Application started")
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
