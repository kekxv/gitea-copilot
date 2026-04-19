import pytest
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from app.database import Base, get_db
from app.main import app
from fastapi.testclient import TestClient
import os

# Use in-memory SQLite for tests
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    
    yield session
    
    session.close()
    transaction.rollback()
    connection.close()

@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def mock_gitea_client(mocker):
    from app.gitea.client import GiteaClient
    mock = mocker.Mock(spec=GiteaClient)
    return mock

@pytest.fixture
def mock_llm_client(mocker):
    from app.skills.llm_client import LLMClient
    mock = mocker.Mock(spec=LLMClient)
    return mock
