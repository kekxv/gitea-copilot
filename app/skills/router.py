from typing import Dict, Any, Optional
from .llm_client import get_llm_client_from_config
from ..gitea import GiteaClient
from ..models import SystemConfig
import logging

logger = logging.getLogger("uvicorn.error")

PERMISSION_DENIED_MESSAGE = "权限不足：此命令需要仓库写入权限。"


class SkillRouter:
    """Route intents to appropriate skill handlers."""

    def __init__(
        self,
        db_session=None,
        gitea_client: GiteaClient = None
    ):
        self.db_session = db_session
        self.llm = get_llm_client_from_config(db_session)
        self.gitea = gitea_client
        self.config = self._load_config()
        self._permission_cache: Dict[tuple[str, str, str], bool] = {}

        # Intent keywords mapping
        self.intent_keywords = {
            "help": "help",
            "帮助": "help",
            "?": "help",
            "label": "label",
            "标签": "label",
            "tag": "label",
            "review": "review",
            "审核": "review",
            "审查": "review",
            "检查": "review",
            "close": "close",
            "关闭": "close",
            "open": "open",
            "打开": "open",
            "reopen": "open",
            "重开": "open",
        }

    def _load_config(self) -> Dict[str, Any]:
        """Load system config for skill limits."""
        config = {
            "copilot_docs_limit": 10,
            "copilot_docs_size_limit": 25,
            "ai_max_tokens": 8000,
            "ai_context_limit": 50000,
            "strip_emoji": False
        }

        if self.db_session:
            try:
                sys_config = self.db_session.query(SystemConfig).first()
                if sys_config:
                    if sys_config.copilot_docs_limit:
                        config["copilot_docs_limit"] = sys_config.copilot_docs_limit
                    if sys_config.copilot_docs_size_limit:
                        config["copilot_docs_size_limit"] = sys_config.copilot_docs_size_limit
                    if sys_config.ai_max_tokens:
                        config["ai_max_tokens"] = sys_config.ai_max_tokens
                    if sys_config.ai_context_limit:
                        config["ai_context_limit"] = sys_config.ai_context_limit
                    if sys_config.strip_emoji:
                        config["strip_emoji"] = sys_config.strip_emoji
            except Exception as e:
                logger.warning(f"Failed to load config: {e}")

        return config

    def classify_intent(self, intent: str) -> str:
        """Classify the intent to determine which skill to use."""
        intent_lower = intent.lower().strip()

        # Check for explicit commands
        for keyword, skill in self.intent_keywords.items():
            if intent_lower.startswith(keyword) or intent_lower == keyword:
                return skill

        # Default to analyze for questions/requests
        return "analyze"

    async def _has_write_access(self, payload: Dict[Any, Any]) -> bool:
        repository = payload.get("repository") or {}
        sender = payload.get("sender") or {}
        full_name = repository.get("full_name") or ""
        username = sender.get("login") or ""

        if not username or "/" not in full_name:
            logger.warning(
                "Denied bot command: incomplete repository or sender identity"
            )
            return False

        owner, repo = full_name.split("/", 1)
        if not owner or not repo or self.gitea is None:
            logger.warning(
                "Denied bot command: invalid repository identity for sender=%s",
                username,
            )
            return False

        cache_key = (owner, repo, username)
        if cache_key in self._permission_cache:
            return self._permission_cache[cache_key]

        try:
            allowed = await self.gitea.check_user_repo_access(
                owner, repo, username
            )
        except Exception:
            logger.warning(
                "Permission lookup failed for sender=%s repository=%s/%s",
                username,
                owner,
                repo,
                exc_info=True,
            )
            allowed = False

        self._permission_cache[cache_key] = allowed
        return allowed

    async def route(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Route the intent to the appropriate skill handler."""
        logger.info(f"=== SkillRouter.route called ===")
        logger.info(f"Intent: '{intent}'")

        skill_name = self.classify_intent(intent)
        logger.info(f"Classified as skill: {skill_name}")

        if not await self._has_write_access(payload):
            repository = payload.get("repository") or {}
            sender = payload.get("sender") or {}
            logger.warning(
                "Denied skill=%s sender=%s repository=%s",
                skill_name,
                sender.get("login") or "unknown",
                repository.get("full_name") or "unknown",
            )
            return PERMISSION_DENIED_MESSAGE

        from .implementations import HelpSkill, LabelSkill, AnalyzeSkill, ReviewSkill, CloseSkill, OpenSkill

        skill_map = {
            "help": HelpSkill,
            "label": LabelSkill,
            "analyze": AnalyzeSkill,
            "review": ReviewSkill,
            "close": CloseSkill,
            "open": OpenSkill,
        }

        skill_class = skill_map.get(skill_name, AnalyzeSkill)
        logger.info(f"Using skill class: {skill_class.__name__}")

        # Create skill instance with LLM, Gitea client, and config
        skill = skill_class(self.llm, self.gitea, self.config)

        logger.info(f"Executing skill...")
        result = await skill.execute(intent, target, comment, payload)
        logger.info(f"Skill result length: {len(result)} chars")

        return result
