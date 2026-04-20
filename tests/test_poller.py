import pytest
from datetime import datetime, timedelta
from app.models import GiteaAccount, GiteaInstance, SystemConfig
from app.tasks.notification_poller import process_account_notifications
from app.gitea.client import GiteaClient

@pytest.mark.asyncio
async def test_process_account_notifications(db_session, mocker):
    # Setup data
    instance = GiteaInstance(url="http://gitea.local", client_id="cid", client_secret_encrypted="csec")
    db_session.add(instance)
    config = SystemConfig(notification_poll_interval=1)
    db_session.add(config)
    db_session.commit()
    
    account = GiteaAccount(
        instance_id=instance.id,
        gitea_user_id="1",
        gitea_username="bot",
        access_token="token",
        last_notified_at=None
    )
    db_session.add(account)
    db_session.commit()

    # Mock GiteaClient
    mock_client = mocker.patch("app.tasks.notification_poller.GiteaClient", autospec=True)
    instance_mock = mock_client.return_value
    
    # Mock notifications
    instance_mock.get_notifications.return_value = [
        {
            "id": 101,
            "updated_at": "2026-04-20T10:00:00Z",
            "subject": {
                "type": "Issue",
                "latest_comment_url": "http://gitea.local/api/v1/repos/o/r/issues/comments/201",
                "url": "http://gitea.local/api/v1/repos/o/r/issues/1"
            },
            "repository": {"full_name": "o/r", "owner": {"login": "o"}, "name": "r"}
        }
    ]
    
    # Mock comment and issue retrieval
    instance_mock.get_comment_by_id.return_value = {
        "id": 201,
        "body": "@bot hello",
        "user": {"login": "user"}
    }
    instance_mock.get_issue.return_value = {"id": 1, "number": 1, "title": "test issue"}
    instance_mock.mark_notification_as_read.return_value = True
    
    # Mock EventProcessor
    mock_processor = mocker.patch("app.tasks.notification_poller.EventProcessor", autospec=True)
    processor_instance = mock_processor.return_value
    
    # Run the poller for this account
    await process_account_notifications(account, instance, db_session)
    
    # Verify calls
    instance_mock.get_notifications.assert_called_once()
    instance_mock.get_comment_by_id.assert_called_once_with("o", "r", 201)
    instance_mock.get_issue.assert_called_once_with("o", "r", 1)
    
    # Verify processor was called with correct payload
    processor_instance.process.assert_called_once()
    args, _ = processor_instance.process.call_args
    event_type, payload, _ = args
    assert event_type == "issue_comment"
    assert payload["comment"]["id"] == 201
    assert payload["sender"]["login"] == "user"
    
    # Verify notification marked as read
    instance_mock.mark_notification_as_read.assert_called_once_with(101)
    
    # Verify last_notified_at was updated
    db_session.refresh(account)
    assert account.last_notified_at is not None
