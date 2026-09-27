"""AES-GCM compatibility with the former WebCrypto ciphertext; no plaintext in evidence."""

import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .policy import AiError


def _key():
    secret = os.getenv("AI_SECRET_ENCRYPTION_KEY", "").strip()
    if len(secret.encode()) < 32:
        raise AiError("AI 凭证加密服务未安全配置", "service_unavailable", 503)
    return AESGCM(hashlib.sha256(secret.encode()).digest())


def encrypt(value):
    if not value:
        return ""
    iv = os.urandom(12)
    payload = _key().encrypt(iv, value.encode(), None)
    return ".".join(
        base64.urlsafe_b64encode(v).decode().rstrip("=") for v in (iv, payload)
    )


def decrypt(value):
    if not value:
        return ""
    try:
        parts = value.split(".")
        if len(parts) != 2:
            raise ValueError()
        iv, payload = [base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)) for v in parts]
        if len(iv) != 12:
            raise ValueError()
        return _key().decrypt(iv, payload, None).decode()
    except Exception as error:
        raise AiError("AI 凭证解密失败", "service_unavailable", 503) from error
