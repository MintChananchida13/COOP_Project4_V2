"""Encryption for externally supplied OCR credentials.

The key is supplied by the deployment, independently of the database.
"""
import os

from cryptography.fernet import Fernet, InvalidToken


class ExternalOcrSecretError(RuntimeError):
    """A credential cannot be safely stored or recovered."""


def _cipher() -> Fernet:
    key = os.environ.get("EXTERNAL_OCR_ENCRYPTION_KEY", "").strip()
    if not key:
        raise ExternalOcrSecretError("EXTERNAL_OCR_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ExternalOcrSecretError("EXTERNAL_OCR_ENCRYPTION_KEY is invalid") from exc


def encrypt_api_key(api_key: str) -> str:
    if not api_key:
        raise ExternalOcrSecretError("API key is required")
    return _cipher().encrypt(api_key.encode("utf-8")).decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise ExternalOcrSecretError("Unable to decrypt External OCR API key") from exc
