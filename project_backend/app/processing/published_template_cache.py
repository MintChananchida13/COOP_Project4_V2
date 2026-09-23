from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.db import connect as connect_db
from app.core.json_utils import jsonb_load

logger = logging.getLogger(__name__)


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {key: row[key] for key in row.keys()}


def _normalize_data_type(value: Optional[str]) -> str:
    normalized = str(value or "text").strip().lower()
    return normalized if normalized in {"text", "table", "image"} else "text"


def _normalize_detection_mode(value: Optional[str]) -> str:
    return "main_page" if value == "main_page" else "all_pages"


def _normalize_main_page_number(value: Any) -> int:
    try:
        page_number = int(value or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, page_number)


def _normalize_roi_points(value: Any) -> List[Dict[str, float]]:
    raw = jsonb_load(value, []) if isinstance(value, str) else value
    if not isinstance(raw, list):
        return []
    points: List[Dict[str, float]] = []
    for point in raw:
        if not isinstance(point, dict):
            continue
        try:
            x_ratio = float(point.get("x_ratio", point.get("x", 0.0)))
            y_ratio = float(point.get("y_ratio", point.get("y", 0.0)))
        except (TypeError, ValueError):
            continue
        points.append({"x_ratio": x_ratio, "y_ratio": y_ratio})
    return points


def _roi_api_from_row(item: Dict[str, Any]) -> Dict[str, Any]:
    roi = {
        "page_number": item["page_number"],
        "x_ratio": item["roi_x_ratio"],
        "y_ratio": item["roi_y_ratio"],
        "width_ratio": item["roi_width_ratio"],
        "height_ratio": item["roi_height_ratio"],
    }
    points = _normalize_roi_points(item.get("roi_points_json"))
    if points:
        roi["points"] = points
    return roi


def _template_field_row_to_api(row: Any) -> Dict[str, Any]:
    item = _row_to_dict(row)
    return {
        "id": item["id"],
        "template_id": item.get("template_id"),
        "template_page_id": item["template_page_id"],
        "page_number": item.get("page_number", 1),
        "field_name": item.get("field_name") or item.get("anchor_name"),
        "display_label": item.get("display_label") or item.get("anchor_name") or item.get("field_name"),
        "roi": _roi_api_from_row(item),
        "data_type": item["data_type"],
        "user_selectable": bool(item.get("user_selectable", not item.get("use_for_verification", False))),
        "default_selected": bool(item.get("default_selected", False)),
        "use_for_verification": bool(item.get("use_for_verification", False)),
        "expected_text": item.get("expected_text"),
        "match_type": item.get("match_type"),
        "required_for_verification": bool(item.get("required_for_verification", False)),
        "extraction_method": item.get("extraction_method", "fixed_roi"),
        "roi_mode": item.get("roi_mode") or "fix",
        "expected_content": item.get("expected_content"),
        "anchor_text": item.get("anchor_text"),
        "regex_pattern": item.get("regex_pattern"),
        "roi_padding": item.get("roi_padding"),
        "verification_weight": item.get("verification_weight", 1.0),
        "image_category": item.get("image_category"),
        "sort_order": item.get("sort_order", 0),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


@dataclass(frozen=True)
class PublishedTemplateEntry:
    template_id: str
    status: str
    metadata: Dict[str, Any]
    field_count: int
    verification_fields: List[Dict[str, Any]]
    layout_rows: List[Dict[str, Any]]


_LOCK = threading.RLock()
_ENTRIES: Dict[str, PublishedTemplateEntry] = {}
_READY = False
_DEGRADED = False
_LAST_REFRESHED_AT: Optional[float] = None


def is_ready() -> bool:
    with _LOCK:
        return _READY


def stats() -> Dict[str, Any]:
    with _LOCK:
        return {
            "ready": _READY,
            "degraded": _DEGRADED,
            "template_count": len(_ENTRIES),
            "layout_row_count": sum(len(entry.layout_rows) for entry in _ENTRIES.values()),
            "last_refreshed_at": _LAST_REFRESHED_AT,
        }


def _load_entries(template_id: Optional[str] = None) -> Dict[str, PublishedTemplateEntry]:
    params: List[Any] = []
    template_filter = ""
    if template_id:
        template_filter = "AND tv.id = ?"
        params.append(template_id)

    with connect_db() as conn:
        template_rows = conn.execute(
            f"""
            SELECT
                tv.id,
                tg.name,
                tg.name AS template_group_name,
                tv.version_name,
                tg.document_type,
                tg.category,
                tv.status,
                tv.version_number AS version,
                tv.template_group_id,
                tv.version_number,
                tv.created_from_version_id AS base_template_id,
                tg.description,
                'new_version' AS creation_type,
                tv.detection_mode,
                tv.main_page_number,
                (
                    SELECT COUNT(*)
                    FROM template_pages tp
                    WHERE tp.template_version_id = tv.id
                ) AS page_count,
                tv.similarity_threshold,
                tv.final_confidence_threshold,
                tv.layout_weight,
                tv.text_anchor_weight,
                tv.image_anchor_weight,
                NULL AS rejection_reason,
                tv.created_at,
                tv.updated_at
            FROM template_versions tv
            JOIN template_groups tg ON tg.id = tv.template_group_id
            WHERE tv.status = 'active'
            {template_filter}
            ORDER BY tv.updated_at DESC
            """,
            tuple(params),
        ).fetchall()

        if not template_rows:
            return {}

        template_ids = [str(row["id"]) for row in template_rows]
        placeholders = ", ".join("?" for _ in template_ids)
        layout_rows = conn.execute(
            f"""
            SELECT
                tv.id AS template_id,
                tg.name AS template_name,
                tv.status AS template_status,
                (
                    SELECT COUNT(*)
                    FROM template_pages count_tp
                    WHERE count_tp.template_version_id = tv.id
                ) AS page_count,
                tv.final_confidence_threshold AS final_confidence_threshold,
                tv.layout_weight AS layout_weight,
                tv.text_anchor_weight AS text_anchor_weight,
                tv.image_anchor_weight AS image_anchor_weight,
                tv.detection_mode AS detection_mode,
                tv.main_page_number AS main_page_number,
                tp.id AS template_page_id,
                NULL AS layout_reference_id,
                tp.page_number AS page_number,
                COALESCE(tp.normalized_image_url, tp.sample_image_url) AS layout_reference_image_url,
                'template_page' AS layout_reference_source,
                CASE
                    WHEN COALESCE(tv.detection_mode, 'all_pages') = 'main_page'
                        AND tp.page_number = COALESCE(tv.main_page_number, 1)
                    THEN 1
                    WHEN COALESCE(tv.detection_mode, 'all_pages') != 'main_page'
                        AND tp.page_number = 1
                    THEN 1
                    ELSE 0
                END AS layout_reference_is_canonical,
                tp.layout_signature_json AS layout_signature_json
            FROM template_pages tp
            JOIN template_versions tv ON tv.id = tp.template_version_id
            JOIN template_groups tg ON tg.id = tv.template_group_id
            WHERE tv.id IN ({placeholders})
              AND tp.layout_signature_json IS NOT NULL
            ORDER BY tv.updated_at DESC, tp.page_number ASC
            """,
            tuple(template_ids),
        ).fetchall()
        field_count_rows = conn.execute(
            f"""
            SELECT tp.template_version_id AS template_id, COUNT(ef.id) AS count
            FROM template_pages tp
            LEFT JOIN extraction_fields ef ON ef.template_page_id = tp.id
            WHERE tp.template_version_id IN ({placeholders})
            GROUP BY tp.template_version_id
            """,
            tuple(template_ids),
        ).fetchall()
        verification_rows = conn.execute(
            f"""
            SELECT
                va.id,
                tp.template_version_id AS template_id,
                va.template_page_id,
                tp.page_number,
                va.anchor_name AS field_name,
                va.anchor_name AS display_label,
                va.roi_x_ratio,
                va.roi_y_ratio,
                va.roi_width_ratio,
                va.roi_height_ratio,
                va.roi_points_json,
                va.anchor_type AS data_type,
                0 AS user_selectable,
                0 AS default_selected,
                1 AS use_for_verification,
                va.expected_text,
                va.match_type,
                va.required AS required_for_verification,
                'fixed_roi' AS extraction_method,
                'fix' AS roi_mode,
                NULL AS expected_content,
                NULL AS anchor_text,
                va.regex_pattern,
                NULL AS roi_padding,
                va.weight AS verification_weight,
                COALESCE(ivc.value, va.image_category_id) AS image_category,
                va.sort_order,
                va.created_at,
                va.updated_at
            FROM verification_anchors va
            JOIN template_pages tp ON tp.id = va.template_page_id
            LEFT JOIN image_verification_categories ivc ON ivc.id = va.image_category_id
            WHERE tp.template_version_id IN ({placeholders})
            ORDER BY tp.page_number ASC, va.sort_order ASC, va.created_at ASC
            """,
            tuple(template_ids),
        ).fetchall()

    field_counts = {str(row["template_id"]): int(row["count"] or 0) for row in field_count_rows}
    layouts_by_template: Dict[str, List[Dict[str, Any]]] = {}
    for row in layout_rows:
        item = _row_to_dict(row)
        layouts_by_template.setdefault(str(item["template_id"]), []).append(item)

    verification_by_template: Dict[str, List[Dict[str, Any]]] = {}
    for row in verification_rows:
        item = _template_field_row_to_api(row)
        verification_by_template.setdefault(str(item["template_id"]), []).append(item)

    entries: Dict[str, PublishedTemplateEntry] = {}
    for row in template_rows:
        item = _row_to_dict(row)
        tid = str(item["id"])
        entries[tid] = PublishedTemplateEntry(
            template_id=tid,
            status=str(item.get("status") or ""),
            metadata=item,
            field_count=field_counts.get(tid, 0),
            verification_fields=verification_by_template.get(tid, []),
            layout_rows=layouts_by_template.get(tid, []),
        )
    return entries


def load_all() -> Dict[str, Any]:
    global _ENTRIES, _READY, _DEGRADED, _LAST_REFRESHED_AT
    started = time.perf_counter()
    entries = _load_entries()
    with _LOCK:
        _ENTRIES = entries
        _READY = True
        _DEGRADED = False
        _LAST_REFRESHED_AT = time.time()
    logger.info(
        "Published template cache loaded: templates=%s layout_rows=%s elapsed=%.3fs",
        len(entries),
        sum(len(entry.layout_rows) for entry in entries.values()),
        time.perf_counter() - started,
    )
    return stats()


def refresh(template_id: str) -> bool:
    global _READY, _DEGRADED, _LAST_REFRESHED_AT
    if not template_id:
        return False
    entries = _load_entries(template_id)
    with _LOCK:
        if entries:
            _ENTRIES[template_id] = next(iter(entries.values()))
        else:
            _ENTRIES.pop(template_id, None)
        _READY = True
        _DEGRADED = False
        _LAST_REFRESHED_AT = time.time()
    return bool(entries)


def mark_degraded() -> None:
    global _DEGRADED
    with _LOCK:
        _DEGRADED = True


def remove(template_id: str) -> None:
    global _LAST_REFRESHED_AT
    if not template_id:
        return
    with _LOCK:
        _ENTRIES.pop(template_id, None)
        _LAST_REFRESHED_AT = time.time()


def get_template(template_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not template_id:
        return None
    with _LOCK:
        entry = _ENTRIES.get(template_id)
        return dict(entry.metadata) if entry else None


def get_field_count(template_id: str) -> Optional[int]:
    with _LOCK:
        entry = _ENTRIES.get(template_id)
        return int(entry.field_count) if entry else None


def get_verification_fields(template_id: str) -> Optional[List[Dict[str, Any]]]:
    with _LOCK:
        entry = _ENTRIES.get(template_id)
        return copy.deepcopy(entry.verification_fields) if entry else None


def get_layout_candidate_rows(
    page_number: int,
    *,
    include_template_id: Optional[str] = None,
    active_only: bool = True,
) -> Optional[List[Dict[str, Any]]]:
    if include_template_id or not active_only:
        return None
    with _LOCK:
        if not _READY or _DEGRADED:
            return None
        rows: List[Dict[str, Any]] = []
        for entry in _ENTRIES.values():
            for row in entry.layout_rows:
                detection_mode = _normalize_detection_mode(row.get("detection_mode"))
                row_page_number = int(row.get("page_number") or 1)
                main_page_number = _normalize_main_page_number(row.get("main_page_number"))
                if detection_mode == "main_page":
                    if row_page_number != main_page_number:
                        continue
                elif row_page_number != int(page_number or 1):
                    continue
                rows.append(dict(row))
        return rows
