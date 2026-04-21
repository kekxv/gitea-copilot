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
    # This tests the reaction-based tracking logic within handle_notification
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

    # Mock comments: bot replied (should be skipped), new mention from user (should be processed)
    mock_client.get_issue_comments = mocker.AsyncMock(return_value=[
        {"id": 1, "user": {"login": "bot"}, "created_at": "2026-04-20T09:00:00Z", "body": "ok"},
        {"id": 2, "user": {"login": "user"}, "created_at": "2026-04-20T10:00:00Z", "body": "@bot test"}
    ])
    mock_client.get_issue = mocker.AsyncMock(return_value={"id": 1, "number": 1, "body": "desc", "created_at": "2026-04-20T08:00:00Z", "user": {}})
    mock_client.mark_notification_as_read = mocker.AsyncMock(return_value=True)

    # Mock reaction checks: no reactions exist yet (returns False for all)
    mock_client.has_bot_reaction = mocker.AsyncMock(return_value=False)
    mock_client.add_comment_reaction = mocker.AsyncMock(return_value={})
    mock_client.add_issue_reaction = mocker.AsyncMock(return_value={})

    mock_processor = mocker.patch("app.tasks.notification_poller.EventProcessor", autospec=True)

    from app.tasks.notification_poller import handle_notification
    await handle_notification(note, mock_client, account, instance, db_session)

    # Mark read should be called
    mock_client.mark_notification_as_read.assert_called_once_with(101)
    # Processor should be called for comment #2 (user's comment with mention)
    mock_processor.return_value.process.assert_called_once()
    args, _ = mock_processor.return_value.process.call_args
    assert args[0] == "issue_comment"
    assert args[1]["comment"]["id"] == 2

    # Eyes reaction should be batch-added before processing
    # Hooray reaction should be added after processing
    # Both reactions on comment #2
    assert mock_client.add_comment_reaction.call_count == 2
    calls = mock_client.add_comment_reaction.call_args_list
    # First call is eyes (batch add before processing)
    assert calls[0] == mocker.call("o", "r", 2, "eyes")
    # Second call is hooray (after processing completed)
    assert calls[1] == mocker.call("o", "r", 2, "hooray")
