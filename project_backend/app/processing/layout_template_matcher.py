import os
import time
import logging
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.db import connect as connect_db
from app.processing import published_template_cache
from app.processing.layout_signature_service import compare_layout_signatures, signature_from_json

logger = logging.getLogger(__name__)

def _connect() -> Any:
    return connect_db()


def search_layout_candidates(
    query_signature: Dict[str, Any],
    page_number: int = 1,
    limit: int = 5,
    include_template_id: Optional[str] = None,
    active_only: bool = True,
    timing: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    total_started = time.perf_counter()
    breakdown: Dict[str, Any] = {
        "page_number": page_number,
        "limit": limit,
        "include_template_id": include_template_id,
        "active_only": active_only,
        "connect": 0.0,
        "pool_getconn": None,
        "ensure_schema": None,
        "postgres_ready_before": None,
        "postgres_ready_after": None,
        "schema_ensure_calls": None,
        "pool_before": None,
        "pool_after": None,
        "cursor_create": 0.0,
        "backend_pid": None,
        "query_correlation_id": None,
        "connection_before": None,
        "connection_before_execute": None,
        "connection_after_execute": None,
        "execute": 0.0,
        "fetch": 0.0,
        "db_total": 0.0,
        "candidate_rows": 0,
        "active_filter": 0.0,
        "active_filtered_count": 0,
        "signature_parse": 0.0,
        "invalid_signature_count": 0,
        "layout_compare": 0.0,
        "layout_compare_breakdown": {},
        "compared_count": 0,
        "prefilter_rejected_count": 0,
        "spatial_evaluated_count": 0,
        "candidate_build": 0.0,
        "best_by_template_update": 0.0,
        "sort": 0.0,
        "top_k_slice": 0.0,
        "include_template_append": 0.0,
        "serialization": 0.0,
        "ranked_count": 0,
        "returned_count": 0,
        "total": 0.0,
    }

    cache_started = time.perf_counter()
    rows = published_template_cache.get_layout_candidate_rows(
        page_number,
        include_template_id=include_template_id,
        active_only=active_only,
    )
    breakdown["published_template_cache"] = {
        "hit": rows is not None,
        "lookup": time.perf_counter() - cache_started,
    }
    if rows is None:
        db_started = time.perf_counter()
        connect_started = time.perf_counter()
        conn = _connect()
        breakdown["connect"] = time.perf_counter() - connect_started
        connect_timing = getattr(conn, "connect_timing", {}) if conn is not None else {}
        if isinstance(connect_timing, dict):
            breakdown["pool_getconn"] = connect_timing.get("pool_getconn")
            breakdown["ensure_schema"] = connect_timing.get("ensure_schema")
            breakdown["postgres_ready_before"] = connect_timing.get("postgres_ready_before")
            breakdown["postgres_ready_after"] = connect_timing.get("postgres_ready_after")
            breakdown["schema_ensure_calls"] = connect_timing.get("schema_ensure_calls")
            breakdown["pool_before"] = connect_timing.get("pool_before")
            breakdown["pool_after"] = connect_timing.get("pool_after")
        with conn:
            if hasattr(conn, "execute_timed"):
                query_correlation_id = f"layout_match:{uuid4().hex[:12]}:page:{page_number}"
                breakdown["query_correlation_id"] = query_correlation_id
                execute_method = (
                    conn.execute_timed_diagnostics
                    if hasattr(conn, "execute_timed_diagnostics")
                    else conn.execute_timed
                )
                cursor, execute_timing = execute_method(
                    """
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
                    """,
                    (page_number,),
                    diagnostics=True,
                    query_comment=query_correlation_id,
                )
                breakdown["cursor_create"] = float(execute_timing.get("cursor_create") or 0.0)
                breakdown["connection_before"] = execute_timing.get("connection_before")
                breakdown["connection_before_execute"] = execute_timing.get("connection_before_execute")
                breakdown["connection_after_execute"] = execute_timing.get("connection_after_execute")
                connection_before = breakdown["connection_before"]
                if isinstance(connection_before, dict):
                    breakdown["backend_pid"] = connection_before.get("backend_pid")
                breakdown["execute"] = float(execute_timing.get("execute") or 0.0)
            else:
                execute_started = time.perf_counter()
                cursor = conn.execute(
                    """
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
                    """,
                    (page_number,),
                )
                breakdown["execute"] = time.perf_counter() - execute_started
            fetch_started = time.perf_counter()
            rows = cursor.fetchall()
            breakdown["fetch"] = time.perf_counter() - fetch_started
            breakdown["candidate_rows"] = len(rows)
            breakdown["db_total"] = time.perf_counter() - db_started
            logger.debug("[LAYOUT] include_template_id=%s rows_count=%s", include_template_id, len(rows))
    else:
        breakdown["candidate_rows"] = len(rows)
        logger.debug("[LAYOUT] published cache rows_count=%s", len(rows))

    best_by_template: Dict[str, Dict[str, Any]] = {}
    compared_count = 0
    prefilter_rejected_count = 0
    spatial_evaluated_count = 0
    compare_elapsed = 0.0
    for row in rows:
        template_id = row["template_id"]

        logger.debug(
            "[LAYOUT] checking template_id=%s status=%s include=%s",
            template_id,
            row["template_status"],
            template_id == include_template_id,
        )

        active_filter_started = time.perf_counter()
        if (
            active_only
            and row["template_status"] != "active"
            and template_id != include_template_id
        ):
            breakdown["active_filtered_count"] += 1
            breakdown["active_filter"] += time.perf_counter() - active_filter_started
            logger.debug("[LAYOUT] skipped by active_only template_id=%s", template_id)
            continue
        breakdown["active_filter"] += time.perf_counter() - active_filter_started

        parse_started = time.perf_counter()
        signature = signature_from_json(row["layout_signature_json"])
        breakdown["signature_parse"] += time.perf_counter() - parse_started

        if not signature:
            breakdown["invalid_signature_count"] += 1
            logger.debug("[LAYOUT] skipped invalid signature template_id=%s", template_id)
            continue

        compare_started = time.perf_counter()
        compare_timing: Dict[str, float] = {}
        similarity = compare_layout_signatures(
            query_signature,
            signature,
            timing=compare_timing,
        )
        compare_iteration_elapsed = time.perf_counter() - compare_started
        compare_elapsed += compare_iteration_elapsed
        breakdown["layout_compare"] += compare_iteration_elapsed
        compare_breakdown = breakdown["layout_compare_breakdown"]
        for key, value in compare_timing.items():
            compare_breakdown[key] = float(compare_breakdown.get(key) or 0.0) + float(value or 0.0)
        compared_count += 1
        if similarity.get("prefilter_rejected"):
            prefilter_rejected_count += 1
            logger.debug(
                "[LAYOUT] prefilter rejected template_id=%s label_count_score=%s area_distribution_score=%s count_threshold=%s area_threshold=%s",
                template_id,
                similarity.get("label_count_score"),
                similarity.get("area_distribution_score"),
                similarity.get("count_prefilter_threshold"),
                similarity.get("area_prefilter_threshold"),
            )
            continue
        spatial_evaluated_count += 1

        logger.debug("[LAYOUT] compared template_id=%s score=%s", template_id, similarity.get("score"))
        candidate_build_started = time.perf_counter()
        metadata = {
            "template_id": template_id,
            "template_name": row["template_name"],
            "template_status": row["template_status"],
            "page_count": row["page_count"],
            "detection_mode": row["detection_mode"],
            "main_page_number": row["main_page_number"],
            "template_page_id": row["template_page_id"],
            "matched_layout_reference_id": row["layout_reference_id"],
            "matched_layout_reference_page_number": row["page_number"],
            "page_number": row["page_number"],
            "matched_layout_reference_image_url": row["layout_reference_image_url"],
            "matched_layout_reference_source": row["layout_reference_source"],
            "matched_layout_reference_is_canonical": bool(row["layout_reference_is_canonical"]),
            "final_confidence_threshold": row["final_confidence_threshold"],
            "layout_weight": row["layout_weight"],
            "text_anchor_weight": row["text_anchor_weight"],
            "image_anchor_weight": row["image_anchor_weight"],
            "retrieval_engine": "layout_signature",
            "vector_store_engine": "layout-signature",
            "layout_signature_version": signature.get("version"),
            "layout_debug": similarity,
        }
        candidate = {
            "vector_id": f"layout_{template_id}_{row['layout_reference_id'] or row['page_number']}",
            "score": similarity["score"],
            "metadata": metadata,
            "layout_score": similarity["score"],
            "layout_debug": similarity,
            "_layout_signature": signature,
        }
        breakdown["candidate_build"] += time.perf_counter() - candidate_build_started
        update_started = time.perf_counter()
        previous = best_by_template.get(template_id)
        if previous is None or candidate["score"] > previous["score"]:
            best_by_template[template_id] = candidate
        breakdown["best_by_template_update"] += time.perf_counter() - update_started

    sort_started = time.perf_counter()
    ranked = sorted(best_by_template.values(), key=lambda item: item["score"], reverse=True)
    breakdown["sort"] = time.perf_counter() - sort_started
    slice_started = time.perf_counter()
    limited = ranked[:limit]
    breakdown["top_k_slice"] = time.perf_counter() - slice_started
    logger.debug(
        "[LAYOUT] compare timing compared=%s spatial_evaluated=%s prefilter_rejected=%s elapsed=%s",
        compared_count,
        spatial_evaluated_count,
        prefilter_rejected_count,
        round(compare_elapsed, 4),
    )
    
    include_started = time.perf_counter()
    if include_template_id and not any(
        item.get("metadata", {}).get("template_id") == include_template_id
        for item in limited
    ):
        included = next(
            (
                item
                for item in ranked[limit:]
                if item.get("metadata", {}).get("template_id") == include_template_id
            ),
            None,
        )
        if included is not None:
            limited.append(included)
    breakdown["include_template_append"] = time.perf_counter() - include_started

    serialization_started = time.perf_counter()
    breakdown["ranked_count"] = len(ranked)
    breakdown["returned_count"] = len(limited)
    breakdown["compared_count"] = compared_count
    breakdown["prefilter_rejected_count"] = prefilter_rejected_count
    breakdown["spatial_evaluated_count"] = spatial_evaluated_count
    breakdown["serialization"] = time.perf_counter() - serialization_started
    breakdown["total"] = time.perf_counter() - total_started
    if timing is not None:
        timing.setdefault("layout_candidate_searches", []).append(breakdown)
    return limited
