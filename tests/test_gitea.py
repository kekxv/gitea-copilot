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
