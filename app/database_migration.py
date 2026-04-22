import logging
from datetime import datetime
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
from .models import Migration

logger = logging.getLogger("uvicorn.error")

# Migration tasks: (version, list of SQL statements)
# Version 1: Add columns for notification polling
# Version 2: Add unique index for processed_events
# Version 3: Add auth_mode column for token/oauth distinction
# Version 4: Add strip_emoji column for review emoji setting
MIGRATIONS = [
    (1, [
        "ALTER TABLE system_config ADD COLUMN notification_poll_interval INTEGER DEFAULT 1",
        "ALTER TABLE gitea_accounts ADD COLUMN last_notified_at DATETIME"
    ]),
    (2, [
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_event_ref ON processed_events (event_type, reference_id)"
    ]),
    (3, [
        "ALTER TABLE gitea_accounts ADD COLUMN auth_mode VARCHAR(10) DEFAULT 'oauth'"
    ]),
    (4, [
        "ALTER TABLE system_config ADD COLUMN strip_emoji BOOLEAN DEFAULT 0"
    ]),
]

def apply_migrations(db: Session):
    """Check database version and apply pending migrations."""
    # Ensure migrations table exists (created by create_all, but double check)
    inspector = inspect(db.get_bind())
    if not inspector.has_table("migrations"):
        logger.info("Migrations table not found, it should have been created by create_all.")
        return

    # Get the latest applied version
    try:
        current_version_record = db.query(Migration).order_by(Migration.version.desc()).first()
        v_start = current_version_record.version if current_version_record else 0
    except Exception as e:
        logger.warning(f"Could not read migrations table: {e}")
        v_start = 0

    # Apply migrations in order
    for version, sqls in MIGRATIONS:
        if version > v_start:
            logger.info(f"Applying database migration version {version}...")
            try:
                for sql in sqls:
                    try:
                        db.execute(text(sql))
                    except Exception as e:
                        # For SQLite, ignore "duplicate column" errors during manual migrations
                        if "duplicate column name" in str(e).lower():
                            logger.info(f"Column already exists, skipping: {sql}")
                            continue
                        raise e
                
                # Record successful migration
                migration = Migration(version=version)
                db.add(migration)
                db.commit()
                logger.info(f"Successfully applied migration version {version}")
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to apply migration version {version}: {e}")
                # We raise to stop application startup if migration fails
                raise e
