import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key(material: str) -> bytes:
    return hashlib.sha256(material.encode()).digest()


def encrypt_credential(plaintext: str, material: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key(material)).encrypt(nonce, plaintext.encode(), None)


def decrypt_credential(ciphertext: bytes, material: str) -> str:
    if len(ciphertext) < 29:
        raise ValueError("invalid encrypted connector credential")
    return AESGCM(_key(material)).decrypt(ciphertext[:12], ciphertext[12:], None).decode()
