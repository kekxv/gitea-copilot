import pytest
from app.auth.utils import hash_password, verify_password

def test_password_hashing():
    password = "secret_password_123"
    hashed = hash_password(password)
    
    # Hash should be different from original
    assert hashed != password
    # Hash should look like a bcrypt hash ($2b$...)
    assert hashed.startswith("$2b$")
    
    # Verification should work
    assert verify_password(password, hashed) is True
    
    # Wrong password should fail
    assert verify_password("wrong_password", hashed) is False
    
    # Different hash for same password (due to salt)
    hashed2 = hash_password(password)
    assert hashed != hashed2
    assert verify_password(password, hashed2) is True

def test_verify_invalid_hash():
    # Test with corrupted hash or non-bcrypt string
    assert verify_password("any", "invalid_hash") is False
    assert verify_password("any", "") is False
