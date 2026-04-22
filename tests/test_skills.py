import pytest
from app.skills.implementations import HelpSkill, LabelSkill, AnalyzeSkill, ReviewSkill, CloseSkill, OpenSkill
from app.gitea.base import BaseGitClient
from app.skills.llm_client import LLMClient
import json

@pytest.mark.asyncio
async def test_help_skill(mocker):
    skill = HelpSkill(mocker.Mock(), mocker.Mock())
    res = await skill.execute("help", {}, {}, {})
    assert "帮助" in res
    assert "review" in res
    assert "close" in res

@pytest.mark.asyncio
async def test_close_skill(mocker):
    mock_git = mocker.Mock(spec=BaseGitClient)
    skill = CloseSkill(mocker.Mock(), mock_git)

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 123}
    }

    res = await skill.execute("close", {}, {}, payload)
    assert res == ""  # CloseSkill is silent
    mock_git.close_issue.assert_called_once_with("owner", "repo", 123)

@pytest.mark.asyncio
async def test_open_skill(mocker):
    mock_git = mocker.Mock(spec=BaseGitClient)
    skill = OpenSkill(mocker.Mock(), mock_git)

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 123}
    }

    res = await skill.execute("open", {}, {}, payload)
    assert res == ""  # OpenSkill is silent
    mock_git.open_issue.assert_called_once_with("owner", "repo", 123)

@pytest.mark.asyncio
async def test_label_skill(mocker):
    mock_git = mocker.Mock(spec=BaseGitClient)
    skill = LabelSkill(mocker.Mock(), mock_git)

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 123}
    }

    res = await skill.execute("label bug feature", {}, {}, payload)
    assert res == "" # LabelSkill is silent
    mock_git.add_issue_label.assert_called_once_with("owner", "repo", 123, ["bug", "feature"])

@pytest.mark.asyncio
async def test_review_skill_comment(mocker):
    """Test review with COMMENT event - should use COMMENT."""
    mock_git = mocker.Mock(spec=BaseGitClient)
    # Mock async methods
    mock_git.get_pull_request = mocker.AsyncMock(return_value={
        "title": "test", "head": {"sha": "head-sha"}, "user": {"login": "other-user"}
    })
    mock_git.get_pull_request_diff = mocker.AsyncMock(return_value="--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-old\n+new")
    mock_git.get_current_user = mocker.AsyncMock(return_value={"login": "bot-user"})
    mock_git.get_repo_file_content = mocker.AsyncMock(return_value=None)
    mock_git.create_pull_request_review = mocker.AsyncMock(return_value={})

    mock_llm = mocker.Mock(spec=LLMClient)

    skill = ReviewSkill(mock_llm, mock_git)
    payload = {
        "is_pull": True,
        "repository": {"full_name": "o/r"},
        "pull_request": {"number": 1, "id": 1},
        "sender": {"login": "caesar"}
    }

    async def side_effect(*args, **kwargs):
        on_tool_call = kwargs.get("on_tool_call")
        # AI passes COMMENT event with LGTM summary
        await on_tool_call("submit_review", {"comments": [], "summary": "LGTM", "event": "COMMENT"})
        return "Success", []

    mock_llm.generate_with_tools = mocker.AsyncMock(side_effect=side_effect)

    res = await skill.execute("review", {}, None, payload)
    assert res == ""

    mock_git.create_pull_request_review.assert_called_once()
    args, kwargs = mock_git.create_pull_request_review.call_args
    assert kwargs["body"] == "LGTM"
    assert kwargs["event"] == "COMMENT"

@pytest.mark.asyncio
async def test_review_skill_request_changes(mocker):
    """Test review with REQUEST_CHANGES event."""
    mock_git = mocker.Mock(spec=BaseGitClient)
    mock_git.get_pull_request = mocker.AsyncMock(return_value={
        "title": "test", "head": {"sha": "head-sha"}, "user": {"login": "other-user"}
    })
    mock_git.get_pull_request_diff = mocker.AsyncMock(return_value="--- a/f.py\n+++ b/f.py\n@@ -10,1 +10,1 @@\n-old\n+new")
    mock_git.get_current_user = mocker.AsyncMock(return_value={"login": "bot-user"})
    mock_git.get_repo_file_content = mocker.AsyncMock(return_value=None)
    mock_git.create_pull_request_review = mocker.AsyncMock(return_value={})
    mock_git.get_server_version = mocker.AsyncMock(return_value="1.24.0")
    mock_git._is_legacy_version = mocker.Mock(return_value=False)

    mock_llm = mocker.Mock(spec=LLMClient)
    skill = ReviewSkill(mock_llm, mock_git)

    async def side_effect(*args, **kwargs):
        on_tool_call = kwargs.get("on_tool_call")
        # AI found issues and requests changes
        await on_tool_call("submit_review", {
            "comments": [{"path": "f.py", "new_position": 10, "body": "Found a secret: sk-1234567890"}],
            "summary": "发现安全漏洞",
            "event": "REQUEST_CHANGES"
        })
        return "Success", []

    mock_llm.generate_with_tools = mocker.AsyncMock(side_effect=side_effect)
    payload = {
        "is_pull": True,
        "repository": {"full_name": "o/r"},
        "pull_request": {"number": 1, "id": 1},
        "sender": {"login": "caesar"}
    }

    await skill.execute("review", {}, None, payload)

    mock_git.create_pull_request_review.assert_called_once()
    args, kwargs = mock_git.create_pull_request_review.call_args
    assert "[REDACTED]" in kwargs["comments"][0]["body"]
    assert kwargs["comments"][0]["new_position"] == 10
    assert "old_position" not in kwargs["comments"][0]  # Should not include null
    assert kwargs["event"] == "REQUEST_CHANGES"

@pytest.mark.asyncio
async def test_review_skill_approved_forced_to_comment(mocker):
    """Test that APPROVED from AI is forced to COMMENT."""
    mock_git = mocker.Mock(spec=BaseGitClient)
    mock_git.get_pull_request = mocker.AsyncMock(return_value={
        "title": "test", "head": {"sha": "head-sha"}, "user": {"login": "other-user"}
    })
    mock_git.get_pull_request_diff = mocker.AsyncMock(return_value="--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-old\n+new")
    mock_git.get_current_user = mocker.AsyncMock(return_value={"login": "bot-user"})
    mock_git.get_repo_file_content = mocker.AsyncMock(return_value=None)
    mock_git.create_pull_request_review = mocker.AsyncMock(return_value={})
    mock_git.get_server_version = mocker.AsyncMock(return_value="1.24.0")
    mock_git._is_legacy_version = mocker.Mock(return_value=False)

    mock_llm = mocker.Mock(spec=LLMClient)
    skill = ReviewSkill(mock_llm, mock_git)

    async def side_effect(*args, **kwargs):
        on_tool_call = kwargs.get("on_tool_call")
        # AI mistakenly tries APPROVED, should be forced to COMMENT
        await on_tool_call("submit_review", {"comments": [], "summary": "LGTM", "event": "APPROVED"})
        return "Success", []

    mock_llm.generate_with_tools = mocker.AsyncMock(side_effect=side_effect)
    payload = {
        "is_pull": True,
        "repository": {"full_name": "o/r"},
        "pull_request": {"number": 1, "id": 1},
        "sender": {"login": "caesar"}
    }

    await skill.execute("review", {}, None, payload)

    mock_git.create_pull_request_review.assert_called_once()
    args, kwargs = mock_git.create_pull_request_review.call_args
    assert kwargs["event"] == "COMMENT"  # Forced from APPROVED to COMMENT

@pytest.mark.asyncio
async def test_review_skill_own_pr_forced_to_comment(mocker):
    """Test that bot reviewing its own PR cannot use REQUEST_CHANGES."""
    mock_git = mocker.Mock(spec=BaseGitClient)
    # PR author is the same as bot user
    mock_git.get_pull_request = mocker.AsyncMock(return_value={
        "title": "test", "head": {"sha": "head-sha"}, "user": {"login": "bot-user"}
    })
    mock_git.get_pull_request_diff = mocker.AsyncMock(return_value="--- a/f.py\n+++ b/f.py\n@@ -10,1 +10,1 @@\n-old\n+new")
    mock_git.get_current_user = mocker.AsyncMock(return_value={"login": "bot-user"})
    mock_git.get_repo_file_content = mocker.AsyncMock(return_value=None)
    mock_git.create_pull_request_review = mocker.AsyncMock(return_value={})
    mock_git.get_server_version = mocker.AsyncMock(return_value="1.24.0")
    mock_git._is_legacy_version = mocker.Mock(return_value=False)

    mock_llm = mocker.Mock(spec=LLMClient)
    skill = ReviewSkill(mock_llm, mock_git)

    async def side_effect(*args, **kwargs):
        on_tool_call = kwargs.get("on_tool_call")
        # AI wants REQUEST_CHANGES but bot is reviewing own PR
        await on_tool_call("submit_review", {
            "comments": [{"path": "f.py", "new_position": 10, "body": "Found issue"}],
            "summary": "发现问题",
            "event": "REQUEST_CHANGES"
        })
        return "Success", []

    mock_llm.generate_with_tools = mocker.AsyncMock(side_effect=side_effect)
    payload = {
        "is_pull": True,
        "repository": {"full_name": "o/r"},
        "pull_request": {"number": 1, "id": 1},
        "sender": {"login": "caesar"}
    }

    await skill.execute("review", {}, None, payload)

    mock_git.create_pull_request_review.assert_called_once()
    args, kwargs = mock_git.create_pull_request_review.call_args
    # Even though AI said REQUEST_CHANGES, bot is reviewing own PR, force COMMENT
    assert kwargs["event"] == "COMMENT"