from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BaseGitClient(ABC):
    """Abstract base class for Git platform clients (Gitea, GitHub, GitLab)."""

    @abstractmethod
    async def get_repo(self, owner: str, repo: str) -> Dict[str, Any]:
        """Get repository information."""
        pass

    @abstractmethod
    async def check_user_repo_access(self, owner: str, repo: str, username: str) -> bool:
        """Check if user has write/admin access."""
        pass

    @abstractmethod
    async def get_repo_file_content(self, owner: str, repo: str, path: str) -> Optional[str]:
        """Get file content from repository."""
        pass

    @abstractmethod
    async def get_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        """Get issue or pull request details."""
        pass

    @abstractmethod
    async def add_issue_label(self, owner: str, repo: str, issue_number: int, label_names: List[str]) -> Dict[str, Any]:
        """Add labels to an issue or PR."""
        pass

    @abstractmethod
    async def create_comment(self, owner: str, repo: str, issue_number: int, body: str) -> Dict[str, Any]:
        """Create a comment on an issue or PR."""
        pass

    @abstractmethod
    async def close_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        """Close an issue or PR."""
        pass

    @abstractmethod
    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> Dict[str, Any]:
        """Get pull request details."""
        pass

    @abstractmethod
    async def get_pull_request_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Get diff content for a PR."""
        pass

    @abstractmethod
    async def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: List[Dict[str, Any]],
        event: str = "COMMENT",
        commit_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a review on a pull request."""
        pass
