import pytest
from datetime import datetime, timedelta
from app.models import GiteaAccount, GiteaInstance, SystemConfig
from app.tasks.notification_poller import process_account_notifications
from app.gitea.client import GiteaClient

@pytest.mark.asyncio
async def test_process_account_notifications_with_tail(db_session, mocker):
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
    
    # Mock notifications: Batch 1 has data, Batch 2 is empty
    instance_mock.get_notifications.side_effect = [
        [{"id": 101, "updated_at": "T1", "subject": {"type": "Issue", "url": "/1"}, "repository": {}}],
        []
    ]
    
    # Minimal mocks for handlers
    mocker.patch("app.tasks.notification_poller.handle_notification")
    
    # Run
    await process_account_notifications(account, instance, db_session)
    
    # Should have called get_notifications twice (once for data, once to confirm empty)
    assert instance_mock.get_notifications.call_count == 2
    
    # Verify last_notified_at was updated
    db_session.refresh(account)
    assert account.last_notified_at is not None

@pytest.mark.asyncio
async def test_handle_notification_logic(db_session, mocker):
    # This tests the catch-up logic within handle_notification
    instance = GiteaInstance(url="http://gitea.local", client_id="cid", client_secret_encrypted="csec")
    db_session.add(instance)
    account = GiteaAccount(instance_id=1, gitea_user_id="1", gitea_username="bot", access_token="t")
    db_session.add(account)
    db_session.commit()

    mock_client = mocker.Mock(spec=GiteaClient)
    
    note = {
        "id": 101,
        "subject": {"type": "Issue", "url": "http://gitea.local/api/v1/repos/o/r/issues/1"},
        "repository": {"full_name": "o/r"}
    }
    
    # Mock comments: bot replied at 09:00, new mention at 10:00
    mock_client.get_issue_comments.return_value = [
        {"id": 1, "user": {"login": "bot"}, "created_at": "2026-04-20T09:00:00Z", "body": "ok"},
        {"id": 2, "user": {"login": "user"}, "created_at": "2026-04-20T10:00:00Z", "body": "@bot test"}
    ]
    mock_client.get_issue.return_value = {"id": 1, "number": 1, "body": "desc", "created_at": "2026-04-20T08:00:00Z", "user": {}}
    
    mock_processor = mocker.patch("app.tasks.notification_poller.EventProcessor", autospec=True)
    
    from app.tasks.notification_poller import handle_notification
    await handle_notification(note, mock_client, account, instance, db_session)
    
    # Mark read should be called
    mock_client.mark_notification_as_read.assert_called_once_with(101)
    # Processor should be called for comment #2
    mock_processor.return_value.process.assert_called_once()
    args, _ = mock_processor.return_value.process.call_args
    assert args[0] == "issue_comment"
    assert args[1]["comment"]["id"] == 2
