"""Cheap image-header checks used before template anchor inference."""
from __future__ import annotations

import base64
import io
import math
import os
from pathlib import Path

from PIL import Image


def tolerance() -> float:
    try:
        value = float(os.getenv("TEMPLATE_ASPECT_RATIO_TOLERANCE", "0.08"))
        return value if math.isfinite(value) and 0 <= value <= 1 else 0.08
    except ValueError:
        return 0.08


def image_geometry(source: str | None) -> tuple[int, int, float] | None:
    """Read dimensions without decoding pixels or downloading remote references."""
    if not source:
        return None
    try:
        if source.startswith("data:image/"):
            resource = io.BytesIO(base64.b64decode(source.split(",", 1)[1], validate=True))
        elif source.startswith(("http://", "https://")):
            return None
        else:
            resource = Path(source)
        with Image.open(resource) as image:
            width, height = image.size
        return (width, height, width / height) if min(width, height) > 0 else None
    except (OSError, ValueError, IndexError, TypeError, Image.DecompressionBombError):
        return None


def image_aspect(source: str | None) -> float | None:
    geometry = image_geometry(source)
    return geometry[2] if geometry else None


def persist_page_geometry(conn, page_id: str, template_id: str | None = None) -> bool:
    scope = " AND template_version_id = ?" if template_id is not None else ""
    row = conn.execute(
        f"SELECT normalized_image_url, sample_image_url FROM template_pages WHERE id = ?{scope}",
        (page_id, template_id) if template_id is not None else (page_id,),
    ).fetchone()
    if row is None:
        return False
    item = dict(row)
    geometry = image_geometry(item.get("normalized_image_url") or item.get("sample_image_url"))
    width, height, ratio = geometry or (None, None, None)
    conn.execute(
        """UPDATE template_pages SET image_width = ?, image_height = ?, image_aspect_ratio = ?,
           image_geometry_checked = TRUE WHERE id = ?""", (width, height, ratio, page_id),
    )
    return geometry is not None


def backfill_page_geometry(conn, template_id: str | None = None) -> dict[str, int]:
    """Process old/new pages once; unreadable images are retried only when replaced."""
    counts = {"processed": 0, "stored": 0, "unknown": 0}
    where = "image_geometry_checked = FALSE"
    params = ()
    if template_id is not None:
        where += " AND template_version_id = ?"
        params = (template_id,)
    while True:
        rows = conn.execute(f"SELECT id FROM template_pages WHERE {where} ORDER BY id LIMIT 100", params).fetchall()
        if not rows:
            break
        for row in rows:
            stored = persist_page_geometry(conn, str(dict(row)["id"]))
            counts["processed"] += 1
            counts["stored" if stored else "unknown"] += 1
    return counts


def load_template_page_aspects(conn, template_ids: list[str]) -> dict[str, dict[int, float]]:
    if not template_ids:
        return {}
    placeholders = ",".join("?" for _ in template_ids)
    rows = conn.execute(
        f"""SELECT template_version_id, page_number, image_aspect_ratio
            FROM template_pages WHERE template_version_id IN ({placeholders})""", tuple(template_ids),
    ).fetchall()
    aspects: dict[str, dict[int, float]] = {}
    for row in rows:
        item = dict(row)
        aspect = item.get("image_aspect_ratio")
        if aspect is not None and math.isfinite(float(aspect)) and float(aspect) > 0:
            aspects.setdefault(str(item["template_version_id"]), {})[int(item["page_number"])] = float(aspect)
    return aspects


def check_template(template: dict, query_aspects: dict[int, float | None], allowed_difference: float) -> dict:
    references = template.get("page_aspect_ratios") or {}
    page_numbers = ([int(template.get("main_page_number") or 1)]
                    if template.get("detection_mode") == "main_page" else list(query_aspects))
    comparisons = []
    for page in page_numbers:
        query = query_aspects.get(page)
        reference = references.get(page, references.get(str(page)))
        if not query or not reference:
            continue
        difference = max(query, reference) / min(query, reference) - 1
        orientation_matches = (query - 1) * (reference - 1) >= 0
        comparisons.append({"page_number": page, "query_aspect": query, "template_aspect": reference,
                            "relative_difference": difference, "orientation_matches": orientation_matches,
                            "passed": orientation_matches and difference <= allowed_difference + 1e-9})
    return {"passed": all(item["passed"] for item in comparisons), "comparisons": comparisons,
            "status": "checked" if comparisons else "unknown_dimensions"}
