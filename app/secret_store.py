from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_ENC_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    key_material = (settings.secret_encryption_key or settings.secret_key).encode("utf-8")
    digest = hashlib.sha256(key_material).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plain_text: str) -> str:
    if not plain_text:
        return plain_text
    token = _fernet().encrypt(plain_text.encode("utf-8")).decode("utf-8")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if not raw.startswith(_ENC_PREFIX):
        return raw
    encrypted = raw[len(_ENC_PREFIX):]
    try:
        return _fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
