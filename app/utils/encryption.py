"""Encryption utilities for sensitive data."""

import os
import base64
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


def get_encryption_key() -> bytes:
    """Get or derive encryption key from SECRET_KEY.
    
    Uses PBKDF2 to derive a Fernet key from the SECRET_KEY.
    This ensures we have a consistent encryption key as long as SECRET_KEY doesn't change.
    
    Returns:
        bytes: 32-byte Fernet encryption key
    """
    secret_key = os.getenv("SECRET_KEY", "")
    
    if not secret_key:
        # Fallback for edge case - generate a temporary key (not ideal but won't crash)
        logger.warning("SECRET_KEY not set, using fallback encryption key")
        secret_key = "fallback-key-for-encryption-only"
    
    # Derive a Fernet key using PBKDF2HMAC with a fixed salt
    # In production, you might want to store a separate salt in the database
    salt = b"giteacopilot-encryption-salt-v1"  # Fixed salt for key derivation
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,  # OWASP recommendation for 2022+
    )
    
    key = base64.urlsafe_b64encode(kdf.derive(secret_key.encode()))
    return key


def encrypt_sensitive_value(value: str) -> str:
    """Encrypt a sensitive value using Fernet symmetric encryption.
    
    Args:
        value: The plaintext value to encrypt
        
    Returns:
        str: Base64-encoded encrypted value
    """
    if not value:
        return value
    
    f = Fernet(get_encryption_key())
    encrypted = f.encrypt(value.encode())
    return base64.urlsafe_b64encode(encrypted).decode()


def decrypt_sensitive_value(encrypted_value: str) -> str:
    """Decrypt a Fernet-encrypted value.
    
    Args:
        encrypted_value: Base64-encoded encrypted value
        
    Returns:
        str: Decrypted plaintext value
        
    Raises:
        Exception: If decryption fails (invalid data or wrong key)
    """
    if not encrypted_value:
        return encrypted_value
    
    f = Fernet(get_encryption_key())
    encrypted = base64.urlsafe_b64decode(encrypted_value.encode())
    decrypted = f.decrypt(encrypted)
    return decrypted.decode()


def is_value_encrypted(value: str) -> bool:
    """Check if a value appears to be encrypted.
    
    Encrypted values are base64-encoded and start with specific patterns.
    This is a heuristic check, not a guarantee.
    
    Args:
        value: The value to check
        
    Returns:
        bool: True if value appears to be encrypted
    """
    if not value or len(value) < 20:
        return False
    
    # Encrypted values are base64-encoded and typically longer than plaintext
    # This is a simple heuristic
    try:
        # Try to decode as base64
        decoded = base64.urlsafe_b64decode(value + '==')  # Add padding if needed
        # Fernet tokens have specific structure (version byte + timestamp + IV + ciphertext + HMAC)
        # They should be at least 43 bytes decoded
        return len(decoded) >= 43
    except Exception:
        return False
