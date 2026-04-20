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
        issue = payload.get("issue", {})
        repository = payload.get("repository", {})

        comment_body = comment.get("body", "")
        issue_number = issue.get("number")
        owner_repo = repository.get("full_name", "")
        sender = payload.get("sender", {}).get("login", "")

        logger.info(f"Comment body: '{comment_body}'")
        logger.info(f"Issue number: {issue_number}")
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

        # Check sender's repository access permission (need write/admin)
        owner, repo = owner_repo.split("/", 1)
        if sender:
            # Owner always has access
            if sender == owner:
                logger.info(f"User '{sender}' is the repository owner, granting access")
                has_access = True
            else:
                logger.debug(f"Checking write/admin permission for user '{sender}' on {owner}/{repo}")
                has_access = await self.client.check_user_repo_access(owner, repo, sender)
                logger.debug(f"Permission check result: has_access={has_access}")
            
            if not has_access:
                logger.warning(f"User {sender} lacks write/admin permission for {owner}/{repo}, skipping")
                return

        logger.info(f"Bot mentioned! Processing...")

        intent = self._extract_intent(comment_body)
        logger.info(f"Extracted intent: '{intent}'")

        response = await self._route_to_skill(intent, issue, comment, payload, db)
        logger.info(f"AI response generated: {response[:100] if response else 'None'}...")

        if response and response.strip():
            # Remove any @mentions of self from the response
            response = self._remove_self_mentions(response)
            logger.info(f"Posting comment to Gitea...")
            try:
                result = await self.client.create_comment(owner, repo, issue_number, response)
                logger.info(f"Comment posted successfully: {result.get('id', 'unknown')}")
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

        # Check sender permission (need write/admin)
        if sender:
            logger.debug(f"Checking write/admin permission for user '{sender}' on {owner}/{repo}")
            has_access = await self.client.check_user_repo_access(owner, repo, sender)
            logger.debug(f"Permission check result: has_access={has_access}")
            if not has_access:
                logger.warning(f"User {sender} lacks write/admin permission for {owner}/{repo}, skipping")
                return

        intent = self._extract_intent(issue_body)
        response = await self._route_to_skill(intent, issue, None, payload, db)

        if response:
            response = self._remove_self_mentions(response)
            await self.client.create_comment(owner, repo, issue_number, response)

    async def _process_pull_request(self, payload: Dict, db: Session):
        """Process pull request event."""
        pr = payload.get("pull_request", {})
        repository = payload.get("repository", {})
        sender = payload.get("sender", {}).get("login", "")

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

        # Check sender permission (need write/admin)
        if sender:
            logger.debug(f"Checking write/admin permission for user '{sender}' on {owner}/{repo}")
            has_access = await self.client.check_user_repo_access(owner, repo, sender)
            logger.debug(f"Permission check result: has_access={has_access}")
            if not has_access:
                logger.warning(f"User {sender} lacks write/admin permission for {owner}/{repo}, skipping")
                return

        intent = self._extract_intent(pr_body)
        response = await self._route_to_skill(intent, pr, None, payload, db)

        if response:
            response = self._remove_self_mentions(response)
            await self.client.create_comment(owner, repo, pr_number, response)

    def _extract_intent(self, text: str) -> str:
        """Extract the intent from bot mention."""
        mention_start = text.find(f"@{self.bot_username}")
        if mention_start == -1:
            return ""

        mention_end = mention_start + len(self.bot_username) + 1
        remaining = text[mention_end:].strip()

        return remaining

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