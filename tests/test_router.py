"""Tests for SkillRouter module."""
import pytest
from app.skills.router import SkillRouter
from app.skills.implementations import HelpSkill, LabelSkill, AnalyzeSkill, ReviewSkill, CloseSkill, OpenSkill


class TestSkillRouter:
    """Tests for SkillRouter class."""

    def test_classify_intent_help(self, mocker):
        """Test classify_intent for help command."""
        router = SkillRouter(db_session=None, gitea_client=mocker.Mock())
        assert router.classify_intent("help") == "help"
        assert router.classify_intent("帮助") == "help"
        assert router.classify_intent("?") == "help"

    def test_classify_intent_label(self, mocker):
        """Test classify_intent for label command."""
        router = SkillRouter(db_session=None, gitea_client=mocker.Mock())
        assert router.classify_intent("label bug") == "label"
        assert router.classify_intent("标签 bug") == "label"
        assert router.classify_intent("tag bug") == "label"

    def test_classify_intent_review(self, mocker):
        """Test classify_intent for review command."""
        router = SkillRouter(db_session=None, gitea_client=mocker.Mock())
        assert router.classify_intent("review") == "review"
        assert router.classify_intent("审核") == "review"
        assert router.classify_intent("审查") == "review"
        assert router.classify_intent("检查") == "review"

    def test_classify_intent_close(self, mocker):
        """Test classify_intent for close command."""
        router = SkillRouter(db_session=None, gitea_client=mocker.Mock())
        assert router.classify_intent("close") == "close"
        assert router.classify_intent("关闭") == "close"

    def test_classify_intent_open(self, mocker):
        """Test classify_intent for open command."""
        router = SkillRouter(db_session=None, gitea_client=mocker.Mock())
        assert router.classify_intent("open") == "open"
        assert router.classify_intent("打开") == "open"
        assert router.classify_intent("reopen") == "open"
        assert router.classify_intent("重开") == "open"

    def test_classify_intent_analyze_default(self, mocker):
        """Test classify_intent defaults to analyze."""
        router = SkillRouter(db_session=None, gitea_client=mocker.Mock())
        assert router.classify_intent("some random question") == "analyze"
        assert router.classify_intent("") == "analyze"
        assert router.classify_intent("如何部署") == "analyze"

    def test_load_config_defaults(self, mocker):
        """Test _load_config returns defaults."""
        router = SkillRouter(db_session=None, gitea_client=mocker.Mock())
        config = router._load_config()
        assert config["copilot_docs_limit"] == 10
        assert config["copilot_docs_size_limit"] == 25
        assert config["ai_max_tokens"] == 8000
        assert config["ai_context_limit"] == 50000

    def test_load_config_from_db(self, mocker):
        """Test _load_config from database."""
        # Need to mock config attributes properly for LLM client too
        mock_config = mocker.Mock()
        mock_config.copilot_docs_limit = 20
        mock_config.copilot_docs_size_limit = 50
        mock_config.ai_max_tokens = 4000
        mock_config.ai_context_limit = 30000
        # LLM config - use empty strings to fallback to defaults
        mock_config.llm_base_url = ""
        mock_config.llm_api_key = ""
        mock_config.llm_model = ""

        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = mock_config

        # Reset global LLM client and patch env vars
        from app.skills.llm_client import reset_llm_client
        import os
        reset_llm_client()
        mocker.patch.dict(os.environ, {}, clear=True)

        router = SkillRouter(db_session=mock_db, gitea_client=mocker.Mock())
        config = router._load_config()

        assert config["copilot_docs_limit"] == 20
        assert config["copilot_docs_size_limit"] == 50
        assert config["ai_max_tokens"] == 4000
        assert config["ai_context_limit"] == 30000

    def test_load_config_db_error(self, mocker):
        """Test _load_config handles database error."""
        mock_db = mocker.Mock()
        mock_db.query.side_effect = Exception("DB error")

        router = SkillRouter(db_session=mock_db, gitea_client=mocker.Mock())
        config = router._load_config()

        # Should return defaults
        assert config["copilot_docs_limit"] == 10

    @pytest.mark.asyncio
    async def test_route_to_help(self, mocker):
        """Test route dispatches to HelpSkill."""
        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = None

        router = SkillRouter(db_session=mock_db, gitea_client=mocker.Mock())

        result = await router.route("help", {}, None, {})
        assert "帮助" in result

    @pytest.mark.asyncio
    async def test_route_to_analyze(self, mocker):
        """Test route dispatches to AnalyzeSkill."""
        mock_llm = mocker.Mock()
        mock_llm.generate = mocker.AsyncMock(return_value="AI response")

        mock_git = mocker.Mock()
        mock_git.get_repo_file_content = mocker.AsyncMock(return_value="README content")

        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = None

        # Patch get_llm_client_from_config BEFORE creating SkillRouter
        mocker.patch("app.skills.router.get_llm_client_from_config", return_value=mock_llm)

        router = SkillRouter(db_session=mock_db, gitea_client=mock_git)

        payload = {"repository": {"full_name": "owner/repo"}}
        target = {"title": "Test issue", "body": "Question"}

        result = await router.route("如何部署", target, None, payload)
        assert result == "AI response"

    @pytest.mark.asyncio
    async def test_route_to_close(self, mocker):
        """Test route dispatches to CloseSkill."""
        mock_git = mocker.Mock()
        mock_git.close_issue = mocker.AsyncMock()

        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = None

        router = SkillRouter(db_session=mock_db, gitea_client=mock_git)

        payload = {"repository": {"full_name": "owner/repo"}, "issue": {"number": 123}}
        result = await router.route("close", {}, None, payload)

        assert result == ""

    @pytest.mark.asyncio
    async def test_route_to_open(self, mocker):
        """Test route dispatches to OpenSkill."""
        mock_git = mocker.Mock()
        mock_git.open_issue = mocker.AsyncMock()

        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = None

        router = SkillRouter(db_session=mock_db, gitea_client=mock_git)

        payload = {"repository": {"full_name": "owner/repo"}, "issue": {"number": 123}}
        result = await router.route("open", {}, None, payload)

        assert result == ""

    @pytest.mark.asyncio
    async def test_route_to_label(self, mocker):
        """Test route dispatches to LabelSkill."""
        mock_git = mocker.Mock()
        mock_git.add_issue_label = mocker.AsyncMock()

        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = None

        router = SkillRouter(db_session=mock_db, gitea_client=mock_git)

        payload = {"repository": {"full_name": "owner/repo"}, "issue": {"number": 123}}
        result = await router.route("label bug", {}, None, payload)

        assert result == ""