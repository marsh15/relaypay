import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key(material: str) -> bytes:
    return hashlib.sha256(material.encode("utf-8")).digest()


def encrypt_webhook_secret(secret: str, encryption_key: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key(encryption_key)).encrypt(nonce, secret.encode("utf-8"), None)


def decrypt_webhook_secret(ciphertext: bytes, encryption_key: str) -> str:
    if len(ciphertext) < 29:
        raise ValueError("invalid encrypted webhook secret")
    return AESGCM(_key(encryption_key)).decrypt(ciphertext[:12], ciphertext[12:], None).decode()
