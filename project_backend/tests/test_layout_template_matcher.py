import json
import unittest
from unittest.mock import patch

from app.layout_signature_service import build_layout_signature
from app.layout_template_matcher import search_layout_candidates


def _layout(regions):
    return {
        "engine": "test",
        "model": "layout",
        "image_width": 1000,
        "image_height": 500,
        "regions": regions,
    }


def _region(label, x, y, width, height):
    return {
        "type": label,
        "confidence": 0.9,
        "roi": {
            "x_ratio": x,
            "y_ratio": y,
            "width_ratio": width,
            "height_ratio": height,
        },
    }


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, reference_rows, fallback_rows):
        self.reference_rows = reference_rows
        self.fallback_rows = fallback_rows
        self.page_filters = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("pragma"):
            return _Cursor([])
        if normalized.startswith("create") or normalized.startswith("alter"):
            return _Cursor([])
        if "from template_pages" in normalized and "join template_versions" in normalized:
            self.page_filters.append(params[0])
            return _Cursor([
                row for row in (self.reference_rows or self.fallback_rows)
                if (
                    row.get("detection_mode") == "main_page"
                    and row["page_number"] == row.get("main_page_number", 1)
                ) or (
                    row.get("detection_mode", "all_pages") != "main_page"
                    and row["page_number"] == params[0]
                )
            ])
        return _Cursor([])

    def commit(self):
        return None


class LayoutTemplateMatcherPageRoutingTest(unittest.TestCase):
    def _row(self, template_id, status="active"):
        return {
            "template_id": template_id,
            "template_name": template_id,
            "template_status": status,
            "page_count": 1,
            "final_confidence_threshold": 0.75,
            "layout_weight": 0.4,
            "text_anchor_weight": 0.3,
            "image_anchor_weight": 0.3,
            "detection_mode": "all_pages",
            "main_page_number": 1,
            "template_page_id": f"{template_id}_page_1",
            "layout_reference_id": None,
            "page_number": 1,
            "layout_reference_image_url": f"{template_id}.png",
            "layout_reference_source": "template_page",
            "layout_reference_is_canonical": 1,
            "layout_signature_json": json.dumps({"version": "test", "template_id": template_id}),
        }

    def test_include_template_id_is_not_dropped_when_outside_limit(self):
        connection = _Connection(
            reference_rows=[
                self._row("active_1"),
                self._row("active_2"),
                self._row("active_3"),
                self._row("draft_template", status="draft"),
            ],
            fallback_rows=[],
        )
        scores = {
            "active_1": {"score": 0.99},
            "active_2": {"score": 0.98},
            "active_3": {"score": 0.97},
            "draft_template": {"score": 0.6},
        }

        def fake_compare(_query_signature, reference_signature):
            return scores[reference_signature["template_id"]]

        with patch("app.layout_template_matcher.connect_db", return_value=connection), patch(
            "app.layout_template_matcher.compare_layout_signatures",
            side_effect=fake_compare,
        ):
            results = search_layout_candidates(
                {"version": "test"},
                page_number=1,
                limit=2,
                include_template_id="draft_template",
            )

        self.assertEqual([item["metadata"]["template_id"] for item in results], ["active_1", "active_2", "draft_template"])

    def test_search_layout_candidates_filters_references_by_query_page_number(self):
        page_one_signature = build_layout_signature(_layout([_region("text", 0.1, 0.1, 0.3, 0.08)]))
        page_two_signature = build_layout_signature(_layout([_region("table", 0.1, 0.4, 0.75, 0.3)]))
        connection = _Connection(
            reference_rows=[
                {
                    "template_id": "template_a",
                    "template_name": "Template A",
                    "template_status": "active",
                    "page_count": 2,
                    "final_confidence_threshold": 0.8,
                    "layout_weight": 0.4,
                    "text_anchor_weight": 0.3,
                    "image_anchor_weight": 0.3,
                    "detection_mode": "all_pages",
                    "main_page_number": 1,
                    "template_page_id": "page_1",
                    "layout_reference_id": None,
                    "page_number": 1,
                    "layout_reference_image_url": "page_1.png",
                    "layout_reference_source": "template_page",
                    "layout_reference_is_canonical": 1,
                    "layout_signature_json": json.dumps(page_one_signature),
                },
                {
                    "template_id": "template_a",
                    "template_name": "Template A",
                    "template_status": "active",
                    "page_count": 2,
                    "final_confidence_threshold": 0.8,
                    "layout_weight": 0.4,
                    "text_anchor_weight": 0.3,
                    "image_anchor_weight": 0.3,
                    "detection_mode": "all_pages",
                    "main_page_number": 1,
                    "template_page_id": "page_2",
                    "layout_reference_id": None,
                    "page_number": 2,
                    "layout_reference_image_url": "page_2.png",
                    "layout_reference_source": "template_page",
                    "layout_reference_is_canonical": 1,
                    "layout_signature_json": json.dumps(page_two_signature),
                },
            ],
            fallback_rows=[],
        )

        with patch("app.layout_template_matcher.connect_db", return_value=connection):
            results = search_layout_candidates(page_two_signature, page_number=1)

        self.assertEqual(connection.page_filters, [1])
        self.assertEqual(len(results), 1)
        metadata = results[0]["metadata"]
        self.assertEqual(metadata["matched_layout_reference_page_number"], 1)
        self.assertIsNone(metadata["matched_layout_reference_id"])

    def test_main_page_mode_uses_canonical_reference_for_any_query_page(self):
        main_signature = build_layout_signature(_layout([_region("text", 0.1, 0.1, 0.3, 0.08)]))
        other_signature = build_layout_signature(_layout([_region("table", 0.1, 0.4, 0.75, 0.3)]))
        connection = _Connection(
            reference_rows=[
                {
                    "template_id": "template_main",
                    "template_name": "Main Mode",
                    "template_status": "active",
                    "page_count": 3,
                    "final_confidence_threshold": 0.75,
                    "layout_weight": 0.4,
                    "text_anchor_weight": 0.3,
                    "image_anchor_weight": 0.3,
                    "detection_mode": "main_page",
                    "main_page_number": 2,
                    "template_page_id": "page_2",
                    "layout_reference_id": None,
                    "page_number": 2,
                    "layout_reference_image_url": "page_2.png",
                    "layout_reference_source": "template_page",
                    "layout_reference_is_canonical": 1,
                    "layout_signature_json": json.dumps(main_signature),
                },
                {
                    "template_id": "template_main",
                    "template_name": "Main Mode",
                    "template_status": "active",
                    "page_count": 3,
                    "final_confidence_threshold": 0.75,
                    "layout_weight": 0.4,
                    "text_anchor_weight": 0.3,
                    "image_anchor_weight": 0.3,
                    "detection_mode": "main_page",
                    "main_page_number": 2,
                    "template_page_id": "page_3",
                    "layout_reference_id": None,
                    "page_number": 3,
                    "layout_reference_image_url": "page_3.png",
                    "layout_reference_source": "template_page",
                    "layout_reference_is_canonical": 0,
                    "layout_signature_json": json.dumps(other_signature),
                },
            ],
            fallback_rows=[],
        )

        with patch("app.layout_template_matcher.connect_db", return_value=connection):
            results = search_layout_candidates(main_signature, page_number=7)

        self.assertEqual(len(results), 1)
        metadata = results[0]["metadata"]
        self.assertEqual(metadata["detection_mode"], "main_page")
        self.assertEqual(metadata["main_page_number"], 2)
        self.assertIsNone(metadata["matched_layout_reference_id"])


if __name__ == "__main__":
    unittest.main()
