"""Symmetric encryption utilities using Fernet (AES-128 in CBC mode with HMAC-SHA256)."""
from cryptography.fernet import Fernet, InvalidToken

from src.config import settings


def _get_fernet(key: str | None = None) -> Fernet:
    raw_key = key or settings.encryption_key
    try:
        return Fernet(raw_key.encode() if isinstance(raw_key, str) else raw_key)
    except Exception as exc:
        raise ValueError("Invalid Fernet encryption key format") from exc


def encrypt(plaintext: str, key: str | None = None) -> str:
    """Encrypt a plaintext string into a base64 ciphertext string."""
    fernet = _get_fernet(key)
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str, key: str | None = None) -> str:
    """Decrypt a base64 ciphertext string back to plaintext."""
    fernet = _get_fernet(key)
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt: invalid key or corrupted ciphertext") from exc
