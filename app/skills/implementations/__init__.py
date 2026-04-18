from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from ..llm_client import LLMClient
from ...gitea import GiteaClient
import logging

logger = logging.getLogger(__name__)


class BaseSkill(ABC):
    """Base class for all skills."""

    def __init__(
        self,
        llm: LLMClient,
        gitea: GiteaClient,
        config: Optional[Dict[str, Any]] = None
    ):
        self.llm = llm
        self.gitea = gitea
        self.config = config or {}

    @abstractmethod
    async def execute(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Execute the skill and return the response."""
        pass

    def get_repo_info(self, payload: Dict) -> tuple[str, str]:
        """Extract owner and repo from payload."""
        repository = payload.get("repository", {})
        full_name = repository.get("full_name", "")
        if "/" in full_name:
            return full_name.split("/", 1)
        return "", ""

    def get_issue_number(self, payload: Dict) -> int:
        """Extract issue number from payload."""
        issue = payload.get("issue", {})
        return issue.get("number", 0)


class HelpSkill(BaseSkill):
    """Skill for showing help information."""

    async def execute(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Show available commands in a friendly way."""
        return """👋 Hi! 我是你的项目助手，可以帮你：

**直接问我问题** - 我会看项目文档帮你分析
`@我 如何部署这个项目？`

**打标签** - 静默添加，不回复
`@我 label bug feature`

**Review PR** - 分析代码变更
`@我 review`

**帮助** - 显示这个信息
`@我 help`

有问题随时 @我 试试看！ 😊"""


class LabelSkill(BaseSkill):
    """Skill for adding labels to issues."""

    async def execute(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Add labels to the issue/PR - silently, no reply."""
        # Extract labels from intent (remove "label" keyword)
        parts = intent.strip().split()
        labels = [p for p in parts if p.lower() != "label"]

        if not labels:
            return ""  # No labels specified, silent fail

        owner, repo = self.get_repo_info(payload)
        issue_number = self.get_issue_number(payload)

        if not owner or not repo or not issue_number:
            return ""  # Invalid info, silent fail

        try:
            # Add labels via Gitea API
            await self.gitea.add_issue_label(owner, repo, issue_number, labels)
            logger.info(f"Added labels {labels} to {owner}/{repo}#{issue_number}")
        except Exception as e:
            logger.error(f"Failed to add labels: {e}", exc_info=True)

        # Always return empty string - no reply comment
        return ""


class AnalyzeSkill(BaseSkill):
    """Skill for answering questions based on project documentation."""

    async def execute(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Analyze project docs and answer the question naturally."""
        owner, repo = self.get_repo_info(payload)

        if not owner or not repo:
            return "抱歉，我暂时无法获取这个仓库的信息。"

        # Get config limits
        max_files = self.config.get("copilot_docs_limit", 10)
        max_size_kb = self.config.get("copilot_docs_size_limit", 25)

        # Get project documentation
        readme = await self.gitea.get_repo_readme(owner, repo)
        docs = await self.gitea.get_repo_docs(owner, repo)

        # Get custom copilot docs from .gitea/copilot directory
        copilot_docs = await self.gitea.get_copilot_docs(
            owner, repo, max_files=max_files, max_size_kb=max_size_kb
        )

        # Get issue/PR context
        issue_title = target.get("title", "")
        issue_body = target.get("body", "")
        sender = payload.get("sender", {}).get("login", "用户")

        context_parts = []

        # Copilot docs have highest priority (project-specific AI context)
        if copilot_docs:
            context_parts.append(f"**项目 AI 配置 (.gitea/copilot):**\n{copilot_docs}")

        if readme:
            context_parts.append(f"**README:**\n{readme[:2000]}")

        if docs:
            context_parts.append(f"**项目文档:**\n{docs[:2000]}")

        if issue_title:
            context_parts.append(f"**当前话题:** {issue_title}")
            if issue_body:
                context_parts.append(f"**详细内容:** {issue_body[:500]}")

        context = "\n\n".join(context_parts) if context_parts else "暂无项目文档信息"

        system_prompt = """你是项目团队的 AI 助手，正在和团队成员讨论问题。

回答要求：
1. 自然对话风格，像同事交流一样，不要用"根据您提供的信息"这种机械表达
2. 直接回答问题，简洁明了，避免过度解释
3. 如果知道答案，直接给出解决方案或步骤
4. 如果不确定，坦诚说明并提供可能的思路
5. 适当使用表情让对话更轻松
6. 回答要有针对性，关注用户真正想解决的问题
7. 如果项目有 .gitea/copilot 目录的配置文档，优先参考其中的内容

不要：
- 使用"我无法从文档中找到..."这种开头
- 写长篇大论的结构化内容
- 使用过多的标题和分隔线"""

        prompt = f"""{sender} 问：{intent}

{context}

请用自然的方式回答这个问题。"""

        logger.info(f"Analyzing question for {owner}/{repo}")
        response = await self.llm.generate(prompt, system_prompt, max_tokens=1500)
        return response


class ReviewSkill(BaseSkill):
    """Skill for reviewing pull request code."""

    async def execute(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Review PR code changes with natural feedback."""
        owner, repo = self.get_repo_info(payload)
        issue_number = self.get_issue_number(payload)

        # Check if this is a PR
        is_pr = payload.get("is_pull", False)
        if not is_pr:
            return "这个命令只在 Pull Request 里有效哦 🙃"

        if not owner or not repo:
            return "暂时无法获取仓库信息"

        try:
            # Get PR details and diff
            pr = await self.gitea.get_pull_request(owner, repo, issue_number)
            diff = await self.gitea.get_pull_request_diff(owner, repo, issue_number)

            if not diff:
                return "获取代码变更失败，可能 PR 还没有实际改动。"

            # Limit diff size for LLM
            diff_preview = diff[:4000] if len(diff) > 4000 else diff

            pr_title = pr.get("title", "")
            additions = pr.get("additions", 0)
            deletions = pr.get("deletions", 0)
            changed_files = pr.get("changed_files", 0)

            system_prompt = """你是团队的代码审查助手，正在帮同事 review 代码。

回答要求：
1. 自然对话风格，像同事交流一样
2. 重点放在有意义的问题上，不要过度关注格式细节
3. 发现问题时直接指出，给出具体的修改建议
4. 如果代码整体不错，简要肯定就好
5. 使用简洁的表达，避免长篇大论"""

            prompt = f"""Review 这个 PR：

**标题**: {pr_title}
**改动**: +{additions} -{deletions}, {changed_files} 个文件

```diff
{diff_preview}
```

请给出你的 review 意见。"""

            logger.info(f"Reviewing PR #{issue_number} for {owner}/{repo}")
            response = await self.llm.generate(prompt, system_prompt, max_tokens=1500)
            return f"🔍 Review 意见\n\n{response}"

        except Exception as e:
            logger.error(f"Failed to review PR: {e}", exc_info=True)
            return f"Review 出了点问题：{str(e)}"