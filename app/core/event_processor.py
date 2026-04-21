import logging
from sqlalchemy.orm import Session
from ..models import GiteaInstance, GiteaAccount
from ..gitea import GiteaClient
from ..skills import SkillRouter
from typing import Dict, Any

logger = logging.getLogger("uvicorn.error")


class EventProcessor:
    """Process Gitea events and trigger AI skills."""

    def __init__(self, instance: GiteaInstance, account: GiteaAccount, db: Session):
        self.instance = instance
        self.account = account
        self.client = GiteaClient(
            instance.url, 
            account.access_token, 
            account_id=account.id, 
            db_session=db
        )
        # Use the account's Gitea username as the bot username
        self.bot_username = account.gitea_username

    async def process(self, event_type: str, payload: Dict[Any, Any], db: Session):
        """Process the webhook event."""
        logger.info(f"Processor.process called: event_type={event_type}")
        try:
            if event_type == "issue_comment":
                logger.info("Processing as issue_comment event")
                await self._process_issue_comment(payload, db)
            elif event_type == "issues":
                logger.info("Processing as issues event")
                await self._process_issue(payload, db)
            elif event_type == "pull_request":
                logger.info("Processing as pull_request event")
                await self._process_pull_request(payload, db)
            else:
                logger.info(f"Unhandled event type: {event_type}")

        except Exception as e:
            logger.error(f"Processing error: {e}", exc_info=True)

    async def _process_issue_comment(self, payload: Dict, db: Session):
        """Process issue comment event."""
        logger.info("=== _process_issue_comment called ===")

        comment = payload.get("comment", {})
        # Support both issue and pull_request fields
        # Gitea uses Issue API for PRs, so PR comments might come with pull_request field
        issue = payload.get("issue", {}) or payload.get("pull_request", {})
        repository = payload.get("repository", {})

        comment_body = comment.get("body", "")
        issue_number = issue.get("number")
        owner_repo = repository.get("full_name", "")
        sender = payload.get("sender", {}).get("login", "")

        logger.info(f"Comment body: '{comment_body}'")
        logger.info(f"Issue/PR number: {issue_number}")
        logger.info(f"Repository: {owner_repo}")
        logger.info(f"Sender: {sender}")
        logger.info(f"Bot username: {self.bot_username}")

        if not owner_repo or not issue_number:
            logger.warning("Missing repository or issue info")
            return

        # Check if comment mentions the bot (user's Gitea username)
        mention_pattern = f"@{self.bot_username}"
        has_mention = mention_pattern in comment_body
        logger.info(f"Checking for '{mention_pattern}' in comment: {has_mention}")

        if not has_mention:
            logger.info(f"Comment doesn't mention {mention_pattern}, skipping")
            return

        # Check if mention is inside a quote/reference block
        if self._is_mention_in_quote(comment_body):
            logger.info("Mention is inside a quote/reference block, skipping")
            return

        logger.info(f"Bot mentioned! Processing...")

        intents = self._extract_intents(comment_body)
        logger.info(f"Extracted intents: {intents}")

        responses = []
        for intent in intents:
            response = await self._route_to_skill(intent, issue, comment, payload, db)
            logger.info(f"Intent '{intent}' response: {response[:100] if response else 'None'}...")
            if response and response.strip():
                responses.append(response)

        # Combine all non-empty responses into one comment
        if responses:
            combined = "\n\n---\n\n".join(responses)
            combined = self._remove_self_mentions(combined)
            logger.info(f"Posting combined comment ({len(responses)} responses) to Gitea...")
            try:
                result = await self.client.create_comment(owner, repo, issue_number, combined)
                comment_id = result.get("id")
                logger.info(f"Comment posted successfully: {comment_id}")
                # Add hooray reaction to mark this comment as posted by bot
                if comment_id:
                    await self.client.add_comment_reaction(owner, repo, comment_id, "hooray")
                    logger.info(f"Added hooray reaction to comment {comment_id}")
            except Exception as e:
                logger.error(f"Failed to post comment: {e}", exc_info=True)

    def _is_mention_in_quote(self, text: str) -> bool:
        """Check if the bot mention is inside a quote/reference block.

        Gitea uses various formats for quotes:
        - <details><summary>引用...</summary>content</details>
        - > quoted text
        - ```quote blocks
        """
        import re

        mention_pattern = f"@{self.bot_username}"

        # Check for HTML quote formats (Gitea reference replies)
        # <details> block
        details_pattern = r'<details[^>]*>.*?</details>'
        details_matches = re.findall(details_pattern, text, re.DOTALL | re.IGNORECASE)
        for match in details_matches:
            if mention_pattern in match:
                return True

        # <summary> block
        summary_pattern = r'<summary[^>]*>.*?</summary>'
        summary_matches = re.findall(summary_pattern, text, re.DOTALL | re.IGNORECASE)
        for match in summary_matches:
            if mention_pattern in match:
                return True

        # Markdown quote blocks (> at line start)
        lines = text.split('\n')
        in_quote = False
        for line in lines:
            if line.strip().startswith('>'):
                in_quote = True
                if mention_pattern in line:
                    return True
            elif in_quote and line.strip() == '':
                # Empty line might end quote block
                in_quote = False
            elif not line.strip().startswith('>') and line.strip():
                in_quote = False

        # Code blocks with quote keyword
        code_quote_pattern = r'```quote.*?```'
        code_matches = re.findall(code_quote_pattern, text, re.DOTALL | re.IGNORECASE)
        for match in code_matches:
            if mention_pattern in match:
                return True

        return False

    def _remove_self_mentions(self, text: str) -> str:
        """Replace @mentions of the bot with @ space to prevent loop triggers.

        E.g., @caesar -> @ caesar (won't trigger webhook)
        """
        import re
        mention_pattern = f"@{self.bot_username}"
        # Replace @username with @ username (add space after @)
        return re.sub(mention_pattern, f"@ {self.bot_username}", text)

    async def _process_issue(self, payload: Dict, db: Session):
        """Process issue event."""
        issue = payload.get("issue", {})
        repository = payload.get("repository", {})
        sender = payload.get("sender", {}).get("login", "")

        owner_repo = repository.get("full_name", "")
        issue_number = issue.get("number")

        if not owner_repo or not issue_number:
            return

        issue_body = issue.get("body", "")
        if f"@{self.bot_username}" not in issue_body:
            return

        # Check if mention is in quote
        if self._is_mention_in_quote(issue_body):
            return

        owner, repo = owner_repo.split("/", 1)

        intent = self._extract_intents(issue_body)
        logger.info(f"Extracted intents from issue body: {intent}")

        responses = []
        for i in intent:
            response = await self._route_to_skill(i, issue, None, payload, db)
            if response and response.strip():
                responses.append(response)

        if responses:
            combined = "\n\n---\n\n".join(responses)
            combined = self._remove_self_mentions(combined)
            result = await self.client.create_comment(owner, repo, issue_number, combined)
            comment_id = result.get("id")
            if comment_id:
                await self.client.add_comment_reaction(owner, repo, comment_id, "hooray")
                logger.info(f"Added hooray reaction to comment {comment_id}")

    async def _process_pull_request(self, payload: Dict, db: Session):
        """Process pull request event."""
        pr = payload.get("pull_request", {})
        repository = payload.get("repository", {})

        owner_repo = repository.get("full_name", "")
        pr_number = pr.get("number")

        if not owner_repo or not pr_number:
            return

        pr_body = pr.get("body", "")
        if f"@{self.bot_username}" not in pr_body:
            return

        # Check if mention is in quote
        if self._is_mention_in_quote(pr_body):
            return

        owner, repo = owner_repo.split("/", 1)

        intent = self._extract_intents(pr_body)
        logger.info(f"Extracted intents from PR body: {intent}")

        responses = []
        for i in intent:
            response = await self._route_to_skill(i, pr, None, payload, db)
            if response and response.strip():
                responses.append(response)

        if responses:
            combined = "\n\n---\n\n".join(responses)
            combined = self._remove_self_mentions(combined)
            result = await self.client.create_comment(owner, repo, pr_number, combined)
            comment_id = result.get("id")
            if comment_id:
                await self.client.add_comment_reaction(owner, repo, comment_id, "hooray")
                logger.info(f"Added hooray reaction to comment {comment_id}")

    def _extract_intents(self, text: str) -> list[str]:
        """Extract all unique intents from bot mentions.

        Deduplicates by first keyword, preserves order and full intent text.

        Examples:
        - "@bot review @bot review @bot label bug" -> ["review", "label bug"]
        - "@bot help @bot help" -> ["help"]
        """
        import re
        pattern = f"@{re.escape(self.bot_username)}\\s*"

        intents = []
        seen_keywords = set()

        # Keyword groups that represent the same command
        keyword_aliases = {
            "help": ["help", "帮助", "?"],
            "label": ["label", "标签", "tag"],
            "review": ["review", "审核", "审查", "检查"],
            "close": ["close", "关闭"],
            "open": ["open", "打开", "reopen", "重开"],
        }

        # Build reverse lookup: keyword -> canonical name
        alias_to_canonical = {}
        for canonical, aliases in keyword_aliases.items():
            for alias in aliases:
                alias_to_canonical[alias.lower()] = canonical

        # Find all mentions and extract intents after them
        pos = 0
        while True:
            match = re.search(pattern, text[pos:])
            if not match:
                break

            # Start of intent is right after the mention
            intent_start = pos + match.end()

            # Find end of intent (next mention or end of text)
            next_mention = re.search(pattern, text[intent_start:])
            if next_mention:
                intent_end = intent_start + next_mention.start()
            else:
                intent_end = len(text)

            intent_text = text[intent_start:intent_end].strip()
            pos = intent_start

            if not intent_text:
                continue

            # Get first word as keyword
            words = intent_text.split()
            first_word = words[0].lower() if words else ""

            if not first_word:
                continue

            # Normalize keyword through alias mapping
            canonical = alias_to_canonical.get(first_word, first_word)

            if canonical not in seen_keywords:
                seen_keywords.add(canonical)
                intents.append(intent_text)

        return intents

    async def _route_to_skill(
        self,
        intent: str,
        target: Dict,
        comment: Dict | None,
        payload: Dict,
        db: Session
    ) -> str:
        """Route the intent to appropriate AI skill."""
        logger.info(f"Routing intent: {intent}")

        try:
            # Pass Gitea client to skill router for operations like labeling
            router = SkillRouter(db_session=db, gitea_client=self.client)
            return await router.route(intent, target, comment, payload)
        except Exception as e:
            logger.error(f"Skill routing error: {e}", exc_info=True)
            return "抱歉，处理您的请求时出现错误。请稍后重试。"