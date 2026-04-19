from .base import BaseGitClient
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import httpx
import json
import logging
import base64
import hmac
import hashlib

logger = logging.getLogger(__name__)


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

    async def _ensure_valid_token(self):
        """Check if token is about to expire and refresh if needed."""
        if not self.account_id or not self.db_session:
            return

        from ..models import GiteaAccount, GiteaInstance
        
        account = self.db_session.query(GiteaAccount).filter(GiteaAccount.id == self.account_id).first()
        if not account or not account.token_expires_at:
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

            if response.status_code not in (200, 201, 204):
                logger.error(f"Gitea API error: status {response.status_code} on {path}")
                raise Exception(f"Gitea API error: {response.status_code}")

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
                # Create new label
                try:
                    new_label = await self._request("POST", f"/repos/{owner}/{repo}/labels", data={"name": name})
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
        """Create a review on a pull request."""
        data = {
            "event": event,
            "body": body,
            "comments": comments
        }
        if commit_id:
            data["commit_id"] = commit_id

        url = f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        return await self._request("POST", url, data=data)

    async def create_pull_request_comment(
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

    # ============ User Webhook Operations ============

    async def list_user_hooks(self) -> List[Dict]:
        """List webhooks for the authenticated user."""
        return await self._request("GET", "/user/hooks")

    async def create_user_hook(
        self,
        webhook_url: str,
        secret: str,
        custom_header_value: str,
        events: List[str] = ["issue_comment", "issues", "pull_request"]
    ) -> Dict:
        """Create a webhook for the authenticated user.

        This webhook will receive events from all repositories the user has access to.
        """
        data = {
            "type": "gitea",
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "secret": secret,
            },
            "events": events,
            "active": True,
            "authorization_header": f"Basic {custom_header_value}"
        }

        return await self._request("POST", "/user/hooks", data=data)

    async def delete_user_hook(self, hook_id: int) -> bool:
        """Delete a user webhook."""
        url = f"{self.base_url}/api/v1/user/hooks/{hook_id}"

        async with httpx.AsyncClient() as client:
            response = await client.request(
                "DELETE",
                url,
                headers=self._headers()
            )

            return response.status_code == 204


def generate_webhook_secret() -> str:
    """Generate a random webhook secret."""
    import secrets
    return secrets.token_urlsafe(32)


def encode_user_context(instance_id: int, account_id: int) -> str:
    """Encode user context as Base64 for webhook Authorization header."""
    context = f"{instance_id}:{account_id}"
    return base64.b64encode(context.encode()).decode()


def decode_user_context(encoded: str) -> tuple[int, int]:
    """Decode user context from Base64 Authorization header.
    
    Returns (instance_id, account_id) or (0, 0) if invalid.
    """
    try:
        decoded = base64.b64decode(encoded).decode()
        if ":" not in decoded:
            return 0, 0
        instance_id, account_id = decoded.split(":", 1)
        return int(instance_id), int(account_id)
    except Exception as e:
        logger.warning(f"Failed to decode user context: {e}")
        return 0, 0


def verify_hmac_signature(
    payload: bytes,
    signature: str,
    secret: str
) -> bool:
    """Verify HMAC-SHA256 signature of webhook payload."""
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)