from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from ..llm_client import LLMClient
from ...gitea.base import BaseGitClient
import logging
import re
import json
import asyncio

logger = logging.getLogger("uvicorn.error")


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

**关闭 Issue/PR** - 静默关闭
`@我 close`

**重新打开 Issue/PR** - 静默打开
`@我 open`

**帮助** - 显示这个信息
`@我 help`

有问题随时 @我 试试看！ 😊"""


class CloseSkill(BaseSkill):
    """Skill for closing issues and pull requests."""

    async def execute(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Close the issue/PR - silently, no reply."""
        owner, repo = self.get_repo_info(payload)
        issue_number = self.get_issue_number(payload)
        if not owner or not repo or not issue_number:
            return ""

        try:
            await self.git_client.close_issue(owner, repo, issue_number)
            logger.info(f"Closed {owner}/{repo}#{issue_number}")
        except Exception as e:
            logger.error(f"Failed to close issue: {e}", exc_info=True)
        return ""


class OpenSkill(BaseSkill):
    """Skill for opening/reopening issues and pull requests."""

    async def execute(
        self,
        intent: str,
        target: Dict[Any, Any],
        comment: Optional[Dict],
        payload: Dict[Any, Any]
    ) -> str:
        """Open/reopen the issue/PR - silently, no reply."""
        owner, repo = self.get_repo_info(payload)
        issue_number = self.get_issue_number(payload)
        if not owner or not repo or not issue_number:
            return ""

        try:
            await self.git_client.open_issue(owner, repo, issue_number)
            logger.info(f"Opened {owner}/{repo}#{issue_number}")
        except Exception as e:
            logger.error(f"Failed to open issue: {e}", exc_info=True)
        return ""


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
        """Analyze project docs and answer the question naturally.

        If intent is empty (just @mention without question), analyze and reply to
        the issue/PR title and body content.
        """
        owner, repo = self.get_repo_info(payload)
        if not owner or not repo: return "抱歉，我暂时无法获取这个仓库的信息。"

        issue_title = target.get("title", "")
        issue_body = target.get("body", "")
        sender = payload.get("sender", {}).get("login", "用户")

        # Get README for context
        readme = await self.git_client.get_repo_file_content(owner, repo, "README.md")

        # Build context
        context_parts = []
        if readme: context_parts.append(f"**README:**\n{readme[:2000]}")
        if issue_title:
            context_parts.append(f"**标题:** {issue_title}")
            if issue_body: context_parts.append(f"**内容:**\n{issue_body}")

        context = "\n\n".join(context_parts) if context_parts else "暂无项目文档信息"

        # Determine the prompt based on intent
        intent_clean = intent.strip()

        if not intent_clean:
            # User only @mentioned without question - analyze and reply to main content
            system_prompt = """你是项目团队的 AI 助手。用户 @你但没有提问，请分析当前 Issue/PR 的内容，给出有用的回复或建议。

如果内容是问题描述，尝试给出解决方案或分析原因。
如果内容是功能请求，给出实现建议或相关讨论。
如果内容是 PR，简要说明改动内容。
回答要简洁、自然、直接。"""
            prompt = f"""{sender} 在以下内容中 @了你，请分析并回复：

**标题:** {issue_title}
{context}"""
        else:
            # User has a specific question - answer it with full context
            system_prompt = """你是项目团队的 AI 助手，回答要求简洁、自然、直接。
请结合当前 Issue/PR 的完整内容来回答用户的问题。"""
            prompt = f"""{sender} 问：{intent_clean}

**当前 Issue/PR:**
标题: {issue_title}
{context}"""

        max_tokens = self.config.get("ai_max_tokens", 1500)
        response = await self.llm.generate(prompt, system_prompt, max_tokens=max_tokens)
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
        strip_emoji = self.config.get("strip_emoji", False)

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
            # Remove emojis if configured (to avoid Gitea frontend editing issues)
            if strip_emoji:
                emoji_pattern = re.compile(
                    '['
                    '\U0001F300-\U0001F9FF'  # Misc Symbols and Pictographs, Emoticons, etc.
                    '\U00002600-\U000027BF'  # Misc symbols
                    '\U0001F600-\U0001F64F'  # Emoticons
                    '\U0001F680-\U0001F6FF'  # Transport & Map
                    '\U0001F1E0-\U0001F1FF'  # Flags
                    '\U00002702-\U000027B0'  # Dingbats
                    '\U000024C2-\U0001F251'  # Enclosed characters
                    ']+', flags=re.UNICODE)
                text = emoji_pattern.sub('', text)
            return text

        # Check if this is a PR - payload should have pull_request field
        # or target might have pull_request field indicating it's a PR
        is_pull = payload.get("is_pull") or payload.get("pull_request") is not None or target.get("pull_request") is not None
        if not is_pull:
            return "这个命令只在 Pull Request 里有效哦 🙃"

        try:
            # 1. Get Context
            pr = await self.git_client.get_pull_request(owner, repo, pr_number)
            head_sha = pr.get("head", {}).get("sha")
            pr_title = pr.get("title", "")
            pr_author = pr.get("user", {}).get("login", "")
            diff = await self.git_client.get_pull_request_diff(owner, repo, pr_number)
            if not diff: return "获取代码变更失败。"

            # Check if bot is reviewing its own PR - cannot use REQUEST_CHANGES
            current_user = await self.git_client.get_current_user()
            current_username = current_user.get("login", "")
            is_own_pr = pr_author == current_username
            if is_own_pr:
                logger.info(f"Bot ({current_username}) is reviewing own PR, REQUEST_CHANGES not allowed")

            # 2. Build Whitelist
            file_changes = self._parse_diff(diff)
            valid_lines = {}
            for file in file_changes:
                path = file["path"]
                valid_lines[path] = {"new": set(), "old": set()}
                for l in file["lines"]:
                    if l.get("new_line") is not None: valid_lines[path]["new"].add(l["new_line"])
                    if l.get("old_line") is not None: valid_lines[path]["old"].add(l["old_line"])
            logger.debug(f"📋 Valid lines for review: {dict((k, {'new': sorted(v['new']), 'old': sorted(v['old'])}) for k, v in valid_lines.items())}")

            # 3. Chunking
            chunks = []
            current_chunk = []
            current_chunk_lines = 0
            for file in file_changes:
                if current_chunk_lines + len(file['lines']) > 800 and current_chunk:
                    chunks.append(current_chunk); current_chunk = []; current_chunk_lines = 0
                current_chunk.append(file); current_chunk_lines += len(file['lines'])
            if current_chunk: chunks.append(current_chunk)

            # 4. AI Process with retry mechanism
            from ..tools import REVIEW_TOOLS, get_review_system_prompt
            max_retries = 5
            retry_delay = 15  # seconds

            all_comments = []
            all_summaries = []
            all_events = []
            successfully_processed_chunks = 0

            for retry in range(max_retries):
                all_comments = []
                all_summaries = []
                all_events = []
                file_cache: Dict[str, str] = {}
                successfully_processed_chunks = 0
                ai_error = None

                try:
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

                                logger.debug(f"📝 submit_review called with {len(raw_c or [])} comments")

                                # Collect event from AI
                                event = args.get("event", "COMMENT")
                                all_events.append(event)
                                logger.info(f"   AI event: {event}")

                                for c in (raw_c or []):
                                    path, body = c.get("path"), c.get("body")
                                    if not path or not body: continue
                                    nl = c.get("new_position") or c.get("new_line")
                                    ol = c.get("old_position") or c.get("old_line")
                                    nl = int(nl) if nl is not None else None
                                    ol = int(ol) if ol is not None else None

                                    # Check if path exists in valid_lines
                                    if path not in valid_lines:
                                        all_summaries.append(f"**[{path}] 补充记录**: {body}")
                                        logger.debug(f"   ⚠ Path {path} not in diff, added to summary")
                                        continue

                                    if nl and nl in valid_lines[path]["new"]:
                                        all_comments.append({"path": path, "new_position": nl, "old_position": None, "body": body})
                                        logger.debug(f"   ✓ Comment on {path}:{nl} (new)")
                                    elif ol and ol in valid_lines[path]["old"]:
                                        all_comments.append({"path": path, "new_position": None, "old_position": ol, "body": body})
                                        logger.debug(f"   ✓ Comment on {path}:{ol} (old)")
                                    else:
                                        all_summaries.append(f"**[{path}] 补充记录**: {body}")
                                        logger.debug(f"   ⚠ Comment on {path} (nl={nl}, ol={ol}) not in valid range, added to summary")

                                if args.get("summary"):
                                    all_summaries.append(args["summary"])
                                    logger.debug(f"   Summary: {args['summary'][:100]}...")
                                return {"success": True, "__break__": True}
                            return {"error": "Unknown tool"}

                        prompt = f"审查 PR #{pr_number} (第 {i+1}/{len(chunks)} 部分)\n标题: {pr_title}\n\n{diff_context}"
                        logger.debug(f"🔍 Processing chunk {i+1}/{len(chunks)} with {len(chunk)} files: {[f['path'] for f in chunk]}")
                        system_prompt = get_review_system_prompt(len(chunks))
                        res, _ = await self.llm.generate_with_tools(prompt, system_prompt, REVIEW_TOOLS, on_tool_call=handle_tool_call)
                        logger.debug(f"✅ Chunk {i+1}/{len(chunks)} completed, collected {len(all_comments)} comments so far")
                        if "AI 调用出错" in res:
                            raise Exception(f"AI 服务响应异常: {res}")
                        successfully_processed_chunks += 1

                    # Check results
                    if successfully_processed_chunks == 0:
                        raise Exception("无法从 AI 获取有效的审查结果。")
                    if not all_summaries:
                        raise Exception("AI 审核服务异常，未返回审查摘要。")

                    # Success - break out of retry loop
                    logger.info(f"AI review completed successfully")
                    break

                except Exception as e:
                    ai_error = str(e)
                    logger.warning(f"AI review attempt {retry + 1}/{max_retries} failed: {e}")
                    if retry < max_retries - 1:
                        logger.info(f"Retrying in {retry_delay} seconds...")
                        await asyncio.sleep(retry_delay)
                    else:
                        logger.error(f"All {max_retries} retries exhausted, review failed")
                        # Don't submit error comment, just return empty
                        return ""

            # 5. Finalize Result
            if not all_summaries:
                logger.error("No summaries collected after retries")
                return ""

            unique_comments = []
            seen_keys = set()
            for c in all_comments:
                key = (c["path"], c["new_position"], c["old_position"], c["body"].strip())
                if key not in seen_keys: unique_comments.append(c); seen_keys.add(key)

            # Use AI's summary directly
            summary_text = "\n\n".join(list(dict.fromkeys(all_summaries)))
            final_body = scrub(summary_text)

            # Determine final event
            if is_own_pr:
                # Bot cannot reject its own PR, force to COMMENT
                final_event = "COMMENT"
                logger.info(f"Final event: COMMENT (own PR)")
            else:
                # Normal review: use REQUEST_CHANGES if AI found issues
                normalized_events = []
                for e in all_events:
                    if e == "APPROVED":
                        normalized_events.append("COMMENT")
                    else:
                        normalized_events.append(e)

                if "REQUEST_CHANGES" in normalized_events:
                    final_event = "REQUEST_CHANGES"
                else:
                    final_event = "COMMENT"

                logger.info(f"Final event: {final_event}")

            # 6. Submit Unified Review
            api_comments = []
            for c in unique_comments:
                api_c = {
                    "path": c["path"],
                    "body": scrub(c["body"])
                }
                # Only include position fields if they're not None
                if c["new_position"] is not None:
                    api_c["new_position"] = c["new_position"]
                if c["old_position"] is not None:
                    api_c["old_position"] = c["old_position"]
                api_comments.append(api_c)

            await self.git_client.create_pull_request_review(
                owner, repo, pr_number,
                body=final_body,
                comments=api_comments,
                event=final_event,
                commit_id=head_sha
            )
            logger.info(f"Review submitted: event={final_event}, {len(api_comments)} comments")
            return ""

        except Exception as e:
            logger.error(f"Review failed: {e}", exc_info=True)
            # Don't submit error comment, just return empty
            return ""

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
