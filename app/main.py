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
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .database import engine, Base
from .routes import admin, pages
from .webhooks import router as webhook_router
from .tasks import start_scheduler

logger = logging.getLogger(__name__)

# Initialize database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="GiteaCopilot")

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
async def startup_event():
    """Start background tasks on app startup."""
    start_scheduler()
    logger.info("Application started")


# Include routers
# Pages router handles frontend HTML (no prefix, handles root)
app.include_router(pages.router, tags=["Pages"])
# API routers
app.include_router(admin.router, prefix="/admin", tags=["Admin"])
app.include_router(webhook_router, prefix="/webhook", tags=["Webhook"])


@app.get("/api")
async def api_root():
    return {"message": "Welcome to GiteaCopilot API"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
