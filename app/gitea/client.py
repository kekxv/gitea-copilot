from .base import BaseGitClient
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import httpx
import json
import time
import logging
import base64
import hmac
import hashlib

logger = logging.getLogger("uvicorn.error")


class GiteaClient(BaseGitClient):
    """Client for interacting with Gitea REST API with automatic token management."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        account_id: Optional[int] = None,
        db_session: Optional[Any] = None
    ):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.account_id = account_id
        self.db_session = db_session
        self._server_version: Optional[str] = None  # Cache server version for compatibility

    async def _ensure_valid_token(self):
        """Check if token is about to expire and refresh if needed (OAuth mode only)."""
        if not self.account_id or not self.db_session:
            return

        from ..models import GiteaAccount, GiteaInstance

        account = self.db_session.query(GiteaAccount).filter(GiteaAccount.id == self.account_id).first()
        if not account:
            return

        # Token mode doesn't need refresh - token is managed externally
        if account.auth_mode == "token":
            return

        # OAuth mode: check if token expires
        if not account.token_expires_at:
            return

        # Refresh if expires in less than 10 minutes
        if datetime.utcnow() + timedelta(minutes=10) >= account.token_expires_at:
            logger.info(f"Token for account {self.account_id} is near expiry, refreshing...")

            instance = self.db_session.query(GiteaInstance).filter(GiteaInstance.id == account.instance_id).first()
            if not instance:
                logger.error("Cannot refresh token: Instance not found")
                return

            from ..tasks.token_manager import refresh_token
            success = await refresh_token(account, instance)

            if success:
                self.db_session.commit()
                self.access_token = account.access_token
                logger.info(f"Successfully refreshed token for account {self.account_id}")
            else:
                logger.error(f"Failed to refresh token for account {self.account_id}")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"token {self.access_token}",
            "Accept": "application/json"
        }

    async def get_server_version(self) -> str:
        """Get Gitea server version with caching."""
        if self._server_version:
            return self._server_version
        try:
            result = await self._request("GET", "/version")
            self._server_version = result.get("version", "")
            logger.info(f"Gitea server version: {self._server_version}")
            return self._server_version
        except Exception as e:
            logger.warning(f"Failed to get server version: {e}")
            return ""

    def _is_legacy_version(self, version: str) -> bool:
        """Check if version needs legacy API handling (<= 1.23.x)."""
        if not version:
            return True  # Unknown version, use safe defaults
        try:
            # Parse version like "1.23.6" or "1.24.0"
            parts = version.split(".")
            major = int(parts[0]) if len(parts) > 0 else 0
            minor = int(parts[1]) if len(parts) > 1 else 0
            # Versions <= 1.23.x need legacy handling
            return major <= 1 and minor <= 23
        except:
            return True

    async def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[Any, Any]:
        """Make a request to Gitea API with auto-refresh check."""
        await self._ensure_valid_token()
        
        url = f"{self.base_url}/api/v1{path}"
        
        if data and method == "POST":
            logger.debug(f"Gitea API POST {path} | Payload: {json.dumps(data, ensure_ascii=False)}")

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                url,
                headers=self._headers(),
                json=data,
                params=params
            )

            if response.status_code == 401:
                logger.warning(f"Gitea API 401 Unauthorized for {path}. Token might have been revoked.")
                # We could try one more refresh here if needed

            if response.status_code not in (200, 201, 204, 205):
                error_body = response.text[:500] if response.text else ""
                logger.error(f"Gitea API error: status {response.status_code} on {path} | Body: {error_body}")
                raise Exception(f"Gitea API error: {response.status_code} - {error_body}")

            if response.status_code == 204:
                return {}

            return response.json()

    async def _request_raw(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None
    ) -> str:
        """Make a request and return raw text content."""
        await self._ensure_valid_token()
        url = f"{self.base_url}/api/v1{path}"

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                url,
                headers=self._headers(),
                params=params
            )

            if response.status_code != 200:
                logger.error(f"Gitea API error: status {response.status_code}")
                raise Exception(f"Gitea API error: {response.status_code}")

            return response.text

    # ============ Repository Operations ============

    async def get_current_user(self) -> Dict:
        """Get current authenticated user info."""
        return await self._request("GET", "/user")

    async def get_user_repos(self) -> List[Dict]:
        """Get list of repositories for the authenticated user."""
        return await self._request("GET", "/user/repos")

    async def get_repo(self, owner: str, repo: str) -> Dict:
        """Get a specific repository."""
        return await self._request("GET", f"/repos/{owner}/{repo}")

    async def check_user_repo_access(self, owner: str, repo: str, username: str) -> bool:
        """Check if a user has write/admin permission to a repository.

        Returns True if user has write or admin permission.
        Read-only permission is NOT sufficient for triggering AI.

        Uses: /repos/{owner}/{repo}/collaborators/{username}/permission
        """
        try:
            # Get user's permission for this repo
            permission_info = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/collaborators/{username}/permission"
            )

            logger.debug(f"Permission info for '{username}' on {owner}/{repo}: {permission_info}")

            # Permission structure: {"permission": "admin"} or {"permission": "write"} etc.
            # Possible values: "read", "write", "admin", "owner"
            permission = permission_info.get("permission", "").lower()

            logger.debug(f"User '{username}' has permission '{permission}' on {owner}/{repo}")

            # Only write, admin, owner permissions are allowed
            allowed_permissions = ["write", "admin", "owner"]

            if permission in allowed_permissions:
                logger.debug(f"User '{username}' has sufficient permission ({permission}) for AI operations")
                return True

            logger.debug(f"User '{username}' has insufficient permission ({permission}), need write/admin")
            return False

        except Exception as e:
            logger.warning(f"Cannot check permission for {username} on {owner}/{repo}: {e}")
            # If permission check fails, deny access
            return False

    async def get_repo_contents(self, owner: str, repo: str, path: str = "") -> Dict:
        """Get contents of a file or directory in repository."""
        return await self._request("GET", f"/repos/{owner}/{repo}/contents/{path}")

    async def get_repo_file_content(self, owner: str, repo: str, path: str) -> Optional[str]:
        """Get content of a file in repository (decoded)."""
        try:
            result = await self._request("GET", f"/repos/{owner}/{repo}/contents/{path}")
            if result.get("type") == "file" and result.get("content"):
                return base64.b64decode(result["content"]).decode("utf-8")
            return None
        except Exception as e:
            logger.warning(f"Failed to get file {path}: {e}")
            return None

    async def get_repo_readme(self, owner: str, repo: str) -> Optional[str]:
        """Get README.md content from repository."""
        readme_variants = ["README.md", "readme.md", "README", "readme"]
        for name in readme_variants:
            content = await self.get_repo_file_content(owner, repo, name)
            if content:
                return content
        return None

    async def get_repo_docs(self, owner: str, repo: str) -> str:
        """Get documentation files from repository."""
        docs_content = []

        doc_files = ["doc.md", "docs.md", "DOC.md", "DOCS.md"]
        doc_dirs = ["docs", "doc", "documentation"]

        # Check for doc files in root
        for filename in doc_files:
            content = await self.get_repo_file_content(owner, repo, filename)
            if content:
                docs_content.append(f"=== {filename} ===\n{content[:2000]}")

        # Check for doc directories
        for dirname in doc_dirs:
            try:
                dir_contents = await self.get_repo_contents(owner, repo, dirname)
                if isinstance(dir_contents, list):
                    for item in dir_contents[:5]:  # Limit to 5 files
                        if item.get("type") == "file" and item.get("name", "").endswith(".md"):
                            filepath = f"{dirname}/{item['name']}"
                            content = await self.get_repo_file_content(owner, repo, filepath)
                            if content:
                                docs_content.append(f"=== {filepath} ===\n{content[:2000]}")
            except Exception:
                pass

        return "\n\n".join(docs_content) if docs_content else ""

    async def get_copilot_docs(
        self,
        owner: str,
        repo: str,
        max_files: int = 10,
        max_size_kb: int = 25
    ) -> str:
        """Get custom AI context docs from .gitea/copilot directory.

        Args:
            owner: Repository owner
            repo: Repository name
            max_files: Maximum number of files to include
            max_size_kb: Maximum total size in KB

        Returns:
            Combined content of all .md files in .gitea/copilot/
        """
        docs_content = []
        total_size = 0
        max_size_bytes = max_size_kb * 1024

        try:
            # Get .gitea/copilot directory contents
            dir_contents = await self.get_repo_contents(owner, repo, ".gitea/copilot")

            if not isinstance(dir_contents, list):
                logger.info(f".gitea/copilot directory not found or empty in {owner}/{repo}")
                return ""

            # Filter .md files
            md_files = [
                item for item in dir_contents
                if item.get("type") == "file" and item.get("name", "").endswith(".md")
            ]

            logger.info(f"Found {len(md_files)} .md files in .gitea/copilot")

            # Process files up to limits
            for item in md_files[:max_files]:
                filepath = f".gitea/copilot/{item['name']}"
                try:
                    content = await self.get_repo_file_content(owner, repo, filepath)
                    if content:
                        file_size = len(content.encode('utf-8'))
                        if total_size + file_size > max_size_bytes:
                            logger.info(f"Reached size limit, stopping at {filepath}")
                            break

                        docs_content.append(f"=== {item['name']} ===\n{content}")
                        total_size += file_size
                        logger.info(f"Added {filepath} ({file_size} bytes)")

                except Exception as e:
                    logger.warning(f"Failed to get {filepath}: {e}")

        except Exception as e:
            logger.info(f"Cannot access .gitea/copilot in {owner}/{repo}: {e}")
            return ""

        logger.info(f"Total copilot docs: {len(docs_content)} files, {total_size} bytes")
        return "\n\n".join(docs_content) if docs_content else ""

    # ============ Issue Operations ============

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> Dict:
        """Get an issue or PR."""
        return await self._request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}")

    async def get_issue_labels(self, owner: str, repo: str, issue_number: int) -> List[Dict]:
        """Get labels for an issue."""
        return await self._request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}/labels")

    async def add_issue_label(self, owner: str, repo: str, issue_number: int, label_names: List[str]) -> Dict:
        """Add labels to an issue/PR."""
        # First get repo labels to find IDs
        repo_labels = await self._request("GET", f"/repos/{owner}/{repo}/labels")
        label_map = {l["name"]: l["id"] for l in repo_labels}

        # Create labels if they don't exist
        label_ids = []
        for name in label_names:
            if name in label_map:
                label_ids.append(label_map[name])
            else:
                # Create new label with a default color
                try:
                    # Generate a simple color based on label name hash for variety
                    import hashlib
                    hash_val = int(hashlib.md5(name.encode()).hexdigest()[:6], 16)
                    color = f"#{(hash_val % 0xFFFFFF):06x}"
                    new_label = await self._request("POST", f"/repos/{owner}/{repo}/labels", data={"name": name, "color": color})
                    label_ids.append(new_label["id"])
                except Exception as e:
                    logger.warning(f"Failed to create label {name}: {e}")

        if label_ids:
            return await self._request("POST", f"/repos/{owner}/{repo}/issues/{issue_number}/labels", data={"labels": label_ids})
        return {}

    async def create_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str
    ) -> Dict:
        """Create a comment on an issue or PR."""
        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            data={"body": body}
        )

    async def close_issue(self, owner: str, repo: str, issue_number: int) -> Dict:
        """Close an issue or PR.

        In Gitea, PRs are also issues, so this works for both.
        Uses PATCH to update state to 'closed'.
        """
        return await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            data={"state": "closed"}
        )

    async def open_issue(self, owner: str, repo: str, issue_number: int) -> Dict:
        """Open/reopen an issue or PR.

        In Gitea, PRs are also issues, so this works for both.
        Uses PATCH to update state to 'open'.
        """
        return await self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            data={"state": "open"}
        )

    # ============ Pull Request Operations ============

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> Dict:
        """Get pull request details."""
        return await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")

    async def get_pull_request_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Get diff for a pull request."""
        try:
            return await self._request_raw("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}.diff")
        except Exception as e:
            logger.error(f"Failed to get PR diff: {e}")
            return ""

    async def get_pull_request_files(self, owner: str, repo: str, pr_number: int) -> List[Dict]:
        """Get list of files changed in a pull request."""
        return await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}/files")

    async def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: List[Dict[str, Any]],
        event: str = "COMMENT",
        commit_id: Optional[str] = None
    ) -> Dict:
        """Create a review on a pull request with version-aware compatibility."""
        # Get server version for compatibility handling
        version = await self.get_server_version()

        data = {
            "event": event,
            "body": body
        }

        # Version-aware handling for comments
        # Gitea <= 1.23.x doesn't accept empty comments array
        if comments:
            data["comments"] = comments
        elif not self._is_legacy_version(version):
            # New versions can accept empty array
            data["comments"] = []

        # commit_id handling (optional field)
        if commit_id:
            data["commit_id"] = commit_id

        url = f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        return await self._request("POST", url, data=data)

    async def get_pull_review_comments(self, owner: str, repo: str, pr_number: int, review_id: int) -> List[Dict]:
        """Get comments for a specific PR review."""
        return await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews/{review_id}/comments")

    async def get_all_pull_comments(self, owner: str, repo: str, pr_number: int) -> List[Dict]:
        """Get all comments for a PR (includes both issue comments and review comments)."""
        return await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}/comments")

    async def get_pull_request_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        path: str,
        body: str,
        new_line: Optional[int] = None,
        old_line: Optional[int] = None
    ) -> Dict:
        """Create an individual line comment on a pull request.
        
        This is more robust than the Review API for line mounting.
        """
        data = {
            "path": path,
            "body": body
        }
        if new_line: data["new_line"] = new_line
        if old_line: data["old_line"] = old_line

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            data=data
        )

    # ============ Notification Operations ============

    async def get_notifications(
        self,
        all_notifications: bool = False,
        since: Optional[datetime] = None,
        limit: int = 50
    ) -> List[Dict]:
        """Fetch notifications for the authenticated user."""
        params = {
            "all": "true" if all_notifications else "false",
            "limit": limit
        }
        if since:
            # Gitea expects RFC3339/ISO 8601 format like YYYY-MM-DDTHH:MM:SSZ
            # Ensure no microseconds and add Z suffix for UTC
            params["since"] = since.replace(microsecond=0).isoformat() + "Z"

        # Correct Gitea API path is /notifications, not /user/notifications
        return await self._request("GET", "/notifications", params=params)

    async def mark_notification_as_read(self, notification_id: int) -> bool:
        """Mark a notification as read.
        
        Uses PATCH /notifications/threads/{id} for specific thread.
        """
        try:
            await self._request("PATCH", f"/notifications/threads/{notification_id}")
            return True
        except Exception:
            return False

    async def get_comment_by_id(self, owner: str, repo: str, comment_id: int) -> Dict:
        """Get an issue/PR comment by its ID."""
        return await self._request("GET", f"/repos/{owner}/{repo}/issues/comments/{comment_id}")

    async def get_issue_comments(self, owner: str, repo: str, issue_number: int) -> List[Dict]:
        """Get all comments for an issue or pull request."""
        return await self._request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}/comments")

    # ============ Reaction Operations ============

    async def get_comment_reactions(self, owner: str, repo: str, comment_id: int) -> List[Dict]:
        """Get reactions for an issue/PR comment."""
        return await self._request("GET", f"/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions")

    async def add_comment_reaction(self, owner: str, repo: str, comment_id: int, reaction: str) -> Dict:
        """Add a reaction to an issue/PR comment.

        Args:
            reaction: Emoji name like 'eyes', '+1', 'heart', etc.
        """
        return await self._request("POST", f"/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions", data={"content": reaction})

    async def get_issue_reactions(self, owner: str, repo: str, issue_number: int) -> List[Dict]:
        """Get reactions for an issue/PR."""
        return await self._request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}/reactions")

    async def add_issue_reaction(self, owner: str, repo: str, issue_number: int, reaction: str) -> Dict:
        """Add a reaction to an issue/PR.

        Args:
            reaction: Emoji name like 'eyes', '+1', 'heart', etc.
        """
        return await self._request("POST", f"/repos/{owner}/{repo}/issues/{issue_number}/reactions", data={"content": reaction})

    async def has_bot_reaction(self, owner: str, repo: str, issue_number: int, comment_id: Optional[int], reaction: str, bot_username: str) -> bool:
        """Check if bot has already added a specific reaction.

        Args:
            issue_number: Issue/PR number
            comment_id: Comment ID (None for issue body reactions)
            reaction: Reaction content to check (e.g., 'eyes')
            bot_username: Bot's Gitea username to match

        Returns:
            True if bot has this reaction on the item
        """
        if comment_id:
            reactions = await self.get_comment_reactions(owner, repo, comment_id)
        else:
            reactions = await self.get_issue_reactions(owner, repo, issue_number)

        # Handle None or empty responses
        if not reactions:
            return False

        for r in reactions:
            if r.get("content") == reaction and r.get("user", {}).get("login") == bot_username:
                return True
        return False