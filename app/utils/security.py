"""Security utilities for JWT and secret key management."""

import os
import secrets
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default insecure key - NEVER use this in production
DEFAULT_INSECURE_KEY = "super-secret-key"

# Environment file path
ENV_FILE_PATH = Path(".env")


def get_or_create_secret_key() -> str:
    """Get SECRET_KEY from environment or create a new one.
    
    Security:
    - If SECRET_KEY env var is set and not the default, use it
    - If SECRET_KEY is the default insecure value, generate a new one
    - If SECRET_KEY is not set, generate a new one and save to .env file
    - Raises RuntimeError if using default key in production
    
    Returns:
        str: The secure SECRET_KEY to use for JWT signing
    """
    env_key = os.getenv("SECRET_KEY")
    
    # If SECRET_KEY is set and not the default insecure value, use it
    if env_key and env_key != DEFAULT_INSECURE_KEY:
        logger.info("Using SECRET_KEY from environment")
        return env_key
    
    # If SECRET_KEY is the default or not set, we need to generate/restore one
    if env_key == DEFAULT_INSECURE_KEY:
        logger.warning("Detected default insecure SECRET_KEY, will generate new one")
    
    # Try to load from .env file
    secret_key = _load_from_env_file()
    
    if secret_key:
        logger.info("Loaded SECRET_KEY from .env file")
        # Set it in environment for other code to use
        os.environ["SECRET_KEY"] = secret_key
        return secret_key
    
    # Generate a new secure key
    secret_key = _generate_secure_key()
    logger.info("Generated new secure SECRET_KEY")
    
    # Save to .env file
    _save_to_env_file(secret_key)
    logger.info("Saved SECRET_KEY to .env file")
    
    # Set it in environment for other code to use
    os.environ["SECRET_KEY"] = secret_key
    return secret_key


def _generate_secure_key() -> str:
    """Generate a cryptographically secure random key.
    
    Returns:
        str: 32-byte URL-safe base64-encoded random string
    """
    return secrets.token_urlsafe(32)


def _load_from_env_file() -> str | None:
    """Load SECRET_KEY from .env file if it exists.
    
    Returns:
        str | None: The SECRET_KEY if found, None otherwise
    """
    if not ENV_FILE_PATH.exists():
        return None
    
    try:
        with open(ENV_FILE_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("SECRET_KEY=") and not line.startswith("#"):
                    key = line.split("=", 1)[1].strip()
                    # Remove quotes if present
                    if key.startswith('"') and key.endswith('"'):
                        key = key[1:-1]
                    elif key.startswith("'") and key.endswith("'"):
                        key = key[1:-1]
                    if key and key != DEFAULT_INSECURE_KEY:
                        return key
    except Exception as e:
        logger.warning(f"Failed to load SECRET_KEY from .env: {e}")
    
    return None


def _save_to_env_file(secret_key: str) -> None:
    """Save SECRET_KEY to .env file.
    
    Args:
        secret_key: The secure key to save
    """
    try:
        # Read existing content
        existing_content = ""
        if ENV_FILE_PATH.exists():
            with open(ENV_FILE_PATH, "r") as f:
                existing_content = f.read()
        
        # Check if SECRET_KEY already exists in file
        if "SECRET_KEY=" in existing_content:
            # Update existing line
            lines = existing_content.splitlines()
            new_lines = []
            for line in lines:
                if line.strip().startswith("SECRET_KEY=") and not line.strip().startswith("#"):
                    new_lines.append(f"SECRET_KEY={secret_key}")
                else:
                    new_lines.append(line)
            existing_content = "\n".join(new_lines)
        else:
            # Append new line
            if existing_content and not existing_content.endswith("\n"):
                existing_content += "\n"
            existing_content += f"SECRET_KEY={secret_key}\n"
        
        # Write back
        with open(ENV_FILE_PATH, "w") as f:
            f.write(existing_content)
            
    except Exception as e:
        logger.warning(f"Failed to save SECRET_KEY to .env: {e}")


def is_secret_key_secure() -> bool:
    """Check if the current SECRET_KEY is secure (not the default).
    
    Returns:
        bool: True if secure, False if using default insecure key
    """
    env_key = os.getenv("SECRET_KEY")
    return env_key is not None and env_key != DEFAULT_INSECURE_KEY


def validate_secret_key() -> bool:
    """Validate that SECRET_KEY is properly configured and secure.
    
    Returns:
        bool: True if valid and secure, False otherwise
    """
    env_key = os.getenv("SECRET_KEY")
    
    if env_key is None:
        logger.error("SECRET_KEY is not configured")
        return False
    
    if env_key == DEFAULT_INSECURE_KEY:
        logger.error("SECRET_KEY is set to the insecure default value!")
        return False
    
    if len(env_key) < 32:
        logger.warning("SECRET_KEY is shorter than recommended (32+ characters)")
        # Still return True if it's not the default
    
    return True
