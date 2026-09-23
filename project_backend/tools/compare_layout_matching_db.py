from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Sequence
from urllib.parse import urlparse


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.local_env import load_local_env

load_local_env()

from app.core.db import connect as connect_db, ensure_database_ready


LAYOUT_MATCHING_SQL = """
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
            AND tp.page_number = ?
        THEN 1

        ELSE 0
    END AS layout_reference_is_canonical,
    tp.layout_signature_json AS layout_signature_json
FROM template_pages tp
JOIN template_versions tv ON tv.id = tp.template_version_id
JOIN template_groups tg ON tg.id = tv.template_group_id
WHERE tp.layout_signature_json IS NOT NULL
AND (
        (
            COALESCE(tv.detection_mode, 'all_pages') = 'main_page'
            AND tp.page_number = COALESCE(tv.main_page_number, 1)
        )
        OR
        (
            COALESCE(tv.detection_mode, 'all_pages') != 'main_page'
            AND tp.page_number = ?
        )
)
ORDER BY tv.updated_at DESC, tp.page_number ASC
"""


def _ms(value: float) -> float:
    return round(value * 1000.0, 2)


def _database_url() -> str:
    import os

    return os.getenv("DATABASE_URL", "").strip().strip('"')


def _target_summary() -> Dict[str, Any]:
    parsed = urlparse(_database_url())
    host = parsed.hostname or ""
    return {
        "host": host,
        "database": parsed.path.lstrip("/") or None,
        "host_type": "neon_pooler" if "-pooler" in host else "neon_direct" if "neon.tech" in host else "other",
    }


def _pooled(page_number: int) -> Dict[str, Any]:
    total_started = time.perf_counter()
    connect_started = time.perf_counter()
    conn = connect_db()
    connect_elapsed = time.perf_counter() - connect_started
    connect_timing = getattr(conn, "connect_timing", {}) if isinstance(getattr(conn, "connect_timing", {}), dict) else {}
    with conn:
        if hasattr(conn, "execute_timed"):
            cursor, execute_timing = conn.execute_timed(LAYOUT_MATCHING_SQL, (page_number, page_number))
            cursor_create_elapsed = float(execute_timing.get("cursor_create") or 0.0)
            execute_elapsed = float(execute_timing.get("execute") or 0.0)
        else:
            cursor_started = time.perf_counter()
            cursor = conn._raw_conn.cursor()
            cursor_create_elapsed = time.perf_counter() - cursor_started
            execute_started = time.perf_counter()
            cursor.execute(LAYOUT_MATCHING_SQL, (page_number, page_number))
            execute_elapsed = time.perf_counter() - execute_started
        fetch_started = time.perf_counter()
        rows = cursor.fetchall()
        fetch_elapsed = time.perf_counter() - fetch_started
    return {
        "mode": "pooled",
        "connect_ms": _ms(connect_elapsed),
        "pool_getconn_ms": _ms(float(connect_timing.get("pool_getconn") or 0.0)),
        "ensure_schema_ms": _ms(float(connect_timing.get("ensure_schema") or 0.0)),
        "cursor_create_ms": _ms(cursor_create_elapsed),
        "execute_ms": _ms(execute_elapsed),
        "fetch_ms": _ms(fetch_elapsed),
        "total_ms": _ms(time.perf_counter() - total_started),
        "row_count": len(rows),
        "pool_before": connect_timing.get("pool_before"),
        "pool_after": connect_timing.get("pool_after"),
    }


def _fresh(page_number: int) -> Dict[str, Any]:
    import psycopg2
    import psycopg2.extras

    total_started = time.perf_counter()
    connect_started = time.perf_counter()
    conn = psycopg2.connect(_database_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    connect_elapsed = time.perf_counter() - connect_started
    try:
        cursor_started = time.perf_counter()
        cursor = conn.cursor()
        cursor_create_elapsed = time.perf_counter() - cursor_started
        execute_started = time.perf_counter()
        cursor.execute(LAYOUT_MATCHING_SQL.replace("?", "%s"), (page_number, page_number))
        execute_elapsed = time.perf_counter() - execute_started
        fetch_started = time.perf_counter()
        rows = cursor.fetchall()
        fetch_elapsed = time.perf_counter() - fetch_started
        conn.commit()
    finally:
        conn.close()
    return {
        "mode": "fresh",
        "fresh_connect_ms": _ms(connect_elapsed),
        "fresh_cursor_create_ms": _ms(cursor_create_elapsed),
        "fresh_execute_ms": _ms(execute_elapsed),
        "fresh_fetch_ms": _ms(fetch_elapsed),
        "fresh_total_ms": _ms(time.perf_counter() - total_started),
        "row_count": len(rows),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare pooled vs fresh DB execution for layout template matching SQL.")
    parser.add_argument("--page-number", type=int, default=1)
    parser.add_argument("--mode", choices=["pooled", "fresh", "both"], default="both")
    parser.add_argument("--skip-startup-init", action="store_true")
    args = parser.parse_args(argv)

    result: Dict[str, Any] = {
        "database": _target_summary(),
        "page_number": args.page_number,
    }
    if not args.skip_startup_init:
        ensure_database_ready()
        result["startup_init"] = "ensure_database_ready"
    if args.mode in {"pooled", "both"}:
        result["pooled"] = _pooled(args.page_number)
    if args.mode in {"fresh", "both"}:
        result["fresh"] = _fresh(args.page_number)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
