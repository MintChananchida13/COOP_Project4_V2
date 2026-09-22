import os
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.db import connect as connect_db
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
) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
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
        ).fetchall()
        logger.debug("[LAYOUT] include_template_id=%s rows_count=%s", include_template_id, len(rows))

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

        if (
            active_only
            and row["template_status"] != "active"
            and template_id != include_template_id
        ):
            logger.debug("[LAYOUT] skipped by active_only template_id=%s", template_id)
            continue

        signature = signature_from_json(row["layout_signature_json"])

        if not signature:
            logger.debug("[LAYOUT] skipped invalid signature template_id=%s", template_id)
            continue

        compare_started = time.perf_counter()
        similarity = compare_layout_signatures(
            query_signature,
            signature,
        )
        compare_elapsed += time.perf_counter() - compare_started
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
        }
        previous = best_by_template.get(template_id)
        if previous is None or candidate["score"] > previous["score"]:
            best_by_template[template_id] = candidate

    ranked = sorted(best_by_template.values(), key=lambda item: item["score"], reverse=True)
    limited = ranked[:limit]
    logger.debug(
        "[LAYOUT] compare timing compared=%s spatial_evaluated=%s prefilter_rejected=%s elapsed=%s",
        compared_count,
        spatial_evaluated_count,
        prefilter_rejected_count,
        round(compare_elapsed, 4),
    )
    
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
    return limited
