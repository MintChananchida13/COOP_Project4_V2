import base64
import hashlib
import hmac
from uuid import uuid4


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('ascii')}"


def hash_password(password: str) -> str:
    return _hash_password(password, uuid4().hex)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, salt, _ = password_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    return hmac.compare_digest(_hash_password(password, salt), password_hash)
