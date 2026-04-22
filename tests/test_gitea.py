import pytest
import httpx
from app.gitea.client import GiteaClient
from app.models import GiteaAccount, GiteaInstance
from datetime import datetime, timedelta

@pytest.mark.asyncio
async def test_gitea_client_request(mocker):
    # Mock httpx response
    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": 1, "name": "test-repo"}
    
    mock_request = mocker.patch("httpx.AsyncClient.request", return_value=mock_response)
    
    client = GiteaClient(base_url="http://gitea.local", access_token="fake-token")
    repo = await client.get_repo("owner", "repo")
    
    assert repo["name"] == "test-repo"
    mock_request.assert_called_once()

@pytest.mark.asyncio
async def test_gitea_client_token_refresh(db_session, mocker):
    from app.utils.encryption import encrypt_sensitive_value
    # Setup data
    instance = GiteaInstance(
        url="http://gitea.local", 
        client_id="cid", 
        client_secret_encrypted=encrypt_sensitive_value("csec")
    )
    db_session.add(instance)
    db_session.commit()
    
    # Expires in 5 mins (should trigger refresh)
    expires_at = datetime.utcnow() + timedelta(minutes=5)
    account = GiteaAccount(
        instance_id=instance.id, 
        gitea_user_id="1", 
        gitea_username="user", 
        access_token="old-token",
        refresh_token="ref-token",
        token_expires_at=expires_at
    )
    db_session.add(account)
    db_session.commit()
    
    # Mock token refresh API
    mock_refresh_res = mocker.Mock()
    mock_refresh_res.status_code = 200
    mock_refresh_res.json.return_value = {
        "access_token": "new-token",
        "refresh_token": "new-ref-token",
        "expires_in": 3600
    }
    
    # Mock httpx.post for refresh
    mock_post = mocker.patch("httpx.AsyncClient.post", return_value=mock_refresh_res)
    # Mock general request to avoid real network call after refresh
    mock_get = mocker.patch("httpx.AsyncClient.request", return_value=mocker.Mock(status_code=200, json=lambda: {}))
    
    client = GiteaClient(
        base_url=instance.url, 
        access_token=account.access_token,
        account_id=account.id,
        db_session=db_session
    )
    
    await client.get_repo("owner", "repo")
    
    # Verify DB was updated
    db_session.refresh(account)
    assert account.access_token == "new-token"
    assert client.access_token == "new-token"


class TestGiteaClientMethods:
    """Tests for various GiteaClient methods."""

    def setup_method(self):
        """Setup test client."""
        self.client = GiteaClient(base_url="http://gitea.local", access_token="fake-token")

    @pytest.mark.asyncio
    async def test_get_current_user(self, mocker):
        """Test get_current_user method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"login": "testuser", "id": 1}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_current_user()
        assert result["login"] == "testuser"

    @pytest.mark.asyncio
    async def test_get_user_repos(self, mocker):
        """Test get_user_repos method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"name": "repo1"}, {"name": "repo2"}]

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_user_repos()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_repo_contents(self, mocker):
        """Test get_repo_contents method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"type": "file", "name": "README.md"}]

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_repo_contents("owner", "repo", "")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_repo_file_content(self, mocker):
        """Test get_repo_file_content method."""
        import base64
        content = "Hello World"
        encoded = base64.b64encode(content.encode()).decode()

        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"type": "file", "content": encoded}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_repo_file_content("owner", "repo", "README.md")
        assert result == content

    @pytest.mark.asyncio
    async def test_get_repo_file_content_not_file(self, mocker):
        """Test get_repo_file_content when not a file."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"type": "dir"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_repo_file_content("owner", "repo", "src")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_repo_file_content_error(self, mocker):
        """Test get_repo_file_content handles error."""
        mock_response = mocker.Mock()
        mock_response.status_code = 404

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_repo_file_content("owner", "repo", "missing.md")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_issue(self, mocker):
        """Test get_issue method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"number": 123, "title": "Test Issue"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_issue("owner", "repo", 123)
        assert result["number"] == 123

    @pytest.mark.asyncio
    async def test_get_issue_labels(self, mocker):
        """Test get_issue_labels method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"name": "bug"}, {"name": "feature"}]

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_issue_labels("owner", "repo", 123)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_create_comment(self, mocker):
        """Test create_comment method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 456, "body": "Test comment"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.create_comment("owner", "repo", 123, "Test comment")
        assert result["id"] == 456

    @pytest.mark.asyncio
    async def test_close_issue(self, mocker):
        """Test close_issue method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"state": "closed"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.close_issue("owner", "repo", 123)
        assert result["state"] == "closed"

    @pytest.mark.asyncio
    async def test_open_issue(self, mocker):
        """Test open_issue method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"state": "open"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.open_issue("owner", "repo", 123)
        assert result["state"] == "open"

    @pytest.mark.asyncio
    async def test_get_pull_request(self, mocker):
        """Test get_pull_request method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"number": 1, "title": "Test PR"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_pull_request("owner", "repo", 1)
        assert result["number"] == 1

    @pytest.mark.asyncio
    async def test_get_pull_request_diff(self, mocker):
        """Test get_pull_request_diff method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.text = "--- a/file\n+++ b/file\n@@ -1,1 +1,1 @@\n-old\n+new"

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_pull_request_diff("owner", "repo", 1)
        assert "old" in result
        assert "new" in result

    @pytest.mark.asyncio
    async def test_get_pull_request_files(self, mocker):
        """Test get_pull_request_files method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"filename": "file.py", "status": "modified"}]

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_pull_request_files("owner", "repo", 1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_create_pull_request_review(self, mocker):
        """Test create_pull_request_review method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 1}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.create_pull_request_review(
            "owner", "repo", 1,
            body="Review body",
            comments=[{"path": "file.py", "body": "Comment"}],
            event="COMMENT"
        )
        assert result["id"] == 1

    @pytest.mark.asyncio
    async def test_get_notifications(self, mocker):
        """Test get_notifications method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": 1}, {"id": 2}]

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_notifications()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_notifications_with_since(self, mocker):
        """Test get_notifications with since parameter."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        mock_request = mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        since = datetime.utcnow() - timedelta(hours=1)
        result = await self.client.get_notifications(since=since)

        # Check that since was passed
        call_args = mock_request.call_args
        assert "since" in call_args.kwargs["params"]

    @pytest.mark.asyncio
    async def test_mark_notification_as_read(self, mocker):
        """Test mark_notification_as_read method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 204

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.mark_notification_as_read(123)
        assert result is True

    @pytest.mark.asyncio
    async def test_mark_notification_as_read_error(self, mocker):
        """Test mark_notification_as_read handles error."""
        mock_response = mocker.Mock()
        mock_response.status_code = 404

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.mark_notification_as_read(123)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_issue_comments(self, mocker):
        """Test get_issue_comments method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": 1}, {"id": 2}]

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_issue_comments("owner", "repo", 123)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_add_comment_reaction(self, mocker):
        """Test add_comment_reaction method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"content": "hooray"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.add_comment_reaction("owner", "repo", 456, "hooray")
        assert result["content"] == "hooray"

    @pytest.mark.asyncio
    async def test_add_issue_reaction(self, mocker):
        """Test add_issue_reaction method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"content": "eyes"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.add_issue_reaction("owner", "repo", 123, "eyes")
        assert result["content"] == "eyes"

    @pytest.mark.asyncio
    async def test_get_comment_reactions(self, mocker):
        """Test get_comment_reactions method."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"content": "+1"}, {"content": "heart"}]

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.get_comment_reactions("owner", "repo", 456)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_has_bot_reaction_true(self, mocker):
        """Test has_bot_reaction returns True when bot has reaction."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"content": "eyes", "user": {"login": "bot-user"}}
        ]

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.has_bot_reaction("owner", "repo", 123, 456, "eyes", "bot-user")
        assert result is True

    @pytest.mark.asyncio
    async def test_has_bot_reaction_false(self, mocker):
        """Test has_bot_reaction returns False when bot doesn't have reaction."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"content": "eyes", "user": {"login": "other-user"}}
        ]

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.has_bot_reaction("owner", "repo", 123, 456, "eyes", "bot-user")
        assert result is False

    @pytest.mark.asyncio
    async def test_has_bot_reaction_empty(self, mocker):
        """Test has_bot_reaction returns False when no reactions."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.has_bot_reaction("owner", "repo", 123, 456, "eyes", "bot-user")
        assert result is False

    @pytest.mark.asyncio
    async def test_request_error(self, mocker):
        """Test _request handles API error."""
        mock_response = mocker.Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        with pytest.raises(Exception) as exc_info:
            await self.client.get_repo("owner", "repo")

        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_204_returns_empty(self, mocker):
        """Test _request returns empty dict for 204 status."""
        mock_response = mocker.Mock()
        mock_response.status_code = 204

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        result = await self.client.mark_notification_as_read(123)
        assert result is True  # mark_notification_as_read wraps the call


class TestGiteaClientPermission:
    """Tests for permission checking."""

    @pytest.mark.asyncio
    async def test_check_user_repo_access_write(self, mocker):
        """Test check_user_repo_access with write permission."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"permission": "write"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        client = GiteaClient(base_url="http://gitea.local", access_token="fake-token")
        result = await client.check_user_repo_access("owner", "repo", "user")
        assert result is True

    @pytest.mark.asyncio
    async def test_check_user_repo_access_admin(self, mocker):
        """Test check_user_repo_access with admin permission."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"permission": "admin"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        client = GiteaClient(base_url="http://gitea.local", access_token="fake-token")
        result = await client.check_user_repo_access("owner", "repo", "user")
        assert result is True

    @pytest.mark.asyncio
    async def test_check_user_repo_access_read(self, mocker):
        """Test check_user_repo_access with read permission (not sufficient)."""
        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"permission": "read"}

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        client = GiteaClient(base_url="http://gitea.local", access_token="fake-token")
        result = await client.check_user_repo_access("owner", "repo", "user")
        assert result is False

    @pytest.mark.asyncio
    async def test_check_user_repo_access_error(self, mocker):
        """Test check_user_repo_access handles error."""
        mock_response = mocker.Mock()
        mock_response.status_code = 404

        mocker.patch("httpx.AsyncClient.request", return_value=mock_response)

        client = GiteaClient(base_url="http://gitea.local", access_token="fake-token")
        result = await client.check_user_repo_access("owner", "repo", "user")
        assert result is False
