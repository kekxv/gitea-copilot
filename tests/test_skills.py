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
async def test_review_skill_lgtm(mocker):
    mock_git = mocker.Mock(spec=BaseGitClient)
    # Mock PR and Diff
    mock_git.get_pull_request.return_value = {"title": "test", "head": {"sha": "head-sha"}}
    # Standard git diff format is important for _parse_diff
    mock_git.get_pull_request_diff.return_value = "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,1 @@\n-old\n+new"
    
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
        # AI must pass "LGTM" as summary explicitly
        await on_tool_call("submit_review", {"comments": [], "summary": "LGTM"})
        return "Success", [] # Return value MUST NOT contain "AI 调用出错"
        
    mock_llm.generate_with_tools.side_effect = side_effect
    
    res = await skill.execute("review", {}, None, payload)
    assert res == "" 
    
    mock_git.create_pull_request_review.assert_called_once()
    args, kwargs = mock_git.create_pull_request_review.call_args
    assert kwargs["body"] == "LGTM"

@pytest.mark.asyncio
async def test_review_skill_with_issues(mocker):
    mock_git = mocker.Mock(spec=BaseGitClient)
    mock_git.get_pull_request.return_value = {"title": "test", "head": {"sha": "head-sha"}}
    # Correct format for line 10
    mock_git.get_pull_request_diff.return_value = "--- a/f.py\n+++ b/f.py\n@@ -10,1 +10,1 @@\n-old\n+new"
    
    mock_llm = mocker.Mock(spec=LLMClient)
    skill = ReviewSkill(mock_llm, mock_git)
    
    async def side_effect(*args, **kwargs):
        on_tool_call = kwargs.get("on_tool_call")
        # Line 10 is valid in our mock diff
        await on_tool_call("submit_review", {
            "comments": [{"path": "f.py", "new_position": 10, "body": "Found a secret: sk-1234567890"}], 
            "summary": "Found risks"
        })
        return "Success", []
        
    mock_llm.generate_with_tools.side_effect = side_effect
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
