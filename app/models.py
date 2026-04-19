from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    totp_enabled = Column(Boolean, default=False)
    totp_secret = Column(String, nullable=True)


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, index=True)
    llm_base_url = Column(String, nullable=True, default="https://api.openai.com/v1")
    llm_api_key = Column(String, nullable=True)
    llm_model = Column(String, nullable=True, default="gpt-4o-mini")
    # Copilot docs config
    copilot_docs_limit = Column(Integer, nullable=True, default=10)  # Max number of .gitea/copilot docs
    copilot_docs_size_limit = Column(Integer, nullable=True, default=25)  # Max size in KB
    # AI token limits
    ai_max_tokens = Column(Integer, nullable=True, default=8000)  # Max tokens per AI call
    ai_context_limit = Column(Integer, nullable=True, default=50000)  # Max total context tokens


class GiteaInstance(Base):
    __tablename__ = "gitea_instances"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False)
    client_id = Column(String, nullable=False)
    client_secret_encrypted = Column(String, nullable=False)

    accounts = relationship("GiteaAccount", back_populates="instance")


class GiteaAccount(Base):
    __tablename__ = "gitea_accounts"

    id = Column(Integer, primary_key=True, index=True)
    instance_id = Column(Integer, ForeignKey("gitea_instances.id"), nullable=False)
    gitea_user_id = Column(String, nullable=False)
    gitea_username = Column(String, nullable=False)
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    # User-level webhook
    webhook_id = Column(Integer, nullable=True)
    webhook_secret = Column(String, nullable=True)

    instance = relationship("GiteaInstance", back_populates="accounts")


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False)
    reference_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)