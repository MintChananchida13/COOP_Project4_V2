from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException

from .db import connect as connect_db


DEFAULT_IMAGE_VERIFICATION_CATEGORIES: List[Dict[str, Any]] = [
    {
        "value": "company_logo",
        "label": "\u0e42\u0e25\u0e42\u0e01\u0e49\u0e1a\u0e23\u0e34\u0e29\u0e31\u0e17",
        "prompt": "This is a photo of a company logo.",
        "match_threshold": 0.50,
        "margin_threshold": 0.05,
        "evidence_temperature": 1.0,
        "enabled": True,
    },
    {
        "value": "official_stamp",
        "label": "\u0e15\u0e23\u0e32\u0e1b\u0e23\u0e30\u0e17\u0e31\u0e1a",
        "prompt": "This is a photo of an official ink stamp on a document.",
        "match_threshold": 0.50,
        "margin_threshold": 0.05,
        "evidence_temperature": 1.0,
        "enabled": True,
    },
    {
        "value": "signature",
        "label": "\u0e25\u0e32\u0e22\u0e40\u0e0b\u0e47\u0e19",
        "prompt": "This is a photo of a handwritten signature.",
        "match_threshold": 0.45,
        "margin_threshold": 0.04,
        "evidence_temperature": 1.0,
        "enabled": True,
    },
    {
        "value": "qr_code",
        "label": "QR Code",
        "prompt": "This is a photo of a QR code.",
        "match_threshold": 0.55,
        "margin_threshold": 0.05,
        "evidence_temperature": 1.0,
        "enabled": True,
    },
    {
        "value": "barcode",
        "label": "\u0e1a\u0e32\u0e23\u0e4c\u0e42\u0e04\u0e49\u0e14",
        "prompt": "This is a photo of a linear barcode.",
        "match_threshold": 0.55,
        "margin_threshold": 0.05,
        "evidence_temperature": 1.0,
        "enabled": True,
    },
    {
        "value": "portrait",
        "label": "\u0e23\u0e39\u0e1b\u0e16\u0e48\u0e32\u0e22\u0e1a\u0e38\u0e04\u0e04\u0e25",
        "prompt": "This is a portrait photo of a real person.",
        "match_threshold": 0.45,
        "margin_threshold": 0.04,
        "evidence_temperature": 1.0,
        "enabled": True,
    },
    {
        "value": "government_emblem",
        "label": "\u0e15\u0e23\u0e32\u0e04\u0e23\u0e38\u0e11",
        "prompt": "This is a photo of the Thai Garuda government emblem.",
        "match_threshold": 0.40,
        "margin_threshold": 0.03,
        "evidence_temperature": 1.0,
        "enabled": True,
    },
    {
        "value": "thailand_symbol",
        "label": "\u0e2a\u0e31\u0e0d\u0e25\u0e31\u0e01\u0e29\u0e13\u0e4c\u0e1b\u0e23\u0e30\u0e40\u0e17\u0e28\u0e44\u0e17\u0e22",
        "prompt": "This is a photo of a recognizable symbol associated with Thailand, such as the map of Thailand, the Thai national flag, or a Thai elephant symbol.",
        "match_threshold": 0.03,
        "margin_threshold": 0.02,
        "evidence_temperature": 1.0,
        "enabled": True,
    },
]


@dataclass(frozen=True)
class ImageVerificationCategory:
    value: str
    label: str
    prompt: str
    match_threshold: float
    margin_threshold: float
    evidence_temperature: float
    enabled: bool = True

    def to_api(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "label": self.label,
            "prompt": self.prompt,
            "match_threshold": self.match_threshold,
            "margin_threshold": self.margin_threshold,
            "evidence_temperature": self.evidence_temperature,
            "enabled": self.enabled,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_category(row: Any) -> ImageVerificationCategory:
    item = dict(row)
    return ImageVerificationCategory(
        value=str(item["value"]),
        label=str(item["label"]),
        prompt=str(item["prompt"]),
        match_threshold=float(item.get("match_threshold") or 0.0),
        margin_threshold=float(item.get("margin_threshold") or 0.0),
        evidence_temperature=max(0.01, float(item.get("evidence_temperature") or 1.0)),
        enabled=bool(item.get("enabled")),
    )


def _category_to_payload(category: Dict[str, Any] | ImageVerificationCategory) -> Dict[str, Any]:
    if isinstance(category, ImageVerificationCategory):
        return category.to_api()
    return {
        "value": str(category.get("value") or "").strip(),
        "label": str(category.get("label") or "").strip(),
        "prompt": str(category.get("prompt") or "").strip(),
        "match_threshold": float(category.get("match_threshold", 0.7)),
        "margin_threshold": float(category.get("margin_threshold", 0.05)),
        "evidence_temperature": max(0.01, float(category.get("evidence_temperature", 1.0))),
        "enabled": bool(category.get("enabled", True)),
    }


def ensure_image_verification_categories_table(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS image_verification_categories (
            id TEXT NOT NULL PRIMARY KEY,
            value TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            prompt TEXT NOT NULL,
            match_threshold REAL NOT NULL DEFAULT 0.70,
            margin_threshold REAL NOT NULL DEFAULT 0.05,
            evidence_temperature REAL NOT NULL DEFAULT 1.0,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for item in DEFAULT_IMAGE_VERIFICATION_CATEGORIES:
        payload = _category_to_payload(item)
        conn.execute(
            """
            INSERT INTO image_verification_categories (
                id, value, label, prompt, match_threshold, margin_threshold,
                evidence_temperature, enabled, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(value) DO NOTHING
            """,
            (
                f"ivc_{payload['value']}",
                payload["value"],
                payload["label"],
                payload["prompt"],
                payload["match_threshold"],
                payload["margin_threshold"],
                payload["evidence_temperature"],
                bool(payload["enabled"]),
            ),
        )
    conn.commit()


def list_image_verification_categories(enabled_only: bool = False) -> List[ImageVerificationCategory]:
    conn = connect_db()
    try:
        ensure_image_verification_categories_table(conn)
        if enabled_only:
            rows = conn.execute(
                """
                SELECT * FROM image_verification_categories
            WHERE enabled = TRUE
                ORDER BY value ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM image_verification_categories
                ORDER BY enabled DESC, value ASC
                """
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_category(row) for row in rows]


def get_image_verification_category(value: Optional[str]) -> Optional[ImageVerificationCategory]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    conn = connect_db()
    try:
        ensure_image_verification_categories_table(conn)
        row = conn.execute(
            "SELECT * FROM image_verification_categories WHERE value = ?",
            (raw_value,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_category(row) if row else None


def require_image_verification_category(value: Optional[str]) -> ImageVerificationCategory:
    category = get_image_verification_category(value)
    if category is None:
        raise HTTPException(status_code=404, detail=f"Image verification category not found: {value or '(empty)'}")
    return category


class ImageVerificationCategoryService:
    def list(self, enabled_only: bool = False) -> Dict[str, Any]:
        categories = [category.to_api() for category in list_image_verification_categories(enabled_only=enabled_only)]
        return {"categories": categories}

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        category = _category_to_payload(payload)
        if not category["value"]:
            raise HTTPException(status_code=422, detail="Category value is required.")
        if category["value"] == "other":
            raise HTTPException(status_code=422, detail="'other' is reserved as a no-match status, not a SigLIP category.")
        if not category["label"] or not category["prompt"]:
            raise HTTPException(status_code=422, detail="Category label and prompt are required.")
        conn = connect_db()
        try:
            ensure_image_verification_categories_table(conn)
            conn.execute(
                """
                INSERT INTO image_verification_categories (
                    id, value, label, prompt, match_threshold, margin_threshold,
                    evidence_temperature, enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    f"ivc_{category['value']}",
                    category["value"],
                    category["label"],
                    category["prompt"],
                    category["match_threshold"],
                    category["margin_threshold"],
                    category["evidence_temperature"],
                    bool(category["enabled"]),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {"category": require_image_verification_category(category["value"]).to_api()}

    def update(self, value: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        existing = require_image_verification_category(value)
        patch = {key: item for key, item in payload.items() if item is not None}
        if not patch:
            return {"category": existing.to_api()}
        if "value" in patch and str(patch["value"]).strip() != value:
            raise HTTPException(status_code=422, detail="Category value cannot be changed after creation.")
        if value == "other":
            raise HTTPException(status_code=422, detail="'other' is reserved and cannot be managed as a category.")
        allowed = {
            "label",
            "prompt",
            "match_threshold",
            "margin_threshold",
            "evidence_temperature",
            "enabled",
        }
        column_values = {key: patch[key] for key in allowed if key in patch}
        if not column_values:
            return {"category": existing.to_api()}
        if "enabled" in column_values:
            column_values["enabled"] = bool(column_values["enabled"])
        for key in {"match_threshold", "margin_threshold", "evidence_temperature"} & set(column_values):
            column_values[key] = float(column_values[key])
        set_clause = ", ".join(f"{column} = ?" for column in column_values)
        conn = connect_db()
        try:
            ensure_image_verification_categories_table(conn)
            conn.execute(
                f"""
                UPDATE image_verification_categories
                SET {set_clause}, updated_at = CURRENT_TIMESTAMP
                WHERE value = ?
                """,
                [*column_values.values(), value],
            )
            conn.commit()
        finally:
            conn.close()
        return {"category": require_image_verification_category(value).to_api()}

    def delete(self, value: str) -> Dict[str, Any]:
        require_image_verification_category(value)
        if value == "other":
            raise HTTPException(status_code=422, detail="'other' is reserved and cannot be deleted.")
        conn = connect_db()
        try:
            ensure_image_verification_categories_table(conn)
            cursor = conn.execute(
                "DELETE FROM image_verification_categories WHERE value = ?",
                (value,),
            )
            conn.commit()
            return {"value": value, "deleted": cursor.rowcount > 0}
        finally:
            conn.close()


def categories_to_runtime_payload(categories: Iterable[ImageVerificationCategory]) -> List[Dict[str, Any]]:
    return [category.to_api() for category in categories if category.enabled]
