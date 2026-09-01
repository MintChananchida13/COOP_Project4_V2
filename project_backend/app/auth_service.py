import hmac
import os
import time
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import Depends, Header, HTTPException

from .auth_password import hash_password, verify_password
from .db import connect as connect_db


_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60


def _secret() -> bytes:
    return os.getenv("AUTH_SECRET", "ocr-studio-dev-secret").encode("utf-8")


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_access_token(user_id: str) -> str:
    expires_at = int(time.time()) + _TOKEN_TTL_SECONDS
    payload = f"{user_id}:{expires_at}"
    token = f"{payload}:{_sign(payload)}"
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii")


def decode_access_token(token: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        user_id, expires_at_text, signature = raw.rsplit(":", 2)
        expires_at = int(expires_at_text)
    except Exception as error:
        raise HTTPException(status_code=401, detail="Invalid authentication token.") from error
    payload = f"{user_id}:{expires_at}"
    if not hmac.compare_digest(_sign(payload), signature) or expires_at < int(time.time()):
        raise HTTPException(status_code=401, detail="Invalid authentication token.")
    return user_id


def _connect() -> Any:
    conn = connect_db()
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def user_to_api(row: Any) -> Dict[str, Any]:
    item = dict(row)
    return {
        "id": item["id"],
        "email": item["email"],
        "role": item["role"],
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def create_user(email: str, password: str, role: str = "user") -> Dict[str, Any]:
    normalized_email = email.strip().lower()
    normalized_role = "admin" if role == "admin" else "user"
    user_id = f"usr_{uuid4().hex[:12]}"
    with _connect() as conn:
        existing = conn.execute("SELECT * FROM users WHERE email = ?", (normalized_email,)).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Email is already registered.")
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, role, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (user_id, normalized_email, hash_password(password), normalized_role),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_to_api(row)


def authenticate_user(email: str, password: str) -> Dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return user_to_api(row)


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_to_api(row) if row else None


def current_user(
    authorization: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    user_id = None
    if authorization and authorization.lower().startswith("bearer "):
        user_id = decode_access_token(authorization.split(" ", 1)[1].strip())
    elif x_user_id:
        user_id = x_user_id.strip()
    if not user_id:
        return {"id": None, "email": None, "role": "mock", "auth_mode": "mock"}
    user = get_user_by_id(user_id)
    if user is None:
        return {"id": None, "email": None, "role": "mock", "auth_mode": "mock"}
    return user


def current_admin(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return user
