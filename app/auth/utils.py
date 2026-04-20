import bcrypt

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    # Salt is automatically generated and embedded in the hash
    salt = bcrypt.gensalt()
    # bcrypt expects bytes
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except Exception:
        return False
