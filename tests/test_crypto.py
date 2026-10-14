"""Tests for Fernet cryptographic encryption and decryption utilities."""
import pytest
from cryptography.fernet import Fernet

from src.crypto import decrypt, encrypt


def test_encrypt_decrypt_roundtrip():
    secret = "my-super-secret-telegram-session-string-12345"
    encrypted = encrypt(secret)
    assert encrypted != secret
    decrypted = decrypt(encrypted)
    assert decrypted == secret


def test_encrypt_decrypt_unicode_and_emojis():
    message = "Привет, мир! 🚀 Secret token: sk-ant-api03-xyz_123 🔑"
    encrypted = encrypt(message)
    decrypted = decrypt(encrypted)
    assert decrypted == message


def test_custom_key_override():
    key1 = Fernet.generate_key().decode()
    key2 = Fernet.generate_key().decode()

    text = "Confidential data"
    enc = encrypt(text, key=key1)
    assert decrypt(enc, key=key1) == text

    # Attempting to decrypt with a different key must fail
    with pytest.raises(ValueError, match="Failed to decrypt"):
        decrypt(enc, key=key2)


def test_tampered_ciphertext_raises_value_error():
    encrypted = encrypt("test-payload")
    tampered = encrypted[:-4] + "AAAA"
    with pytest.raises(ValueError, match="Failed to decrypt"):
        decrypt(tampered)


def test_invalid_key_format():
    with pytest.raises(ValueError, match="Invalid Fernet encryption key"):
        encrypt("hello", key="not-a-valid-base64-key")
