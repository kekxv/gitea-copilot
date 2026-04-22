from typing import Dict, Any, Optional
from .llm_client import get_llm_client_from_config
from ..gitea import GiteaClient
from ..models import SystemConfig
import logging

logger = logging.getLogger("uvicorn.error")


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