import pytest
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from app.models import Base, Migration
from app.database_migration import apply_migrations

# Use a separate in-memory database for migration tests to avoid side effects
@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(bind=engine)
    return engine, TestingSessionLocal

def test_apply_migrations_fresh_start(test_db):
    engine, SessionLocal = test_db
    # First, create all tables (initial state)
    # NOTE: In our current app, create_all already includes the columns
    # because they are in the models. Migration logic will see version 0 and try to run.
    Base.metadata.create_all(bind=engine)
    
    with SessionLocal() as db:
        # Initial state: migrations table is empty, version is 0
        apply_migrations(db)
        
        # Verify version 1 was recorded
        migration = db.query(Migration).filter(Migration.version == 1).first()
        assert migration is not None
        
        # Verify columns exist
        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("system_config")]
        assert "notification_poll_interval" in columns
        
        columns_acc = [c["name"] for c in inspector.get_columns("gitea_accounts")]
        assert "last_notified_at" in columns_acc

def test_apply_migrations_already_applied(test_db, mocker):
    engine, SessionLocal = test_db
    Base.metadata.create_all(bind=engine)
    
    with SessionLocal() as db:
        # Record version 1 as already applied
        db.add(Migration(version=1))
        db.commit()
        
        # Mock logger to see if it skips
        mock_logger = mocker.patch("app.database_migration.logger.info")
        
        apply_migrations(db)
        
        # Should NOT see "Applying database migration version 1"
        applied_calls = [call for call in mock_logger.call_args_list if "Applying database migration" in str(call)]
        assert len(applied_calls) == 0

def test_apply_migrations_idempotency_via_partial_existence(test_db, mocker):
    engine, SessionLocal = test_db
    # To test the skip logic for existing columns, we need a table that 
    # has the column but the Migration record says it hasn't been applied.
    # create_all already does this!
    Base.metadata.create_all(bind=engine)
    
    with SessionLocal() as db:
        mock_logger = mocker.patch("app.database_migration.logger.info")
        
        # Migration record is empty (v_start = 0). 
        # apply_migrations will try to run version 1.
        # version 1 SQLs will fail (duplicate column) and hit our skip logic.
        apply_migrations(db)
        
        # Verify it logged the skip for both columns
        # The SQLs in MIGRATIONS use DEFAULT 1 and DATETIME, so we match those
        mock_logger.assert_any_call("Column already exists, skipping: ALTER TABLE system_config ADD COLUMN notification_poll_interval INTEGER DEFAULT 1")
        mock_logger.assert_any_call("Column already exists, skipping: ALTER TABLE gitea_accounts ADD COLUMN last_notified_at DATETIME")
        
        # Migration should still be recorded as successful (version 1)
        migration = db.query(Migration).filter(Migration.version == 1).first()
        assert migration is not None

def test_apply_migrations_no_table_error(test_db, mocker):
    engine, SessionLocal = test_db
    # DO NOT create tables
    
    with SessionLocal() as db:
        mock_logger = mocker.patch("app.database_migration.logger.info")
        
        # Should log info and return early
        apply_migrations(db)
        mock_logger.assert_any_call("Migrations table not found, it should have been created by create_all.")

def test_apply_migrations_read_error(test_db, mocker):
    engine, SessionLocal = test_db
    Base.metadata.create_all(bind=engine)
    
    with SessionLocal() as db:
        # Mock query to raise exception
        mocker.patch.object(db, "query", side_effect=Exception("DB Error"))
        mock_logger = mocker.patch("app.database_migration.logger.warning")
        
        apply_migrations(db)
        
        args, _ = mock_logger.call_args
        assert "Could not read migrations table" in args[0]

def test_apply_migrations_critical_failure(test_db, mocker):
    engine, SessionLocal = test_db
    Base.metadata.create_all(bind=engine)
    
    with SessionLocal() as db:
        # Mock the SQL list to have a bad SQL that DOES NOT trigger the skip logic
        # (i.e., not a "duplicate column" error)
        import app.database_migration
        mocker.patch.object(app.database_migration, "MIGRATIONS", [(1, ["UPDATE non_existent_table SET x=1"])])
        
        with pytest.raises(Exception) as excinfo:
            apply_migrations(db)
        
        assert "no such table: non_existent_table" in str(excinfo.value)
