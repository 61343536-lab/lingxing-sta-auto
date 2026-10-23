from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .errors import ServiceError


def _javascript_string(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def canonical_parameters(params: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in sorted(params):
        value = params[key]
        if value == "":
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            rendered = _javascript_string(value)
        parts.append(f"{key}={rendered}")
    return "&".join(parts)


def sign_parameters(params: Mapping[str, Any], app_id: str) -> str:
    key = app_id.encode()
    if len(key) not in {16, 24, 32}:
        raise ServiceError("CONFIG_ERROR", "领星 AppID 长度不符合 AES 签名密钥要求。")

    digest = hashlib.md5(canonical_parameters(params).encode(), usedforsecurity=False)
    plaintext = digest.hexdigest().upper().encode()
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode()
