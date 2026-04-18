import logging
from openai import AsyncOpenAI
from typing import Optional
import os

logger = logging.getLogger(__name__)


class LLMClient:
    """Client for interacting with OpenAI-compatible LLM APIs using official SDK."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")

        # Handle empty/whitespace-only api_key - use placeholder for APIs that don't require auth
        raw_key = api_key or os.getenv("LLM_API_KEY", "")
        self.api_key = raw_key.strip() if raw_key and raw_key.strip() else "sk-no-key-required"

        # Initialize OpenAI client
        self.client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )

        logger.info(f"LLMClient initialized: base_url={self.base_url}, model={self.model}, api_key_set={bool(raw_key.strip())}")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7
    ) -> str:
        """Generate a response from the LLM."""
        return await self._call_api(prompt, system_prompt, max_tokens, temperature)

    async def _call_api(
        self,
        prompt: str,
        system_prompt: Optional[str],
        max_tokens: int,
        temperature: float
    ) -> str:
        """Call OpenAI-compatible API using official SDK."""
        logger.info(f"Calling LLM API with model: {self.model}")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature
            )

            content = response.choices[0].message.content
            logger.info(f"LLM response received: {len(content)} chars")
            return content

        except Exception as e:
            logger.error(f"LLM API call failed: {e}", exc_info=True)
            return f"LLM API 调用出错: {str(e)}"


def get_llm_client_from_config(db_session=None) -> LLMClient:
    """Get LLM client configured from SystemConfig or environment.

    Priority: SystemConfig > Environment variables
    """
    base_url = None
    api_key = None
    model = None

    # Try to get from database if session available
    if db_session:
        try:
            from ..models import SystemConfig
            config = db_session.query(SystemConfig).first()
            if config:
                base_url = config.llm_base_url
                # Only use db api_key if it's non-empty
                if config.llm_api_key and config.llm_api_key.strip():
                    api_key = config.llm_api_key.strip()
                model = config.llm_model
                logger.info(f"LLM config from DB: base_url={base_url}, model={model}, has_key={bool(api_key)}")
        except Exception as e:
            logger.warning(f"Failed to get config from DB: {e}")

    # Fall back to environment variables if not set
    env_key = os.getenv("LLM_API_KEY", "")
    if not api_key and env_key.strip():
        api_key = env_key.strip()
    if not base_url:
        base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    if not model:
        model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    return LLMClient(base_url=base_url, api_key=api_key, model=model)


# Global LLM client instance (lazy initialized)
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get or create the global LLM client instance (uses env vars)."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client():
    """Reset the global LLM client (called when config changes)."""
    global _llm_client
    _llm_client = None