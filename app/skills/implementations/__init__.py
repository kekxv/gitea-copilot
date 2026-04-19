from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from ..llm_client import LLMClient
from ...gitea.base import BaseGitClient
import logging
import re
import json

logger = logging.getLogger(__name__)


class BaseSkill(ABC):
    """Base class for all skills, decoupled from specific Git platforms."""

    def __init__(
        self,
        llm: LLMClient,
        git_client: BaseGitClient,
        config: Optional[Dict[str, Any]] = None
    ):
        self.llm = llm
        self.git_client = git_client
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
        if "issue" in payload:
            return payload["issue"].get("number", 0)
        if "pull_request" in payload:
            return payload["pull_request"].get("number", 0)
        return 0


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
        parts = intent.strip().split()
        labels = [p for p in parts if p.lower() != "label"]
        if not labels: return ""

        owner, repo = self.get_repo_info(payload)
        issue_number = self.get_issue_number(payload)
        if not owner or not repo or not issue_number: return ""

        try:
            await self.git_client.add_issue_label(owner, repo, issue_number, labels)
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
        if not owner or not repo: return "抱歉，我暂时无法获取这个仓库的信息。"

        max_files = self.config.get("copilot_docs_limit", 10)
        max_size_kb = self.config.get("copilot_docs_size_limit", 25)

        # Get README (generic abstract client supports this via get_repo_file_content)
        readme = await self.git_client.get_repo_file_content(owner, repo, "README.md")
        
        # Get custom copilot docs from .gitea/copilot directory
        # (Note: For absolute purity, we'd abstract directory listing too, 
        # but for now we rely on the standard file content access)
        copilot_context = ""
        
        issue_title = target.get("title", "")
        issue_body = target.get("body", "")
        sender = payload.get("sender", {}).get("login", "用户")

        context_parts = []
        if readme: context_parts.append(f"**README:**\n{readme[:2000]}")
        if issue_title:
            context_parts.append(f"**当前话题:** {issue_title}")
            if issue_body: context_parts.append(f"**详细内容:** {issue_body[:500]}")

        context = "\n\n".join(context_parts) if context_parts else "暂无项目文档信息"
        system_prompt = """你是项目团队的 AI 助手，回答要求简洁、自然、直接。"""
        prompt = f"""{sender} 问：{intent}\n\n{context}"""
        response = await self.llm.generate(prompt, system_prompt, max_tokens=1500)
        return response


class ReviewSkill(BaseSkill):
    """Skill for reviewing pull request code with robust logic, security, and loop prevention."""

    async def execute(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Review PR code changes using abstract git client."""
        owner, repo = self.get_repo_info(payload)
        pr_number = self.get_issue_number(payload)
        original_comment_body = comment.get("body", "") if comment else "@caesar review"
        sender_login = payload.get("sender", {}).get("login", "")

        def scrub(text: str) -> str:
            if not text: return ""
            sensitive_keywords = r'token|key|password|secret|credential|auth|bearer|sha|md5|salt|api_key|access_token|private_key|passwd|pwd'
            patterns = [
                # Precise match: key: value or key=value
                (rf'(?i)\b({sensitive_keywords})\b\s*[:=：]\s*["\']?[a-zA-Z0-9_\-\.]{{8,}}["\']?', r'\1: [REDACTED]'),
                # Catch specific sk- tokens or other long random strings after identifiers
                (r'(?i)\b(secret|token|key)\b\s*[:=：\s]+\s*["\']?([a-zA-Z0-9_\-\.]{15,})["\']?', r'\1: [REDACTED]'),
            ]
            for p, r in patterns: text = re.sub(p, r, text)
            if sender_login:
                text = re.sub(rf"@{re.escape(sender_login)}\b", f"@ {sender_login}", text)
            return text

        if not payload.get("is_pull", False):
            return "这个命令只在 Pull Request 里有效哦 🙃"

        try:
            # 1. Get Context
            pr = await self.git_client.get_pull_request(owner, repo, pr_number)
            head_sha = pr.get("head", {}).get("sha")
            pr_title = pr.get("title", "")
            diff = await self.git_client.get_pull_request_diff(owner, repo, pr_number)
            if not diff: return "获取代码变更失败。"

            # 2. Build Whitelist
            file_changes = self._parse_diff(diff)
            valid_lines = {}
            for file in file_changes:
                path = file["path"]
                valid_lines[path] = {"new": set(), "old": set()}
                for l in file["lines"]:
                    if l.get("new_line") is not None: valid_lines[path]["new"].add(l["new_line"])
                    if l.get("old_line") is not None: valid_lines[path]["old"].add(l["old_line"])

            # 3. Chunking
            chunks = []
            current_chunk = []
            current_chunk_lines = 0
            for file in file_changes:
                if current_chunk_lines + len(file['lines']) > 800 and current_chunk:
                    chunks.append(current_chunk); current_chunk = []; current_chunk_lines = 0
                current_chunk.append(file); current_chunk_lines += len(file['lines'])
            if current_chunk: chunks.append(current_chunk)

            # 4. AI Process
            from ..tools import REVIEW_TOOLS, get_review_system_prompt
            all_comments = []
            all_summaries = []
            file_cache: Dict[str, str] = {}
            successfully_processed_chunks = 0

            for i, chunk in enumerate(chunks):
                diff_context = self._format_diff_for_review(chunk)
                async def handle_tool_call(tool_name: str, args: Dict) -> Dict:
                    if tool_name == "get_file_content":
                        path = args.get("path", "")
                        if path in file_cache: return {"path": path, "content": file_cache[path]}
                        content = await self.git_client.get_repo_file_content(owner, repo, path)
                        if content: file_cache[path] = content; return {"path": path, "content": content}
                        return {"error": "File not found"}
                    elif tool_name == "submit_review":
                        raw_c = args.get("comments", [])
                        if isinstance(raw_c, str):
                            try: raw_c = json.loads(raw_c)
                            except: pass
                        if isinstance(raw_c, dict): raw_c = [raw_c]
                        
                        for c in (raw_c or []):
                            path, body = c.get("path"), c.get("body")
                            if not path or not body: continue
                            nl = c.get("new_position") or c.get("new_line")
                            ol = c.get("old_position") or c.get("old_line")
                            nl = int(nl) if nl is not None else None
                            ol = int(ol) if ol is not None else None
                            
                            if path in valid_lines:
                                if nl and nl in valid_lines[path]["new"]: 
                                    all_comments.append({"path": path, "new_position": nl, "old_position": None, "body": body})
                                elif ol and ol in valid_lines[path]["old"]: 
                                    all_comments.append({"path": path, "new_position": None, "old_position": ol, "body": body})
                                else:
                                    all_summaries.append(f"**[{path}] 补充记录**: {body}")
                        
                        if args.get("summary"): all_summaries.append(args["summary"])
                        return {"success": True, "__break__": True}
                    return {"error": "Unknown tool"}

                prompt = f"审查 PR #{pr_number} (第 {i+1}/{len(chunks)} 部分)\n标题: {pr_title}\n\n{diff_context}"
                res, _ = await self.llm.generate_with_tools(prompt, get_review_system_prompt(), REVIEW_TOOLS, on_tool_call=handle_tool_call)
                if "AI 调用出错" in res:
                    raise Exception(f"AI 服务响应异常: {res}")
                successfully_processed_chunks += 1

            # 5. Finalize Result
            if successfully_processed_chunks == 0:
                raise Exception("无法从 AI 获取有效的审查结果。")

            unique_comments = []
            seen_keys = set()
            for c in all_comments:
                key = (c["path"], c["new_position"], c["old_position"], c["body"].strip())
                if key not in seen_keys: unique_comments.append(c); seen_keys.add(key)

            # Structured Report
            summary_text = "\n\n".join(list(dict.fromkeys(all_summaries)))
            
            # Handle LGTM
            has_issues = len(unique_comments) > 0 or any(kw in summary_text for kw in ["❌", "发现风险", "存在缺陷", "隐患", "建议修改"])
            final_body = "LGTM" if not has_issues else scrub(summary_text)

            # 6. Submit Unified Review
            api_comments = []
            for c in unique_comments:
                api_comments.append({
                    "path": c["path"],
                    "body": scrub(c["body"]),
                    "new_position": c["new_position"],
                    "old_position": c["old_position"]
                })

            await self.git_client.create_pull_request_review(
                owner, repo, pr_number,
                body=final_body,
                comments=api_comments,
                commit_id=head_sha
            )
            return "" 

        except Exception as e:
            logger.error(f"Review failed: {e}", exc_info=True)
            quoted = f"> {scrub(original_comment_body)}\n\n"
            return f"{quoted}❌ **代码审查失败**\n\n**原因**: {scrub(str(e))}"

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
        """Format diff for AI review."""
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
