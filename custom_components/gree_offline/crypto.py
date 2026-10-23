import base64
import json
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def to_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def encrypt(obj: Any, key: bytes) -> str:
    padder = padding.PKCS7(128).padder()
    plain = padder.update(to_json(obj).encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return base64.b64encode(encryptor.update(plain) + encryptor.finalize()).decode()


def decrypt(data: str, key: bytes) -> Any:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    plain = decryptor.update(base64.b64decode(data)) + decryptor.finalize()
    # Padding is not always valid PKCS7, so cut after the closing brace instead.
    return json.loads(plain[: plain.rindex(b"}") + 1])


def unpack(message: dict, key: bytes) -> dict:
    """Open a {"t": "pack"} envelope; any other message is returned as is."""
    return decrypt(message["pack"], key) if message.get("t") == "pack" else message
