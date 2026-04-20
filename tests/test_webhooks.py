import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models import GiteaInstance, GiteaAccount
import hmac
import hashlib
import json

@pytest.fixture
def webhook_client(db_session):
    # Setup necessary data for webhook processing
    instance = GiteaInstance(url="http://gitea.local", client_id="cid", client_secret_encrypted="csec")
    db_session.add(instance)
    db_session.commit()
    
    account = GiteaAccount(
        instance_id=instance.id,
        gitea_user_id="1",
        gitea_username="bot",
        access_token="token",
        webhook_secret="secret"
    )
    db_session.add(account)
    db_session.commit()
    
    return {"instance": instance, "account": account}

def test_webhook_receiver_signature_fail(webhook_client, client):
    # Malformed context (should return 401 with new signature validation)
    payload = {"action": "created", "comment": {"id": 1, "body": "hello"}}
    headers = {"X-Gitea-Event": "issue_comment", "Authorization": "Basic MQ=="} # encoded '1' -> invalid JSON context

    response = client.post("/webhook/gitea", json=payload, headers=headers)
    # With new signature validation, invalid auth returns 401
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_webhook_processor_routing(mocker, db_session, webhook_client):
    from app.webhooks.processor import WebhookProcessor
    
    # Mock Skill Router
    mock_router = mocker.patch("app.skills.router.SkillRouter.route", return_value="AI Response")
    # Mock Gitea Client methods to avoid real network calls
    mocker.patch("app.gitea.client.GiteaClient.check_user_repo_access", return_value=True)
    mocker.patch("app.gitea.client.GiteaClient.create_comment", return_value={})
    
    processor = WebhookProcessor(webhook_client["instance"], webhook_client["account"], db_session)
    
    payload = {
        "action": "created",
        "repository": {"full_name": "o/r"},
        "issue": {"number": 1, "title": "test"},
        "comment": {"id": 1, "body": "@bot review", "user": {"login": "user"}},
        "sender": {"login": "user"}
    }
    
    await processor.process("issue_comment", payload, db_session)
    
    # Verify it routed to skill
    mock_router.assert_called_once()
