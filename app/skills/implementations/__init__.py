from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from ..llm_client import LLMClient
from ...gitea import GiteaClient
import logging
import re
import json

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
            return ""

        owner, repo = self.get_repo_info(payload)
        issue_number = self.get_issue_number(payload)

        if not owner or not repo or not issue_number:
            return ""

        try:
            await self.gitea.add_issue_label(owner, repo, issue_number, labels)
            logger.info(f"Added labels {labels} to {owner}/{repo}#{issue_number}")
        except Exception as e:
            logger.error(f"Failed to add labels: {e}", exc_info=True)

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

        max_files = self.config.get("copilot_docs_limit", 10)
        max_size_kb = self.config.get("copilot_docs_size_limit", 25)

        readme = await self.gitea.get_repo_readme(owner, repo)
        docs = await self.gitea.get_repo_docs(owner, repo)
        copilot_docs = await self.gitea.get_copilot_docs(
            owner, repo, max_files=max_files, max_size_kb=max_size_kb
        )

        issue_title = target.get("title", "")
        issue_body = target.get("body", "")
        sender = payload.get("sender", {}).get("login", "用户")

        context_parts = []
        if copilot_docs: context_parts.append(f"**项目 AI 配置 (.gitea/copilot):**\n{copilot_docs}")
        if readme: context_parts.append(f"**README:**\n{readme[:2000]}")
        if docs: context_parts.append(f"**项目文档:**\n{docs[:2000]}")
        if issue_title:
            context_parts.append(f"**当前话题:** {issue_title}")
            if issue_body: context_parts.append(f"**详细内容:** {issue_body[:500]}")

        context = "\n\n".join(context_parts) if context_parts else "暂无项目文档信息"

        system_prompt = """你是项目团队的 AI 助手，正在和团队成员讨论问题。回答要求：简洁自然、直接给出解决方案、适当使用表情。"""
        prompt = f"""{sender} 问：{intent}\n\n{context}"""

        logger.info(f"Analyzing question for {owner}/{repo}")
        response = await self.llm.generate(prompt, system_prompt, max_tokens=1500)
        return response


class ReviewSkill(BaseSkill):
    """Skill for reviewing pull request code with tool calls."""

    async def execute(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Review PR code changes using grouped Review API with mandatory Commit ID."""
        owner, repo = self.get_repo_info(payload)
        pr_number = self.get_issue_number(payload)

        if not payload.get("is_pull", False):
            return "这个命令只在 Pull Request 里有效哦 🙃"

        try:
            # 1. Get PR Context including head SHA
            pr = await self.gitea.get_pull_request(owner, repo, pr_number)
            head_sha = pr.get("head", {}).get("sha")
            pr_title = pr.get("title", "")
            diff = await self.gitea.get_pull_request_diff(owner, repo, pr_number)
            if not diff: return "获取代码变更失败。"

            # 2. Build Line Whitelist
            file_changes = self._parse_diff(diff)
            valid_lines = {} 
            for file in file_changes:
                path = file["path"]
                valid_lines[path] = {"new": set(), "old": set()}
                for l in file["lines"]:
                    if l.get("new_line") is not None: valid_lines[path]["new"].add(l["new_line"])
                    if l.get("old_line") is not None: valid_lines[path]["old"].add(l["old_line"])

            # 3. Chunking for AI
            chunks = []
            current_chunk = []
            current_chunk_lines = 0
            for file in file_changes:
                if current_chunk_lines + len(file['lines']) > 800 and current_chunk:
                    chunks.append(current_chunk); current_chunk = []; current_chunk_lines = 0
                current_chunk.append(file); current_chunk_lines += len(file['lines'])
            if current_chunk: chunks.append(current_chunk)

            logger.info(f"Starting review for PR #{pr_number} at {head_sha[:8]}")

            # 4. Processing
            from ..tools import REVIEW_TOOLS, get_review_system_prompt
            all_comments = []
            all_summaries = []
            file_cache: Dict[str, str] = {}

            for i, chunk in enumerate(chunks):
                diff_context = self._format_diff_for_review(chunk)
                async def handle_tool_call(tool_name: str, args: Dict) -> Dict:
                    if tool_name == "get_file_content":
                        path = args.get("path", "")
                        if path in file_cache: return {"path": path, "content": file_cache[path]}
                        content = await self.gitea.get_repo_file_content(owner, repo, path)
                        if content: file_cache[path] = content; return {"path": path, "content": content}
                        return {"error": "File not found"}
                    elif tool_name == "submit_review":
                        raw_comments = args.get("comments", [])
                        if isinstance(raw_comments, str):
                            try: raw_comments = json.loads(raw_comments)
                            except: pass
                        if isinstance(raw_comments, dict): raw_comments = [raw_comments]
                        for c in raw_comments:
                            path, body = c.get("path"), c.get("body")
                            if not path or not body: continue
                            # Accept both old and new field names for robustness
                            nl = c.get("new_position") or c.get("new_line")
                            ol = c.get("old_position") or c.get("old_line")

                            nl = int(nl) if nl is not None else None
                            ol = int(ol) if ol is not None else None

                            # Validate
                            is_valid = False
                            if path in valid_lines:
                                if nl and nl in valid_lines[path]["new"]: is_valid = True
                                elif ol and ol in valid_lines[path]["old"]: is_valid = True
                            
                            if is_valid:
                                all_comments.append({"path": path, "new_line": nl, "old_line": ol, "body": body})
                            else:
                                all_summaries.append(f"**[{path}]**: {body}")
                        
                        if args.get("summary"): all_summaries.append(args["summary"])
                        return {"success": True, "__break__": True}
                    return {"error": "Unknown tool"}

                prompt = f"审查 PR #{pr_number} (第 {i+1}/{len(chunks)} 部分)\n标题: {pr_title}\n\n{diff_context}\n\n请分析并调用 submit_review。"
                await self.llm.generate_with_tools(prompt, get_review_system_prompt(), REVIEW_TOOLS, on_tool_call=handle_tool_call)

            # 5. Final Deduplication
            unique_comments = []
            seen_keys = set()
            for c in all_comments:
                key = (c["path"], c["new_line"], c["old_line"], c.get("body", "").strip())
                if key not in seen_keys: unique_comments.append(c); seen_keys.add(key)

            # 6. Submit Unified Review
            if unique_comments or all_summaries:
                api_comments = []
                for c in unique_comments:
                    # CRITICAL FIX: Gitea Review API uses 'new_position' and 'old_position'
                    api_c = {"path": c["path"], "body": c["body"]}
                    if c["new_line"]: api_c["new_position"] = c["new_line"]
                    if c["old_line"]: api_c["old_position"] = c["old_line"]
                    api_comments.append(api_c)

                final_summary = "🔍 **AI 代码审查报告**\n\n" + ("\n\n".join(list(dict.fromkeys(all_summaries))) or "代码整体质量良好。")

                # DEBUG: Output payload to log
                logger.info(f"Submitting Review to Gitea with {len(api_comments)} comments (using position fields)")
                debug_payload = {
                    "event": "COMMENT",
                    "body": final_summary[:100] + "...", 
                    "comments_count": len(api_comments),
                    "commit_id": head_sha,
                    "sample_comment": api_comments[0] if api_comments else None
                }
                logger.debug(f"REVIEW PAYLOAD DEBUG: {json.dumps(debug_payload, indent=2, ensure_ascii=False)}")

                await self.gitea.create_pull_request_review(
                    owner, repo, pr_number,
                    body=final_summary,
                    comments=api_comments,
                    commit_id=head_sha # MANDATORY FOR MOUNTING
                )
                
                # Return empty string to WebhookProcessor to avoid duplicate issue comment
                return ""
            
            return "审查完成，未发现明显问题。"

        except Exception as e:
            logger.error(f"Review failed: {e}", exc_info=True)
            return f"❌ Review 出错：{str(e)}"

    def _parse_diff(self, diff_text: str) -> List[Dict[str, Any]]:
        """Parse diff to get file changes with line numbers."""
        file_changes = []; current_file = None; current_old_line = 0; current_new_line = 0; lines_in_file = []
        for line in diff_text.split('\n'):
            if line.startswith('+++ b/'):
                if current_file and lines_in_file: file_changes.append({"path": current_file, "lines": lines_in_file})
                current_file = line[6:].strip(); current_old_line = 0; current_new_line = 0; lines_in_file = []
            elif line.startswith('---'): continue
            elif line.startswith('@@'):
                match = re.search(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
                if match: current_old_line = int(match.group(1)); current_new_line = int(match.group(2))
            elif current_file:
                if line.startswith('+') and not line.startswith('+++'):
                    lines_in_file.append({"new_line": current_new_line, "old_line": None, "type": "add", "content": line[1:]}); current_new_line += 1
                elif line.startswith('-') and not line.startswith('---'):
                    lines_in_file.append({"new_line": None, "old_line": current_old_line, "type": "del", "content": line[1:]}); current_old_line += 1
                elif line.startswith(' '):
                    lines_in_file.append({"new_line": current_new_line, "old_line": current_old_line, "type": "ctx", "content": line[1:]}); current_old_line += 1; current_new_line += 1
        if current_file and lines_in_file: file_changes.append({"path": current_file, "lines": lines_in_file})
        return file_changes

    def _format_diff_for_review(self, file_changes: List[Dict], max_lines: int = 1000) -> str:
        """Format diff for AI review with standard format."""
        result = []; total_lines = 0
        for file in file_changes:
            result.append(f"\n## File: {file['path']}")
            for l in file['lines']:
                if total_lines >= max_lines: break
                if l['type'] == 'add': label = f"[{l['new_line']}|+]"
                elif l['type'] == 'del': label = f"[{l['old_line']}|-]"
                else: label = f"[{l['new_line']}| ]"
                result.append(f"{label} {l['content']}"); total_lines += 1
        return '\n'.join(result)
