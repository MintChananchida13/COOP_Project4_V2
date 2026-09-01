import os
import sys
import types
import unittest
import importlib.util
from unittest.mock import patch

import numpy as np

from app.model_runtime_client import ModelRuntimeUnavailableError
from app.table_recognition_v2_adapter import (
    TableRecognitionV2UnavailableError,
    _build_table_candidate,
    _calculate_ocr_confidence,
    _calculate_table_quality,
    _deduplicate_assigned_table_cells,
    _postprocess_table_result,
    _recognize_raw_ocr_geometry_table,
    _recognize_text_crops_with_core,
    _recover_slanext_structure_collapse,
    _recover_slanext_row_collapse,
    _reassign_ocr_text_to_slanext_cells,
    _select_best_table_candidate,
    _structured_assignment_quality,
    _section_from_region_candidate,
    _slanext_result_from_output,
    _try_semi_structured_table,
    _try_forced_semi_after_empty_slanext,
    recognize_table_v2,
    recognize_table_v2_local,
    table_recognition_runtime_summary,
)
from app.table_grid_analyzer import analyze_table_regions
from app.ocr_postprocess import normalize_ocr_text, normalize_table_rows, parse_table_html_with_bs4


class FakeTableRecognitionPipelineV2:
    init_kwargs = None

    def __init__(self, **kwargs):
        FakeTableRecognitionPipelineV2.init_kwargs = kwargs

    def predict(self, **kwargs):
        return [{"html": "<table><tr><td>A</td><td>B</td></tr></table>"}]


class EmptyTableRecognitionPipelineV2:
    init_kwargs = None

    def __init__(self, **kwargs):
        EmptyTableRecognitionPipelineV2.init_kwargs = kwargs

    def predict(self, **kwargs):
        return [{}]


class TableRecognitionV2AdapterRuntimeRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.multiple(
            "app.table_recognition_v2_adapter",
            _TABLE_MODEL=None,
            _TABLE_MODEL_KIND="",
            _TABLE_WIRED_MODEL_NAME="SLANeXt_wired",
            _TABLE_WIRELESS_MODEL_NAME="SLANeXt_wireless",
            _TABLE_MODEL_NAME="SLANeXt_wired/SLANeXt_wireless",
            _TABLE_TEXT_RECOGNITION_MODEL_NAME="th_PP-OCRv5_mobile_rec",
            _TABLE_DEVICE="cpu",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeTableRecognitionPipelineV2.init_kwargs = None

    def test_remote_runtime_is_used_without_loading_local_pipeline(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"TABLE_MODEL_URL": "https://model.example"}, clear=False), patch(
            "app.table_recognition_v2_adapter.remote_recognize_table_raw",
            return_value={"raw_output": [{"html": "<table><tr><td>A</td></tr></table>"}]},
        ) as remote, patch("app.table_recognition_v2_adapter._load_table_model") as load_local:
            result = recognize_table_v2(image)

        self.assertEqual(result["table_rows"], [["A"]])
        remote.assert_called_once()
        load_local.assert_not_called()

    def test_remote_runtime_error_raises_without_local_fallback(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"TABLE_MODEL_URL": "https://model.example"}, clear=False), patch(
            "app.table_recognition_v2_adapter.remote_recognize_table_raw",
            side_effect=ModelRuntimeUnavailableError("remote table boom"),
        ), patch("app.table_recognition_v2_adapter._load_table_model") as load_local:
            with self.assertRaisesRegex(TableRecognitionV2UnavailableError, "remote table boom"):
                recognize_table_v2(image)

        load_local.assert_not_called()

    def test_remote_runtime_none_raises_clear_error(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)

        with patch.dict("os.environ", {"TABLE_MODEL_URL": "https://model.example"}, clear=False), patch(
            "app.table_recognition_v2_adapter.remote_recognize_table_raw",
            return_value=None,
        ), patch("app.table_recognition_v2_adapter._load_table_model") as load_local:
            with self.assertRaisesRegex(TableRecognitionV2UnavailableError, "Remote Table Recognition runtime returned no result."):
                recognize_table_v2(image)

        load_local.assert_not_called()

    def test_empty_model_service_url_uses_local_pipeline(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=FakeTableRecognitionPipelineV2)

        with patch.dict("os.environ", {"TABLE_MODEL_URL": ""}, clear=False), patch.dict(sys.modules, {"paddleocr": fake_paddleocr}), patch(
            "app.table_recognition_v2_adapter.cv2.imwrite",
            return_value=True,
        ), patch("app.table_recognition_v2_adapter.Path.unlink"):
            result = recognize_table_v2(image)

        self.assertEqual(result["engine"], "table_recognition_v2")
        self.assertEqual(result["model"], "SLANeXt_wired/SLANeXt_wireless")
        self.assertEqual(FakeTableRecognitionPipelineV2.init_kwargs["wired_table_structure_recognition_model_name"], "SLANeXt_wired")
        self.assertEqual(FakeTableRecognitionPipelineV2.init_kwargs["wireless_table_structure_recognition_model_name"], "SLANeXt_wireless")
        self.assertEqual(FakeTableRecognitionPipelineV2.init_kwargs["text_recognition_model_name"], "th_PP-OCRv5_mobile_rec")
        self.assertEqual(FakeTableRecognitionPipelineV2.init_kwargs["device"], "cpu")

    def test_paddle_table_device_cpu_is_used_by_pipeline_and_summary(self) -> None:
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=FakeTableRecognitionPipelineV2)

        with patch("app.table_recognition_v2_adapter._TABLE_DEVICE", "cpu"), patch.dict(sys.modules, {"paddleocr": fake_paddleocr}):
            summary = table_recognition_runtime_summary()

        self.assertEqual(
            summary,
            {
                "enabled": True,
                "structure_model": "SLANeXt_wired/SLANeXt_wireless",
                "wired_structure_model": "SLANeXt_wired",
                "wireless_structure_model": "SLANeXt_wireless",
                "text_recognition_model": "th_PP-OCRv5_mobile_rec",
                "device": "cpu",
            },
        )
        self.assertIsNotNone(FakeTableRecognitionPipelineV2.init_kwargs)
        self.assertEqual(FakeTableRecognitionPipelineV2.init_kwargs["device"], "cpu")

    def test_paddle_table_device_env_gpu_is_ignored_for_cpu_only_runtime(self) -> None:
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=FakeTableRecognitionPipelineV2)

        with patch.dict("os.environ", {"PADDLE_TABLE_DEVICE": "gpu:0"}, clear=False), patch.dict(sys.modules, {"paddleocr": fake_paddleocr}):
            summary = table_recognition_runtime_summary()

        self.assertEqual(summary["device"], "cpu")
        self.assertIsNotNone(FakeTableRecognitionPipelineV2.init_kwargs)
        self.assertEqual(FakeTableRecognitionPipelineV2.init_kwargs["device"], "cpu")

    def test_cached_pipeline_is_reused(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=FakeTableRecognitionPipelineV2)

        with patch.dict(sys.modules, {"paddleocr": fake_paddleocr}), patch(
            "app.table_recognition_v2_adapter.cv2.imwrite",
            return_value=True,
        ), patch("app.table_recognition_v2_adapter.Path.unlink"):
            first = recognize_table_v2_local(image)
            first_model = first["model"]
            FakeTableRecognitionPipelineV2.init_kwargs = {"sentinel": "should_not_be_reinitialized"}
            second = recognize_table_v2_local(image)

        self.assertEqual(first_model, "SLANeXt_wired/SLANeXt_wireless")
        self.assertEqual(second["model"], "SLANeXt_wired/SLANeXt_wireless")
        self.assertEqual(FakeTableRecognitionPipelineV2.init_kwargs, {"sentinel": "should_not_be_reinitialized"})

    def test_runtime_endpoint_can_use_warmed_local_model_function(self) -> None:
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=FakeTableRecognitionPipelineV2)

        with patch.dict(sys.modules, {"paddleocr": fake_paddleocr}), patch(
            "app.table_recognition_v2_adapter.cv2.imwrite",
            return_value=True,
        ), patch("app.table_recognition_v2_adapter.Path.unlink"):
            result = recognize_table_v2_local(image)

        self.assertEqual(result["table_rows"], [["A", "B"]])

    def test_forced_semi_clusters_text_boxes_when_slanet_is_empty(self) -> None:
        image = np.zeros((120, 260, 3), dtype=np.uint8)
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=EmptyTableRecognitionPipelineV2)
        detected_regions = [
            {"bbox": {"x": 10, "y": 10, "width": 45, "height": 15}},
            {"bbox": {"x": 120, "y": 10, "width": 45, "height": 15}},
            {"bbox": {"x": 10, "y": 50, "width": 45, "height": 15}},
            {"bbox": {"x": 120, "y": 50, "width": 45, "height": 15}},
        ]
        recognitions = [
            {"text": "Name", "confidence": 0.9},
            {"text": "Amount", "confidence": 0.9},
            {"text": "Alice", "confidence": 0.8},
            {"text": "100", "confidence": 0.8},
        ]

        with patch.dict(sys.modules, {"paddleocr": fake_paddleocr}), patch(
            "app.table_recognition_v2_adapter.cv2.imwrite",
            return_value=True,
        ), patch("app.table_recognition_v2_adapter.Path.unlink"), patch(
            "app.table_recognition_v2_adapter.detect_text_boxes",
            return_value={"regions": detected_regions},
        ) as detect, patch(
            "app.table_recognition_v2_adapter.run_paddle_thai_ocr_batch",
            return_value=recognitions,
        ) as recognize:
            result = recognize_table_v2_local(image)

        self.assertEqual(result["table_rows"], [["Name", "Amount"], ["Alice", "100"]])
        self.assertEqual(result["table_structured"]["rows"], [["Name", "Amount"], ["Alice", "100"]])
        self.assertIn(result["table_selected_method"], {"coordinate_based_semi_forced", "ocr_table_fallback"})
        self.assertTrue(result["table_debug"].get("forced_after_empty_slanext", result["table_selected_method"] == "ocr_table_fallback"))
        self.assertEqual(result["table_debug"]["column_count"], 2)
        self.assertGreaterEqual(detect.call_count, 1)
        self.assertGreaterEqual(recognize.call_count, 1)

    def test_raw_ocr_geometry_table_returns_table_when_ocr_text_exists(self) -> None:
        image = np.zeros((80, 200, 3), dtype=np.uint8)
        detected_regions = [
            {"bbox": {"x": 10, "y": 10, "width": 40, "height": 12}},
            {"bbox": {"x": 100, "y": 11, "width": 50, "height": 12}},
            {"bbox": {"x": 10, "y": 40, "width": 40, "height": 12}},
        ]
        recognitions = [
            {"text": "A1", "confidence": 0.91},
            {"text": "B1", "confidence": 0.81},
            {"text": "A2", "confidence": 0.71},
        ]

        with patch("app.table_recognition_v2_adapter.detect_text_boxes", return_value={"regions": detected_regions}), patch(
            "app.table_recognition_v2_adapter.run_paddle_thai_ocr_batch",
            return_value=recognitions,
        ):
            result = _recognize_raw_ocr_geometry_table(image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_selected_method"], "raw_ocr_geometry_table")
        self.assertEqual(result["table_rows"], [["A1", "B1"], ["A2", ""]])
        self.assertEqual(result["table_structured"]["cells"][0]["text"], "A1")
        self.assertAlmostEqual(result["confidence"], (0.91 + 0.81 + 0.71) / 3)

    def test_table_text_crops_use_shared_text_ocr_core(self) -> None:
        crops = [np.zeros((20, 80, 3), dtype=np.uint8), np.zeros((20, 80, 3), dtype=np.uint8)]

        with patch(
            "app.ocr_adapter.recognize_text_roi",
            side_effect=[
                {"text": "A", "confidence": 0.91},
                {"text": "B", "confidence": 0.92},
            ],
        ) as recognize:
            recognitions, debug = _recognize_text_crops_with_core(crops, "table_core_test")

        self.assertEqual([item["text"] for item in recognitions], ["A", "B"])
        self.assertEqual(debug["ocr_core"], "recognize_text_roi")
        self.assertEqual(debug["crop_count"], 2)
        self.assertEqual(recognize.call_count, 2)

    def test_row_collapse_recovery_splits_collapsed_slanext_body_rows(self) -> None:
        image = np.zeros((140, 220, 3), dtype=np.uint8)
        structured = {
            "rows": [["Code", "Name"], ["IC-0001 IC-0002 IC-0003", "Mouse Keyboard Scanner"]],
            "headerRowCount": 1,
            "cells": [
                {"row": 0, "col": 0, "text": "Code", "bbox": {"x": 0, "y": 0, "width": 80, "height": 24}},
                {"row": 0, "col": 1, "text": "Name", "bbox": {"x": 80, "y": 0, "width": 120, "height": 24}},
                {"row": 1, "col": 0, "text": "IC-0001 IC-0002 IC-0003", "bbox": {"x": 0, "y": 24, "width": 80, "height": 90}},
                {"row": 1, "col": 1, "text": "Mouse Keyboard Scanner", "bbox": {"x": 80, "y": 24, "width": 120, "height": 90}},
            ],
        }
        candidate = _build_table_candidate({"table_rows": structured["rows"], "table_structured": structured}, "slanext")
        ocr_cells = [
            {"text": "IC-0001", "confidence": 0.9, "bbox": {"x": 8, "y": 34, "width": 42, "height": 10}, "x": 8, "y": 34, "width": 42, "height": 10, "center_x": 29, "center_y": 39},
            {"text": "Mouse", "confidence": 0.9, "bbox": {"x": 94, "y": 34, "width": 34, "height": 10}, "x": 94, "y": 34, "width": 34, "height": 10, "center_x": 111, "center_y": 39},
            {"text": "IC-0002", "confidence": 0.9, "bbox": {"x": 8, "y": 58, "width": 42, "height": 10}, "x": 8, "y": 58, "width": 42, "height": 10, "center_x": 29, "center_y": 63},
            {"text": "Keyboard", "confidence": 0.9, "bbox": {"x": 94, "y": 58, "width": 48, "height": 10}, "x": 94, "y": 58, "width": 48, "height": 10, "center_x": 118, "center_y": 63},
            {"text": "IC-0003", "confidence": 0.9, "bbox": {"x": 8, "y": 82, "width": 42, "height": 10}, "x": 8, "y": 82, "width": 42, "height": 10, "center_x": 29, "center_y": 87},
            {"text": "Scanner", "confidence": 0.9, "bbox": {"x": 94, "y": 82, "width": 44, "height": 10}, "x": 94, "y": 82, "width": 44, "height": 10, "center_x": 116, "center_y": 87},
        ]

        with patch("app.table_recognition_v2_adapter._ocr_cells_from_text_detection", return_value=(ocr_cells, [0.9] * len(ocr_cells), {"detected_boxes": len(ocr_cells)})):
            recovered, debug = _recover_slanext_row_collapse(candidate, image)

        self.assertTrue(debug["suspected_row_collapse"])
        self.assertEqual(debug["body_row_count"], 1)
        self.assertEqual(debug["y_cluster_count"], 3)
        self.assertEqual(debug["supporting_columns"], 2)
        self.assertEqual(recovered["table_rows"], [["Code", "Name"], ["IC-0001", "Mouse"], ["IC-0002", "Keyboard"], ["IC-0003", "Scanner"]])

    def test_structure_collapse_recovery_splits_collapsed_columns_only(self) -> None:
        image = np.zeros((140, 240, 3), dtype=np.uint8)
        structured = {
            "rows": [["Info", "Amount"], ["Code Name", "100"], ["Code2 Name2", "200"]],
            "headerRowCount": 1,
            "cells": [
                {"row": 0, "col": 0, "text": "Info", "bbox": {"x": 0, "y": 0, "width": 120, "height": 24}},
                {"row": 0, "col": 1, "text": "Amount", "bbox": {"x": 120, "y": 0, "width": 100, "height": 24}},
                {"row": 1, "col": 0, "text": "IC-0001 Mouse", "bbox": {"x": 0, "y": 24, "width": 120, "height": 28}},
                {"row": 1, "col": 1, "text": "100", "bbox": {"x": 120, "y": 24, "width": 100, "height": 28}},
                {"row": 2, "col": 0, "text": "IC-0002 Keyboard", "bbox": {"x": 0, "y": 52, "width": 120, "height": 28}},
                {"row": 2, "col": 1, "text": "200", "bbox": {"x": 120, "y": 52, "width": 100, "height": 28}},
            ],
        }
        candidate = _build_table_candidate({"table_rows": structured["rows"], "table_structured": structured}, "slanext")
        ocr_cells = [
            {"text": "IC-0001", "confidence": 0.9, "bbox": {"x": 8, "y": 32, "width": 42, "height": 10}, "x": 8, "y": 32, "width": 42, "height": 10, "center_x": 29, "center_y": 37},
            {"text": "Mouse", "confidence": 0.9, "bbox": {"x": 68, "y": 32, "width": 34, "height": 10}, "x": 68, "y": 32, "width": 34, "height": 10, "center_x": 85, "center_y": 37},
            {"text": "100", "confidence": 0.9, "bbox": {"x": 146, "y": 32, "width": 24, "height": 10}, "x": 146, "y": 32, "width": 24, "height": 10, "center_x": 158, "center_y": 37},
            {"text": "IC-0002", "confidence": 0.9, "bbox": {"x": 8, "y": 60, "width": 42, "height": 10}, "x": 8, "y": 60, "width": 42, "height": 10, "center_x": 29, "center_y": 65},
            {"text": "Keyboard", "confidence": 0.9, "bbox": {"x": 68, "y": 60, "width": 48, "height": 10}, "x": 68, "y": 60, "width": 48, "height": 10, "center_x": 92, "center_y": 65},
            {"text": "200", "confidence": 0.9, "bbox": {"x": 146, "y": 60, "width": 24, "height": 10}, "x": 146, "y": 60, "width": 24, "height": 10, "center_x": 158, "center_y": 65},
        ]

        with patch("app.table_recognition_v2_adapter._ocr_cells_from_text_detection", return_value=(ocr_cells, [0.9] * len(ocr_cells), {"detected_boxes": len(ocr_cells)})):
            recovered, debug = _recover_slanext_structure_collapse(candidate, image)

        self.assertFalse(debug["row_collapse"])
        self.assertTrue(debug["column_collapse"])
        self.assertEqual(debug["recovery_axis"], "column")
        self.assertEqual(debug["x_cluster_count"], 3)
        self.assertEqual(recovered["table_rows"][1], ["IC-0001", "Mouse", "100"])
        self.assertEqual(recovered["table_rows"][2], ["IC-0002", "Keyboard", "200"])

    def test_slanext_result_records_source_structure_model_from_raw_output(self) -> None:
        image = np.zeros((80, 160, 3), dtype=np.uint8)
        output = [{
            "structure_model": "SLANeXt_wireless",
            "html": "<table><tr><td>A</td><td>B</td></tr></table>",
        }]

        result = _slanext_result_from_output(output, image, 0.0)

        self.assertEqual(result["table_debug"]["source_structure_model"], "SLANeXt_wireless")

    def test_local_table_flow_runs_structure_collapse_recovery_as_common_post_validation(self) -> None:
        image = np.zeros((80, 160, 3), dtype=np.uint8)
        output = [{
            "structure_model": "SLANeXt_wired",
            "html": "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>",
        }]
        recovery_debug = {
            "attempted": True,
            "source_structure_model": "SLANeXt_wired",
            "row_collapse": False,
            "column_collapse": False,
            "x_cluster_count": 2,
            "y_cluster_count": 2,
            "alignment_score": 0.0,
            "recovery_axis": "none",
            "recovery_success": False,
            "selected": False,
            "recovered_row_count": 2,
            "recovered_column_count": 2,
            "reason": "structure_collapse_not_supported",
        }

        with patch("app.table_recognition_v2_adapter._load_table_model", return_value=object()), \
            patch("app.table_recognition_v2_adapter._predict_table_model", return_value=output), \
            patch("app.table_recognition_v2_adapter._recover_slanext_structure_collapse", side_effect=lambda candidate, img: (candidate, recovery_debug)) as recover:
            result = recognize_table_v2_local(image)

        self.assertEqual(recover.call_count, 1)
        self.assertEqual(result["table_debug"]["structure_collapse_recovery"]["source_structure_model"], "SLANeXt_wired")
        self.assertEqual(result["table_debug"]["structure_collapse_recovery"]["recovery_axis"], "none")

    @unittest.skipUnless(importlib.util.find_spec("bs4") and importlib.util.find_spec("lxml"), "beautifulsoup4/lxml not installed")
    def test_table_html_postprocess_uses_beautifulsoup_lxml(self) -> None:
        result = parse_table_html_with_bs4("<table><tr><th> วันที่ </th><th>ยอดเงิน</th></tr><tr><td>  1  ม.ค.  </td><td>  100.00 </td></tr></table>")

        self.assertIsNotNone(result)
        self.assertEqual(result["rows"], [["วันที่", "ยอดเงิน"], ["1 ม.ค.", "100.00"]])
        self.assertEqual(result["parser"], "beautifulsoup4+lxml")

    @unittest.skipUnless(importlib.util.find_spec("bs4") and importlib.util.find_spec("lxml"), "beautifulsoup4/lxml not installed")
    def test_table_html_postprocess_preserves_empty_tr_rows(self) -> None:
        result = parse_table_html_with_bs4("<table><tr><td>A</td></tr><tr></tr><tr><td>C</td></tr></table>")

        self.assertIsNotNone(result)
        self.assertEqual(result["rows"], [["A"], [""], ["C"]])

    @unittest.skipUnless(importlib.util.find_spec("pythainlp"), "pythainlp not installed")
    def test_ocr_text_postprocess_uses_pythainlp_normalization(self) -> None:
        self.assertEqual(normalize_ocr_text("  ทดสอบ   OCR  \n\n  ภาษาไทย  "), "ทดสอบ OCR\nภาษาไทย")

    def test_ocr_text_postprocess_removes_obvious_noise(self) -> None:
        self.assertEqual(normalize_ocr_text("....."), "")
        self.assertEqual(normalize_ocr_text("-----"), "")
        self.assertEqual(normalize_ocr_text("P นางสาวศิรินทร์ สุวรรณ a"), "นางสาวศิรินทร์ สุวรรณ")
        self.assertEqual(normalize_ocr_text("ชื่อ ..... นางสาวศิรินทร์"), "ชื่อ นางสาวศิรินทร์")

    def test_ocr_text_postprocess_preserves_real_values(self) -> None:
        self.assertEqual(normalize_ocr_text("1"), "1")
        self.assertEqual(normalize_ocr_text("4"), "4")
        self.assertEqual(normalize_ocr_text("ประเภท A"), "ประเภท A")
        self.assertEqual(normalize_ocr_text("1.25"), "1.25")
        self.assertEqual(normalize_ocr_text("ABC-123"), "ABC-123")
        self.assertEqual(normalize_ocr_text("พ.ศ."), "พ.ศ.")

    def test_ocr_text_postprocess_can_skip_noise_cleanup_for_ground_truth(self) -> None:
        self.assertEqual(normalize_ocr_text("P นางสาวศิรินทร์ สุวรรณ a", cleanup_noise=False), "P นางสาวศิรินทร์ สุวรรณ a")

    def test_table_row_postprocess_preserves_empty_structure_rows(self) -> None:
        rows = normalize_table_rows([["หัวข้อ", "จำนวน"], ["", ""], ["รวม", "10"]])

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1], ["", ""])
        self.assertEqual(rows[2][1], "10")

    def test_slanext_good_structure_wins_over_borderless(self) -> None:
        slanext = _build_table_candidate(
            {
                "text": "",
                "table_rows": [["A", "B"], ["C", "D"]],
                "table_structured": {
                    "rows": [["A", "B"], ["C", "D"]],
                    "cells": [
                        {"row": 0, "col": 0, "text": "A"},
                        {"row": 0, "col": 1, "text": "B"},
                        {"row": 1, "col": 0, "text": "C"},
                        {"row": 1, "col": 1, "text": "D"},
                    ],
                },
            },
            "slanext",
        )
        borderless = _build_table_candidate({"table_rows": [["A", ""], ["C", ""]]}, "borderless_text_clustering")

        selected, reason = _select_best_table_candidate([slanext, borderless])

        self.assertIs(selected, slanext)
        self.assertIn(reason, {"higher_final_confidence", "tie_preferred_structured_slanext", "tie_breaker"})

    def test_sparse_slanext_loses_to_more_consistent_borderless(self) -> None:
        slanext = _build_table_candidate({"table_rows": [["A", "", "", ""], ["", "", "", ""], ["B", "", "", ""]]}, "slanext")
        borderless = _build_table_candidate(
            {
                "table_rows": [["A", "B", "C"], ["D", "E", "F"]],
                "segments": [{"text": "A", "confidence": 0.92}, {"text": "B", "confidence": 0.9}],
            },
            "borderless_text_clustering",
        )

        selected, reason = _select_best_table_candidate([slanext, borderless])

        self.assertIs(selected, borderless)
        self.assertIn(reason, {"higher_final_confidence", "borderless_improved_low_quality_slanext"})

    def test_tie_prefers_structured_slanext(self) -> None:
        rows = [["A", "B"], ["C", "D"]]
        slanext = _build_table_candidate(
            {
                "table_rows": rows,
                "table_structured": {
                    "rows": rows,
                    "cells": [
                        {"row": 0, "col": 0, "text": "A"},
                        {"row": 0, "col": 1, "text": "B"},
                        {"row": 1, "col": 0, "text": "C"},
                        {"row": 1, "col": 1, "text": "D"},
                    ],
                },
            },
            "slanext",
        )
        borderless = _build_table_candidate({"table_rows": rows}, "borderless_text_clustering")
        slanext["confidence"] = 0.8
        slanext["table_debug"]["final_confidence"] = 0.8
        borderless["confidence"] = 0.81
        borderless["table_debug"]["final_confidence"] = 0.81

        selected, reason = _select_best_table_candidate([borderless, slanext])

        self.assertIs(selected, slanext)
        self.assertEqual(reason, "tie_preferred_structured_slanext")

    def test_borderless_error_returns_slanext_candidate(self) -> None:
        image = np.zeros((120, 260, 3), dtype=np.uint8)
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=FakeTableRecognitionPipelineV2)

        with patch.dict(sys.modules, {"paddleocr": fake_paddleocr}), patch(
            "app.table_recognition_v2_adapter.cv2.imwrite",
            return_value=True,
        ), patch("app.table_recognition_v2_adapter.Path.unlink"), patch(
            "app.table_recognition_v2_adapter._recognize_borderless_table",
            side_effect=RuntimeError("borderless boom"),
        ):
            result = recognize_table_v2_local(image)

        self.assertEqual(result["table_selected_method"], "slanext")
        self.assertEqual(result["table_rows"], [["A", "B"]])

    def test_table_debug_trace_captures_slanext_stages_without_changing_selection(self) -> None:
        image = np.zeros((120, 260, 3), dtype=np.uint8)
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=FakeTableRecognitionPipelineV2)

        with patch.dict(os.environ, {"TABLE_DEBUG_TRACE": "1", "TABLE_DEBUG_TRACE_DIR": ""}, clear=False), patch.dict(
            sys.modules,
            {"paddleocr": fake_paddleocr},
        ), patch("app.table_recognition_v2_adapter.cv2.imwrite", return_value=True), patch(
            "app.table_recognition_v2_adapter.Path.unlink"
        ):
            result = recognize_table_v2_local(image)

        self.assertEqual(result["table_selected_method"], "slanext")
        trace = result["table_debug"]["table_recognition_trace"]
        self.assertEqual(trace["input"]["image_size"], {"width": 260, "height": 120})
        self.assertEqual(len(trace["input"]["sha256"]), 64)
        self.assertEqual(trace["paddle_raw"]["model_fields"]["table_type"], "not_available")
        self.assertEqual(trace["parsed"]["table_rows"], [["A", "B"]])
        self.assertEqual(trace["postprocessed"]["table_rows"], [["A", "B"]])
        self.assertFalse(trace["ocr_assignment"]["changed"])
        self.assertEqual(trace["final"]["table_selected_method"], "slanext")

    def test_no_rows_quality_score_is_zero(self) -> None:
        quality = _calculate_table_quality([], None, "slanext")

        self.assertEqual(quality["score"], 0.0)
        self.assertFalse(quality["usable_shape"])
        self.assertIn("no_rows", quality["penalties"])

    def test_missing_ocr_confidence_is_not_assumed_perfect(self) -> None:
        candidate = _build_table_candidate({"table_rows": [["A", "B"], ["C", "D"]]}, "slanext")

        self.assertFalse(candidate["table_debug"]["ocr_confidence"]["available"])
        self.assertLess(candidate["confidence"], 1.0)

    def test_confidence_0_to_100_is_normalized(self) -> None:
        ocr_confidence = _calculate_ocr_confidence(
            {
                "segments": [
                    {"text": "A", "confidence": 95},
                    {"text": "B", "confidence": 80},
                    {"text": "", "confidence": 10},
                ]
            }
        )

        self.assertTrue(ocr_confidence["available"])
        self.assertEqual(ocr_confidence["recognized_count"], 2)
        self.assertAlmostEqual(ocr_confidence["average"], 0.875)

    def test_merged_cells_are_not_over_penalized(self) -> None:
        rows = [["Header", ""], ["A", "B"], ["C", "D"]]
        structured = {
            "rows": rows,
            "cells": [
                {"row": 0, "col": 0, "text": "Header", "rowSpan": 1, "colSpan": 2},
                {"row": 0, "col": 1, "text": "", "hidden": True},
                {"row": 1, "col": 0, "text": "A"},
                {"row": 1, "col": 1, "text": "B"},
                {"row": 2, "col": 0, "text": "C"},
                {"row": 2, "col": 1, "text": "D"},
            ],
        }

        quality = _calculate_table_quality(rows, structured, "slanext")

        self.assertGreater(quality["score"], 0.65)
        self.assertGreater(quality["merged_cell_ratio"], 0.0)

    def test_grid_analyzer_splits_multiple_topologies_without_text_keywords(self) -> None:
        image = np.full((240, 240, 3), 255, dtype=np.uint8)
        for y in [10, 70, 130, 190, 230]:
            image[y : y + 2, 10:230] = 0
        for x in [10, 80, 150, 230]:
            image[10:130, x : x + 2] = 0
        for x in [10, 120, 230]:
            image[130:230, x : x + 2] = 0

        analysis = analyze_table_regions(image)

        self.assertTrue(analysis["detected"])
        self.assertGreaterEqual(len(analysis["regions"]), 2)
        self.assertGreaterEqual(analysis["confidence"], 0.72)
        self.assertTrue(all({"bbox", "confidence", "type"}.issubset(region) for region in analysis["regions"]))

    def test_normal_table_does_not_split(self) -> None:
        image = np.full((220, 220, 3), 255, dtype=np.uint8)
        for y in [10, 70, 130, 190]:
            image[y : y + 2, 10:210] = 0
        for x in [10, 80, 150, 210]:
            image[10:190, x : x + 2] = 0

        analysis = analyze_table_regions(image)

        self.assertFalse(analysis["detected"])

    def test_complete_vertical_grid_does_not_enter_semi_table(self) -> None:
        image = np.full((260, 260, 3), 255, dtype=np.uint8)
        for y in [10, 70, 130, 190, 250]:
            image[y : y + 2, 10:250] = 0
        for x in [10, 90, 170, 250]:
            image[10:250, x : x + 2] = 0

        analysis = analyze_table_regions(image)

        self.assertFalse(analysis["detected"])
        self.assertEqual(analysis["reason"], "full_vertical_grid")

    def _run_coordinate_semi_case(self, detected_regions, recognitions, image=None):
        test_image = image if image is not None else np.full((160, 220, 3), 255, dtype=np.uint8)
        fake_analysis = {
            "detected": True,
            "confidence": 0.91,
            "topology_change_ratio": 0.5,
            "regions": [
                {"type": "grid", "bbox": {"x": 0, "y": 0, "width": test_image.shape[1], "height": test_image.shape[0] // 2}},
                {"type": "grid", "bbox": {"x": 0, "y": test_image.shape[0] // 2, "width": test_image.shape[1], "height": test_image.shape[0] // 2}},
            ],
        }

        class ForbiddenModel:
            def predict(self, **kwargs):
                raise AssertionError("Semi table path must not call SLANeXt.")

        with patch("app.table_recognition_v2_adapter.analyze_table_regions", return_value=fake_analysis), patch(
            "app.table_recognition_v2_adapter.detect_text_boxes",
            return_value={"regions": detected_regions},
        ), patch(
            "app.table_recognition_v2_adapter.run_paddle_thai_ocr_batch",
            return_value=recognitions,
        ), patch("app.table_recognition_v2_adapter.cv2.imwrite", return_value=True), patch("app.table_recognition_v2_adapter.Path.unlink"):
            return _try_semi_structured_table(test_image, ForbiddenModel(), 0.0)

    def test_semi_coordinate_reconstructs_with_broken_lines(self) -> None:
        image = np.full((120, 160, 3), 255, dtype=np.uint8)
        image[10:12, 5:155] = 0
        image[60:62, 5:70] = 0
        image[60:62, 90:155] = 0
        image[110:112, 5:155] = 0
        image[10:112, 5:7] = 0
        image[10:112, 75:77] = 0
        image[10:112, 155:157] = 0
        regions = [
            {"bbox": {"x": 12, "y": 25, "width": 30, "height": 10}},
            {"bbox": {"x": 95, "y": 25, "width": 30, "height": 10}},
            {"bbox": {"x": 12, "y": 78, "width": 30, "height": 10}},
            {"bbox": {"x": 95, "y": 78, "width": 30, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["A", "B", "C", "D"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_selected_method"], "coordinate_based_semi")
        self.assertEqual(result["table_rows"][0][:2], ["A", "B"])
        self.assertEqual(result["table_rows"][1][:2], ["C", "D"])

    def test_semi_grid_normalizer_draws_missing_lines_then_calls_slanext(self) -> None:
        image = np.full((120, 180, 3), 255, dtype=np.uint8)
        image[10:12, 5:175] = 0
        image[110:112, 5:175] = 0
        image[10:112, 5:7] = 0
        image[10:112, 175:177] = 0
        detected_regions = [
            {"bbox": {"x": 18, "y": 25, "width": 32, "height": 10}},
            {"bbox": {"x": 112, "y": 25, "width": 30, "height": 10}},
            {"bbox": {"x": 18, "y": 78, "width": 32, "height": 10}},
            {"bbox": {"x": 112, "y": 78, "width": 30, "height": 10}},
        ]
        recognitions = [{"text": value, "confidence": 0.9} for value in ["A", "B", "C", "D"]]
        fake_analysis = {
            "detected": True,
            "confidence": 0.91,
            "topology_change_ratio": 0.5,
            "regions": [
                {"type": "grid", "bbox": {"x": 0, "y": 0, "width": 180, "height": 60}},
                {"type": "grid", "bbox": {"x": 0, "y": 60, "width": 180, "height": 60}},
            ],
        }

        class SyntheticGridModel:
            calls = 0

            def predict(self, **kwargs):
                SyntheticGridModel.calls += 1
                input_path = kwargs.get("input")
                if not isinstance(input_path, str):
                    raise AssertionError("Synthetic grid should be passed to SLANeXt as an image path.")
                return [{"html": "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"}]

        with patch("app.table_recognition_v2_adapter.analyze_table_regions", return_value=fake_analysis), patch(
            "app.table_recognition_v2_adapter.detect_text_boxes",
            return_value={"regions": detected_regions},
        ), patch(
            "app.table_recognition_v2_adapter.run_paddle_thai_ocr_batch",
            return_value=recognitions,
        ), patch("app.table_recognition_v2_adapter._recognize_coordinate_based_semi_table") as coordinate:
            result = _try_semi_structured_table(image, SyntheticGridModel(), 0.0)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(SyntheticGridModel.calls, 1)
        coordinate.assert_not_called()
        self.assertEqual(result["table_selected_method"], "grid_normalized_slanext")
        self.assertEqual(result["table_debug"]["status"], "grid_normalized_slanext")
        self.assertEqual(result["table_semi_analysis"]["merge_status"], "grid_normalized_slanext")
        self.assertTrue(result["table_semi_analysis"]["grid_normalizer"]["synthetic_grid"])
        self.assertGreater(
            result["table_semi_analysis"]["grid_normalizer"]["drawn_lines"]["horizontal"]
            + result["table_semi_analysis"]["grid_normalizer"]["drawn_lines"]["vertical"],
            0,
        )

    def test_forced_empty_slanext_can_use_grid_normalizer_before_coordinate_fallback(self) -> None:
        image = np.full((120, 180, 3), 255, dtype=np.uint8)
        image[10:12, 5:175] = 0
        image[110:112, 5:175] = 0
        image[10:112, 5:7] = 0
        image[10:112, 175:177] = 0
        detected_regions = [
            {"bbox": {"x": 18, "y": 25, "width": 32, "height": 10}},
            {"bbox": {"x": 112, "y": 25, "width": 30, "height": 10}},
            {"bbox": {"x": 18, "y": 78, "width": 32, "height": 10}},
            {"bbox": {"x": 112, "y": 78, "width": 30, "height": 10}},
        ]
        recognitions = [{"text": value, "confidence": 0.9} for value in ["A", "B", "C", "D"]]

        class SyntheticGridModel:
            def predict(self, **kwargs):
                return [{"html": "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"}]

        with patch("app.table_recognition_v2_adapter._load_table_model", return_value=SyntheticGridModel()), patch(
            "app.table_recognition_v2_adapter.detect_text_boxes",
            return_value={"regions": detected_regions},
        ), patch(
            "app.table_recognition_v2_adapter.run_paddle_thai_ocr_batch",
            return_value=recognitions,
        ), patch("app.table_recognition_v2_adapter._recognize_coordinate_based_semi_table") as coordinate:
            result = _try_forced_semi_after_empty_slanext(image, None)

        self.assertIsNotNone(result)
        assert result is not None
        coordinate.assert_not_called()
        self.assertEqual(result["table_debug"]["status"], "grid_normalized_slanext")
        self.assertEqual(result["table_semi_analysis"]["merge_status"], "grid_normalized_slanext")

    def test_semi_coordinate_handles_missing_vertical_line_segment(self) -> None:
        regions = [
            {"bbox": {"x": 12, "y": 20, "width": 30, "height": 10}},
            {"bbox": {"x": 100, "y": 20, "width": 30, "height": 10}},
            {"bbox": {"x": 12, "y": 70, "width": 30, "height": 10}},
            {"bbox": {"x": 100, "y": 70, "width": 30, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.88} for value in ["L1", "R1", "L2", "R2"]])

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][1][:2], ["L2", "R2"])

    def test_semi_coordinate_reconstructs_merged_header_colspan(self) -> None:
        image = np.full((100, 160, 3), 255, dtype=np.uint8)
        image[10:12, 5:155] = 0
        image[45:47, 5:155] = 0
        image[90:92, 5:155] = 0
        image[10:92, 5:7] = 0
        image[45:92, 75:77] = 0
        image[10:92, 155:157] = 0
        regions = [
            {"bbox": {"x": 45, "y": 24, "width": 70, "height": 12}},
            {"bbox": {"x": 12, "y": 62, "width": 30, "height": 10}},
            {"bbox": {"x": 100, "y": 62, "width": 30, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["Header", "A", "B"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        header = next(cell for cell in result["table_structured"]["cells"] if cell["text"] == "Header")
        self.assertEqual(header["colSpan"], 2)

    def test_semi_coordinate_reconstructs_merged_cell_rowspan(self) -> None:
        image = np.full((130, 160, 3), 255, dtype=np.uint8)
        for y in [10, 50, 90, 120]:
            image[y:y + 2, 5:155] = 0
        image[10:120, 5:7] = 0
        image[10:120, 75:77] = 0
        image[10:120, 155:157] = 0
        image[50:90, 5:75] = 255
        regions = [
            {"bbox": {"x": 12, "y": 30, "width": 35, "height": 45}},
            {"bbox": {"x": 100, "y": 30, "width": 30, "height": 10}},
            {"bbox": {"x": 100, "y": 70, "width": 30, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["Tall", "B1", "B2"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        tall = next(cell for cell in result["table_structured"]["cells"] if cell["text"] == "Tall")
        self.assertGreaterEqual(tall["rowSpan"], 2)

    def test_semi_coordinate_infers_boundaries_from_text_alignment_and_empty_cell(self) -> None:
        regions = [
            {"bbox": {"x": 12, "y": 20, "width": 30, "height": 10}},
            {"bbox": {"x": 100, "y": 20, "width": 30, "height": 10}},
            {"bbox": {"x": 12, "y": 70, "width": 30, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["A1", "B1", "A2"]])

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"], [["A1", "B1"], ["A2", ""]])
        self.assertTrue(any(cell.get("text") == "" for cell in result["table_structured"]["cells"]))

    def test_semi_coordinate_splits_multiple_logical_rows_without_horizontal_lines(self) -> None:
        image = np.full((180, 240, 3), 255, dtype=np.uint8)
        image[10:12, 5:235] = 0
        image[165:167, 5:235] = 0
        image[10:167, 5:7] = 0
        image[10:167, 80:82] = 0
        image[10:167, 160:162] = 0
        image[10:167, 235:237] = 0
        regions = []
        recognitions = []
        for index, code in enumerate(["IC-0001", "IC-0003", "IC-0004", "IC-0005"]):
            y = 30 + index * 28
            regions.extend([
                {"bbox": {"x": 12, "y": y, "width": 45, "height": 10}},
                {"bbox": {"x": 92, "y": y, "width": 45, "height": 10}},
                {"bbox": {"x": 178, "y": y, "width": 22, "height": 10}},
            ])
            recognitions.extend([
                {"text": code, "confidence": 0.9},
                {"text": f"Item {index + 1}", "confidence": 0.9},
                {"text": str(index + 1), "confidence": 0.9},
            ])
        result = self._run_coordinate_semi_case(regions, recognitions, image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual([row[0] for row in result["table_rows"][:4]], ["IC-0001", "IC-0003", "IC-0004", "IC-0005"])

    def test_semi_coordinate_splits_summary_rows_without_horizontal_separators(self) -> None:
        image = np.full((140, 220, 3), 255, dtype=np.uint8)
        image[10:12, 5:215] = 0
        image[125:127, 5:215] = 0
        image[10:127, 5:7] = 0
        image[10:127, 130:132] = 0
        image[10:127, 215:217] = 0
        regions = [
            {"bbox": {"x": 20, "y": 35, "width": 40, "height": 10}},
            {"bbox": {"x": 150, "y": 35, "width": 35, "height": 10}},
            {"bbox": {"x": 20, "y": 70, "width": 40, "height": 10}},
            {"bbox": {"x": 150, "y": 70, "width": 35, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["Total", "100", "VAT", "7"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][:2], [["Total", "100"], ["VAT", "7"]])

    def test_semi_coordinate_splits_tight_subrows_by_vertical_overlap(self) -> None:
        image = np.full((90, 180, 3), 255, dtype=np.uint8)
        image[10:12, 5:175] = 0
        image[80:82, 5:175] = 0
        image[10:82, 5:7] = 0
        image[10:82, 90:92] = 0
        image[10:82, 175:177] = 0
        regions = [
            {"bbox": {"x": 16, "y": 25, "width": 32, "height": 4}},
            {"bbox": {"x": 106, "y": 25, "width": 24, "height": 4}},
            {"bbox": {"x": 16, "y": 31, "width": 32, "height": 4}},
            {"bbox": {"x": 106, "y": 31, "width": 24, "height": 4}},
            {"bbox": {"x": 16, "y": 37, "width": 32, "height": 4}},
            {"bbox": {"x": 106, "y": 37, "width": 24, "height": 4}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["A1", "B1", "A2", "B2", "A3", "B3"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][:3], [["A1", "B1"], ["A2", "B2"], ["A3", "B3"]])

    def test_semi_coordinate_infers_subcolumns_from_bbox_edge_gaps(self) -> None:
        image = np.full((140, 240, 3), 255, dtype=np.uint8)
        image[10:12, 5:235] = 0
        image[125:127, 5:235] = 0
        image[10:127, 5:7] = 0
        image[10:127, 235:237] = 0
        regions = [
            {"bbox": {"x": 18, "y": 28, "width": 80, "height": 10}},
            {"bbox": {"x": 130, "y": 28, "width": 34, "height": 10}},
            {"bbox": {"x": 18, "y": 65, "width": 22, "height": 10}},
            {"bbox": {"x": 130, "y": 65, "width": 34, "height": 10}},
            {"bbox": {"x": 18, "y": 96, "width": 72, "height": 10}},
            {"bbox": {"x": 130, "y": 96, "width": 34, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["Long label", "100", "Tax", "7", "Net amount", "107"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][:3], [["Long label", "100"], ["Tax", "7"], ["Net amount", "107"]])

    def test_semi_coordinate_preserves_merged_footer_colspan(self) -> None:
        image = np.full((100, 240, 3), 255, dtype=np.uint8)
        image[10:12, 5:235] = 0
        image[55:57, 5:235] = 0
        image[90:92, 5:235] = 0
        image[10:92, 5:7] = 0
        for x in [40, 75, 110, 145, 180, 210]:
            image[10:55, x:x + 2] = 0
        image[10:92, 235:237] = 0
        regions = [
            {"bbox": {"x": 10, "y": 25, "width": 20, "height": 10}},
            {"bbox": {"x": 45, "y": 25, "width": 20, "height": 10}},
            {"bbox": {"x": 82, "y": 25, "width": 20, "height": 10}},
            {"bbox": {"x": 118, "y": 25, "width": 20, "height": 10}},
            {"bbox": {"x": 152, "y": 25, "width": 20, "height": 10}},
            {"bbox": {"x": 185, "y": 25, "width": 20, "height": 10}},
            {"bbox": {"x": 214, "y": 25, "width": 15, "height": 10}},
            {"bbox": {"x": 45, "y": 70, "width": 145, "height": 10}},
        ]
        recognitions = [{"text": f"H{index}", "confidence": 0.9} for index in range(7)] + [{"text": "Footer total", "confidence": 0.9}]
        result = self._run_coordinate_semi_case(regions, recognitions, image)

        self.assertIsNotNone(result)
        assert result is not None
        footer = next(cell for cell in result["table_structured"]["cells"] if cell["text"] == "Footer total")
        self.assertGreaterEqual(footer["colSpan"], 2)

    def test_semi_coordinate_infers_internal_columns_without_vertical_lines(self) -> None:
        image = np.full((120, 220, 3), 255, dtype=np.uint8)
        image[10:12, 5:215] = 0
        image[110:112, 5:215] = 0
        image[10:112, 5:7] = 0
        image[10:112, 215:217] = 0
        regions = [
            {"bbox": {"x": 20, "y": 25, "width": 35, "height": 10}},
            {"bbox": {"x": 120, "y": 25, "width": 45, "height": 10}},
            {"bbox": {"x": 20, "y": 65, "width": 35, "height": 10}},
            {"bbox": {"x": 120, "y": 65, "width": 45, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["Code", "Name", "IC-1", "Mouse"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][:2], [["Code", "Name"], ["IC-1", "Mouse"]])
        self.assertGreaterEqual(result["table_debug"]["logical_column_boundary_count"], 3)

    def test_semi_coordinate_repeated_label_value_alignment_infers_subcolumn(self) -> None:
        image = np.full((140, 220, 3), 255, dtype=np.uint8)
        image[10:12, 5:215] = 0
        image[125:127, 5:215] = 0
        image[10:127, 5:7] = 0
        image[10:127, 215:217] = 0
        regions = [
            {"bbox": {"x": 18, "y": 25, "width": 40, "height": 10}},
            {"bbox": {"x": 135, "y": 25, "width": 35, "height": 10}},
            {"bbox": {"x": 18, "y": 65, "width": 40, "height": 10}},
            {"bbox": {"x": 135, "y": 65, "width": 35, "height": 10}},
            {"bbox": {"x": 18, "y": 95, "width": 40, "height": 10}},
            {"bbox": {"x": 135, "y": 95, "width": 35, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["Total", "100", "VAT", "7", "Net", "107"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][:3], [["Total", "100"], ["VAT", "7"], ["Net", "107"]])

    def test_semi_coordinate_multiline_cell_does_not_create_fake_column(self) -> None:
        image = np.full((150, 220, 3), 255, dtype=np.uint8)
        image[10:12, 5:215] = 0
        image[135:137, 5:215] = 0
        image[10:137, 5:7] = 0
        image[10:137, 215:217] = 0
        regions = [
            {"bbox": {"x": 25, "y": 35, "width": 120, "height": 10}},
            {"bbox": {"x": 25, "y": 65, "width": 110, "height": 10}},
            {"bbox": {"x": 25, "y": 95, "width": 130, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["Line one", "Line two", "Line three"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(max(len(row) for row in result["table_rows"]), 1)

    def test_semi_coordinate_combines_hard_and_inferred_boundaries(self) -> None:
        image = np.full((130, 260, 3), 255, dtype=np.uint8)
        image[10:12, 5:255] = 0
        image[120:122, 5:255] = 0
        image[10:122, 5:7] = 0
        image[10:122, 130:132] = 0
        image[10:122, 255:257] = 0
        regions = [
            {"bbox": {"x": 18, "y": 30, "width": 35, "height": 10}},
            {"bbox": {"x": 76, "y": 30, "width": 35, "height": 10}},
            {"bbox": {"x": 155, "y": 30, "width": 35, "height": 10}},
            {"bbox": {"x": 212, "y": 30, "width": 35, "height": 10}},
            {"bbox": {"x": 18, "y": 75, "width": 35, "height": 10}},
            {"bbox": {"x": 76, "y": 75, "width": 35, "height": 10}},
            {"bbox": {"x": 155, "y": 75, "width": 35, "height": 10}},
            {"bbox": {"x": 212, "y": 75, "width": 35, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["A", "B", "C", "D", "E", "F", "G", "H"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][:2], [["A", "B", "C", "D"], ["E", "F", "G", "H"]])
        self.assertGreaterEqual(result["table_debug"]["hard_column_boundary_count"], 3)

    def test_semi_coordinate_real_header_can_span_inferred_columns_without_fake_headers(self) -> None:
        image = np.full((120, 220, 3), 255, dtype=np.uint8)
        image[10:12, 5:215] = 0
        image[50:52, 5:215] = 0
        image[110:112, 5:215] = 0
        image[10:112, 5:7] = 0
        image[10:112, 215:217] = 0
        regions = [
            {"bbox": {"x": 35, "y": 25, "width": 120, "height": 10}},
            {"bbox": {"x": 20, "y": 70, "width": 35, "height": 10}},
            {"bbox": {"x": 130, "y": 70, "width": 35, "height": 10}},
            {"bbox": {"x": 20, "y": 92, "width": 35, "height": 10}},
            {"bbox": {"x": 130, "y": 92, "width": 35, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["ข้อมูล", "ชื่อ", "สมชาย", "อายุ", "35"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        flat_text = " ".join(value for row in result["table_rows"] for value in row)
        self.assertNotIn("Header 1", flat_text)
        self.assertNotIn("Column 1", flat_text)
        header = next(cell for cell in result["table_structured"]["cells"] if cell["text"] == "ข้อมูล")
        self.assertGreaterEqual(header["colSpan"], 2)

    def test_semi_coordinate_preserves_label_value_pairing_inside_each_hard_region(self) -> None:
        image = np.full((140, 260, 3), 255, dtype=np.uint8)
        image[10:12, 5:255] = 0
        image[125:127, 5:255] = 0
        image[10:127, 5:7] = 0
        image[10:127, 130:132] = 0
        image[10:127, 255:257] = 0
        regions = [
            {"bbox": {"x": 18, "y": 30, "width": 42, "height": 10}},
            {"bbox": {"x": 78, "y": 30, "width": 35, "height": 10}},
            {"bbox": {"x": 148, "y": 30, "width": 40, "height": 10}},
            {"bbox": {"x": 210, "y": 30, "width": 35, "height": 10}},
            {"bbox": {"x": 18, "y": 75, "width": 42, "height": 10}},
            {"bbox": {"x": 78, "y": 75, "width": 35, "height": 10}},
            {"bbox": {"x": 148, "y": 75, "width": 40, "height": 10}},
            {"bbox": {"x": 210, "y": 75, "width": 35, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(
            regions,
            [{"text": value, "confidence": 0.9} for value in ["LeftA", "1", "RightA", "2", "LeftB", "3", "RightB", "4"]],
            image,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][:2], [["LeftA", "1", "RightA", "2"], ["LeftB", "3", "RightB", "4"]])
        self.assertGreaterEqual(result["table_debug"]["hard_column_boundary_count"], 3)
        self.assertGreaterEqual(result["table_debug"]["logical_column_boundary_count"], 5)
        assignment = result["table_debug"]["assignment"]
        self.assertEqual(assignment["hard_region_violation_count"], 0)
        self.assertEqual(assignment["row_non_empty_counts"][:2], [4, 4])
        self.assertEqual(assignment["column_non_empty_counts"][:4], [2, 2, 2, 2])
        self.assertGreaterEqual(assignment["average_column_overlap"], 0.6)

    def test_semi_coordinate_assigns_boundary_crossing_bbox_by_dominant_overlap(self) -> None:
        image = np.full((100, 180, 3), 255, dtype=np.uint8)
        for y in [10, 50, 90]:
            image[y:y + 2, 5:175] = 0
        for x in [5, 90, 175]:
            image[10:92, x:x + 2] = 0
        regions = [
            {"bbox": {"x": 16, "y": 25, "width": 30, "height": 10}},
            {"bbox": {"x": 96, "y": 25, "width": 35, "height": 10}},
            {"bbox": {"x": 86, "y": 65, "width": 32, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["Left", "Right", "MostlyRight"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][0], ["Left", "Right"])
        self.assertEqual(result["table_rows"][1], ["", "MostlyRight"])

    def test_semi_coordinate_merges_multiple_ocr_boxes_in_same_cell_before_assignment(self) -> None:
        image = np.full((100, 180, 3), 255, dtype=np.uint8)
        for y in [10, 50]:
            image[y:y + 2, 5:175] = 0
        for x in [5, 90, 175]:
            image[10:52, x:x + 2] = 0
        regions = [
            {"bbox": {"x": 15, "y": 25, "width": 25, "height": 10}},
            {"bbox": {"x": 42, "y": 25, "width": 20, "height": 10}},
            {"bbox": {"x": 105, "y": 25, "width": 35, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": value, "confidence": 0.9} for value in ["ชื่อ", "สินค้า", "100"]], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"][:1], [["ชื่อ สินค้า", "100"]])
        self.assertEqual(
            len([cell for cell in result["table_structured"]["cells"] if cell.get("text") == "ชื่อ สินค้า"]),
            1,
        )

    def test_semi_coordinate_does_not_split_when_internal_evidence_is_insufficient(self) -> None:
        image = np.full((90, 220, 3), 255, dtype=np.uint8)
        image[10:12, 5:215] = 0
        image[80:82, 5:215] = 0
        image[10:82, 5:7] = 0
        image[10:82, 215:217] = 0
        regions = [
            {"bbox": {"x": 25, "y": 35, "width": 35, "height": 10}},
            {"bbox": {"x": 140, "y": 35, "width": 35, "height": 10}},
        ]
        result = self._run_coordinate_semi_case(regions, [{"text": "Only", "confidence": 0.9}, {"text": "Once", "confidence": 0.9}], image)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(max(len(row) for row in result["table_rows"]), 1)
        self.assertIn("Only Once", result["table_rows"][0][0])

    def test_semi_coordinate_uses_per_cell_ocr_when_text_detection_misses_grid_text(self) -> None:
        image = np.full((90, 180, 3), 255, dtype=np.uint8)
        for y in [10, 45, 80]:
            image[y:y + 2, 5:175] = 0
        for x in [5, 60, 120, 175]:
            image[10:82, x:x + 2] = 0
        fake_analysis = {
            "detected": True,
            "confidence": 0.91,
            "topology_change_ratio": 0.5,
            "regions": [
                {"type": "grid", "bbox": {"x": 0, "y": 0, "width": 180, "height": 45}},
                {"type": "grid", "bbox": {"x": 0, "y": 45, "width": 180, "height": 45}},
            ],
        }

        with patch("app.table_recognition_v2_adapter.analyze_table_regions", return_value=fake_analysis), patch(
            "app.table_recognition_v2_adapter.detect_text_boxes",
            return_value={"regions": []},
        ), patch(
            "app.table_recognition_v2_adapter.run_paddle_thai_ocr_batch",
            return_value=[
                {"text": "A1", "confidence": 0.91},
                {"text": "B1", "confidence": 0.92},
                {"text": "C1", "confidence": 0.93},
                {"text": "A2", "confidence": 0.94},
                {"text": "B2", "confidence": 0.95},
                {"text": "C2", "confidence": 0.96},
            ],
        ):
            result = _try_semi_structured_table(image, None, 0.0)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_rows"], [["A1", "B1", "C1"], ["A2", "B2", "C2"]])
        self.assertEqual(result["table_debug"]["per_cell_ocr"]["filled_cells"], 6)
        self.assertGreaterEqual(result["table_debug"]["hard_column_boundary_count"], 4)

    def test_semi_coordinate_does_not_call_slanext_model(self) -> None:
        regions = [{"bbox": {"x": 12, "y": 20, "width": 30, "height": 10}}, {"bbox": {"x": 100, "y": 20, "width": 30, "height": 10}}]
        result = self._run_coordinate_semi_case(regions, [{"text": "A", "confidence": 0.9}, {"text": "B", "confidence": 0.9}])

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["table_debug"]["model_reuse"]["model_inference_count"], 0)

    def test_recognize_table_v2_local_uses_slanext_first_and_skips_semi_when_confident(self) -> None:
        image = np.full((120, 160, 3), 255, dtype=np.uint8)
        slanext_output = [{"html": "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>"}]

        with patch("app.table_recognition_v2_adapter._load_table_model", return_value=object()), patch(
            "app.table_recognition_v2_adapter._predict_table_model",
            return_value=slanext_output,
        ), patch(
            "app.table_recognition_v2_adapter.analyze_table_regions",
            side_effect=AssertionError("Semi analyzer should not run when SLANeXt is confident."),
        ), patch("app.table_recognition_v2_adapter._try_semi_structured_table") as semi, patch(
            "app.table_recognition_v2_adapter.cv2.imwrite",
            return_value=True,
        ), patch("app.table_recognition_v2_adapter.Path.unlink"):
            result = recognize_table_v2_local(image)

        semi.assert_not_called()
        self.assertEqual(result["table_selected_method"], "slanext")
        self.assertEqual(result["table_debug"]["semi_skipped_reason"], "slanext_passed_quality_gate")

    def test_recognize_table_v2_local_rejects_unreliable_semi_and_uses_slanext(self) -> None:
        image = np.full((120, 160, 3), 255, dtype=np.uint8)
        fake_analysis = {
            "detected": True,
            "confidence": 0.91,
            "topology_change_ratio": 0.5,
            "regions": [
                {"type": "grid", "bbox": {"x": 0, "y": 0, "width": 160, "height": 60}},
                {"type": "grid", "bbox": {"x": 0, "y": 60, "width": 160, "height": 60}},
            ],
            "line_summary": {"horizontal": 4, "vertical": 4},
        }
        weak_semi = {
            "text": "| A B C |\n| --- |",
            "confidence": 0.2,
            "segments": [{"text": "A B C", "confidence": 0.8, "bbox": {"x": 10, "y": 10, "width": 120, "height": 10}}],
            "table_rows": [["A B C"]],
            "table_structured": {"rows": [["A B C"]], "cells": [{"row": 0, "col": 0, "text": "A B C"}]},
            "table_debug": {
                "status": "coordinate_based_semi_reconstructed",
                "hard_column_boundary_count": 4,
                "logical_column_boundary_count": 2,
                "ocr": {"detected_boxes": 3, "recognized_cells": 1},
            },
            "table_selected_method": "coordinate_based_semi",
            "table_semi_analysis": fake_analysis,
        }
        slanext_result = {
            "text": "| A | B |\n| --- | --- |",
            "confidence": 0.9,
            "segments": [],
            "table_rows": [["A", "B"]],
            "table_structured": {
                "rows": [["A", "B"]],
                "cells": [
                    {"row": 0, "col": 0, "text": "A"},
                    {"row": 0, "col": 1, "text": "B"},
                ],
            },
            "table_debug": {"status": "slanext"},
        }

        with patch("app.table_recognition_v2_adapter.analyze_table_regions", return_value=fake_analysis), patch(
            "app.table_recognition_v2_adapter._recognize_coordinate_based_semi_table",
            return_value=weak_semi,
        ), patch("app.table_recognition_v2_adapter._load_table_model", return_value=object()), patch(
            "app.table_recognition_v2_adapter._predict_table_model",
            return_value=object(),
        ), patch(
            "app.table_recognition_v2_adapter._slanext_result_from_output",
            return_value=slanext_result,
        ):
            result = recognize_table_v2_local(image)

        self.assertEqual(result["table_selected_method"], "slanext")
        self.assertEqual(result["table_semi_analysis"]["merge_status"], "coordinate_reconstruction_rejected")
        self.assertIn("logical_grid_lost_hard_boundaries", result["table_semi_analysis"]["reliability"]["reasons"])

    def test_column_anchor_assignment_prevents_values_shifting_to_neighbor_columns(self) -> None:
        cells = []
        anchors_x = [10, 50, 90, 130, 170, 210, 250]
        for col, x in enumerate(anchors_x):
            cells.append({"row": 0, "col": col, "text": f"H{col}", "bbox": {"x": x - 6, "y": 0, "width": 12, "height": 10}, "rowSpan": 1, "colSpan": 1})
        # Deliberately wrong SLANeXt col values. Geometry should put all item ids back in column 0.
        for row, item_id in enumerate(["IC-0003", "IC-0004", "IC-0005"], start=1):
            cells.append({"row": row, "col": 1, "text": item_id, "bbox": {"x": 4, "y": row * 20, "width": 24, "height": 10}, "rowSpan": 1, "colSpan": 1})
            cells.append({"row": row, "col": 3, "text": str(row * 100), "bbox": {"x": 84, "y": row * 20, "width": 18, "height": 10}, "rowSpan": 1, "colSpan": 1})
        candidate = {
            "confidence": 0.9,
            "table_rows": [[""] * 7 for _ in range(4)],
            "table_structured": {"rows": [[""] * 7 for _ in range(4)], "cells": cells, "headerRowCount": 1},
        }

        section = _section_from_region_candidate(candidate, {"bbox": {"x": 0, "y": 0, "width": 280, "height": 100}}, "main")

        self.assertEqual(section["rows"][1][0], "IC-0003")
        self.assertEqual(section["rows"][2][0], "IC-0004")
        self.assertEqual(section["rows"][3][0], "IC-0005")
        self.assertEqual(section["rows"][1][2], "100")
        item_cells = [cell for cell in section["cells"] if cell["text"].startswith("IC-")]
        self.assertTrue(all(cell["col"] == 0 for cell in item_cells))

    def test_summary_table_uses_local_column_anchors(self) -> None:
        cells = [
            {"row": 0, "col": 0, "text": "Total", "bbox": {"x": 10, "y": 0, "width": 40, "height": 10}, "rowSpan": 1, "colSpan": 1},
            {"row": 0, "col": 1, "text": "300", "bbox": {"x": 150, "y": 0, "width": 30, "height": 10}, "rowSpan": 1, "colSpan": 1},
            {"row": 1, "col": 0, "text": "VAT", "bbox": {"x": 12, "y": 20, "width": 28, "height": 10}, "rowSpan": 1, "colSpan": 1},
            {"row": 1, "col": 1, "text": "21", "bbox": {"x": 154, "y": 20, "width": 20, "height": 10}, "rowSpan": 1, "colSpan": 1},
        ]
        candidate = {"confidence": 0.8, "table_rows": [["Total", "300"], ["VAT", "21"]], "table_structured": {"rows": [["Total", "300"], ["VAT", "21"]], "cells": cells}}

        section = _section_from_region_candidate(candidate, {"bbox": {"x": 0, "y": 120, "width": 220, "height": 60}}, "summary")

        self.assertEqual(len(section["columns"]), 2)
        self.assertEqual(section["rows"], [["Total", "300"], ["VAT", "21"]])
        self.assertTrue(all(cell["colSpan"] == 1 for cell in section["cells"]))

    def test_column_anchor_reconstruction_preserves_header_merge(self) -> None:
        cells = [
            {"row": 0, "col": 0, "text": "Asset", "bbox": {"x": 0, "y": 0, "width": 90, "height": 10}, "rowSpan": 1, "colSpan": 2},
            {"row": 1, "col": 0, "text": "Code", "bbox": {"x": 0, "y": 20, "width": 30, "height": 10}, "rowSpan": 1, "colSpan": 1},
            {"row": 1, "col": 1, "text": "Name", "bbox": {"x": 50, "y": 20, "width": 30, "height": 10}, "rowSpan": 1, "colSpan": 1},
        ]
        candidate = {"confidence": 0.8, "table_rows": [["Asset", ""], ["Code", "Name"]], "table_structured": {"rows": [["Asset", ""], ["Code", "Name"]], "cells": cells}}

        section = _section_from_region_candidate(candidate, {"bbox": {"x": 0, "y": 0, "width": 120, "height": 50}}, "main")
        header = next(cell for cell in section["cells"] if cell["text"] == "Asset")

        self.assertEqual(header["col"], 0)
        self.assertEqual(header["colSpan"], 2)

    def test_slanext_ocr_assignment_uses_single_owner_by_overlap_and_preserves_span(self) -> None:
        structured = {
            "rows": [["", ""], ["", ""]],
            "headerRowCount": 1,
            "cells": [
                {"row": 0, "col": 0, "text": "Header", "bbox": {"x": 0, "y": 0, "width": 100, "height": 20}, "rowSpan": 1, "colSpan": 2},
                {"row": 0, "col": 1, "text": "", "rowSpan": 1, "colSpan": 1, "hidden": True},
                {"row": 1, "col": 0, "text": "A", "bbox": {"x": 2, "y": 24, "width": 46, "height": 18}, "rowSpan": 1, "colSpan": 1},
                {"row": 1, "col": 1, "text": "", "bbox": {"x": 52, "y": 24, "width": 46, "height": 18}, "rowSpan": 1, "colSpan": 1},
                {"row": 1, "col": 0, "text": "B", "bbox": {"x": 56, "y": 26, "width": 20, "height": 10}, "rowSpan": 1, "colSpan": 1},
            ],
        }
        candidate = {"table_structured": structured, "table_rows": structured["rows"], "table_debug": {"status": "slanext"}}

        reassigned, debug = _reassign_ocr_text_to_slanext_cells(candidate)

        self.assertTrue(debug["selected"])
        self.assertEqual(reassigned["table_rows"][1][0], "A")
        self.assertEqual(reassigned["table_rows"][1][1], "B")
        header = next(cell for cell in reassigned["table_structured"]["cells"] if cell.get("text") == "Header")
        self.assertEqual(header["colSpan"], 2)
        hidden = [cell for cell in reassigned["table_structured"]["cells"] if cell.get("hidden")]
        self.assertEqual(len(hidden), 1)

    def test_assignment_quality_gate_fails_cross_boundary_assignment(self) -> None:
        structured = {
            "rows": [["A", ""]],
            "cells": [
                {"row": 0, "col": 0, "text": "A", "bbox": {"x": 45, "y": 0, "width": 30, "height": 12}, "rowSpan": 1, "colSpan": 1},
                {"row": 0, "col": 1, "text": "", "bbox": {"x": 50, "y": 0, "width": 50, "height": 20}, "rowSpan": 1, "colSpan": 1},
            ],
        }

        quality = _structured_assignment_quality(structured)

        self.assertFalse(quality["passed"])
        self.assertGreater(quality["cross_boundary_ratio"], 0)

    def test_postprocess_preserves_empty_cells_and_merge_grid(self) -> None:
        result = {
            "text": "",
            "table_structured": {
                "cells": [
                    {"row": 0, "col": 0, "text": "Header", "rowSpan": 1, "colSpan": 3},
                    {"row": 0, "col": 1, "text": "", "rowSpan": 1, "colSpan": 1, "hidden": True},
                    {"row": 0, "col": 2, "text": "", "rowSpan": 1, "colSpan": 1, "hidden": True},
                    {"row": 1, "col": 0, "text": "A", "rowSpan": 1, "colSpan": 1},
                    {"row": 1, "col": 1, "text": "", "rowSpan": 1, "colSpan": 1},
                    {"row": 1, "col": 2, "text": "C", "rowSpan": 1, "colSpan": 1},
                    {"row": 2, "col": 0, "text": "", "rowSpan": 1, "colSpan": 1},
                    {"row": 2, "col": 1, "text": "", "rowSpan": 1, "colSpan": 1},
                    {"row": 2, "col": 2, "text": "", "rowSpan": 1, "colSpan": 1},
                ],
                "headerRowCount": 1,
            },
        }

        processed = _postprocess_table_result(result)

        self.assertEqual(processed["table_rows"], [["Header", "", ""], ["A", "", "C"], ["", "", ""]])
        self.assertEqual(len(processed["table_structured"]["cells"]), 9)
        self.assertEqual(processed["table_structured"]["cells"][0]["colSpan"], 3)

    def test_postprocess_prefers_structured_cell_grid_over_short_rows(self) -> None:
        cells = [
            {"row": row, "col": col, "text": "A" if row == 0 and col == 0 else "", "rowSpan": 1, "colSpan": 1}
            for row in range(10)
            for col in range(2)
        ]
        result = {
            "text": "",
            "table_rows": [["A", ""]],
            "table_structured": {
                "rows": [["A", ""]],
                "cells": cells,
                "headerRowCount": 1,
            },
        }

        processed = _postprocess_table_result(result)

        self.assertEqual(len(processed["table_rows"]), 10)
        self.assertEqual(len(processed["table_rows"][0]), 2)
        self.assertEqual(processed["table_rows"][9], ["", ""])
        self.assertEqual(len(processed["table_structured"]["rows"]), 10)

    def test_slanext_structured_grid_is_usable_even_when_most_rows_are_empty(self) -> None:
        cells = [
            {"row": row, "col": col, "text": "A" if row == 0 and col == 0 else "", "rowSpan": 1, "colSpan": 1}
            for row in range(10)
            for col in range(2)
        ]
        candidate = _build_table_candidate(
            {
                "text": "",
                "table_rows": [["A", ""], *[["", ""] for _ in range(9)]],
                "table_structured": {"rows": [["A", ""], *[["", ""] for _ in range(9)]], "cells": cells},
            },
            "slanext",
        )
        quality = candidate["table_debug"]["quality"]

        self.assertEqual(quality["row_count"], 10)
        self.assertEqual(quality["column_count"], 2)
        self.assertTrue(quality["has_structured_cells"])
        self.assertEqual(len(candidate["table_rows"]), 10)

    def test_semi_structured_all_regions_fail_returns_none_for_whole_roi_fallback(self) -> None:
        image = np.zeros((120, 120, 3), dtype=np.uint8)
        fake_analysis = {
            "detected": True,
            "confidence": 0.91,
            "regions": [{"type": "grid", "bbox": {"x": 0, "y": 0, "width": 120, "height": 60}}],
        }

        with patch("app.table_recognition_v2_adapter.analyze_table_regions", return_value=fake_analysis):
            result = _try_semi_structured_table(image, object(), 0.0)

        self.assertIsNone(result)

    def test_semi_structured_low_topology_change_is_skipped(self) -> None:
        image = np.zeros((120, 180, 3), dtype=np.uint8)
        fake_analysis = {
            "detected": True,
            "confidence": 0.91,
            "topology_change_ratio": 0.12,
            "regions": [
                {"type": "grid", "bbox": {"x": 0, "y": 0, "width": 180, "height": 60}},
                {"type": "grid", "bbox": {"x": 0, "y": 60, "width": 180, "height": 60}},
            ],
            "reason": "weak_topology_change",
        }

        with patch("app.table_recognition_v2_adapter.analyze_table_regions", return_value=fake_analysis), patch(
            "app.table_recognition_v2_adapter._recognize_coordinate_based_semi_table"
        ) as coordinate:
            result = _try_semi_structured_table(image, object(), 0.0)

        coordinate.assert_not_called()
        self.assertIsNone(result)

    def test_grid_analyzer_error_falls_back_to_whole_roi(self) -> None:
        image = np.zeros((120, 260, 3), dtype=np.uint8)
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=FakeTableRecognitionPipelineV2)

        with patch.dict(sys.modules, {"paddleocr": fake_paddleocr}), patch(
            "app.table_recognition_v2_adapter.analyze_table_regions",
            side_effect=RuntimeError("grid analyzer boom"),
        ), patch("app.table_recognition_v2_adapter.cv2.imwrite", return_value=True), patch(
            "app.table_recognition_v2_adapter.Path.unlink"
        ):
            result = recognize_table_v2_local(image)

        self.assertEqual(result["table_rows"], [["A", "B"]])
        self.assertEqual(result["table_semi_analysis"]["merge_status"], "whole_roi_fallback")
        self.assertFalse(result["table_semi_analysis"]["detected"])

    def test_forced_semi_runs_when_slanext_has_no_usable_table(self) -> None:
        image = np.zeros((120, 260, 3), dtype=np.uint8)
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=EmptyTableRecognitionPipelineV2)
        forced_result = {
            "text": "| A | B |\n| --- | --- |\n| 1 | 2 |",
            "confidence": 0.86,
            "segments": [{"text": "A", "confidence": 0.9, "bbox": {"x": 10, "y": 10, "width": 20, "height": 10}}],
            "attempts": [{"step": "coordinate_based_semi_reconstruction"}],
            "preprocessing": "coordinate_based_semi_reconstruction",
            "engine": "table_recognition_v2",
            "model": "coordinate_based_semi",
            "table_rows": [["A", "B"], ["1", "2"]],
            "table_structured": {
                "rows": [["A", "B"], ["1", "2"]],
                "cells": [
                    {"row": 0, "col": 0, "text": "A", "confidence": 0.9},
                    {"row": 0, "col": 1, "text": "B", "confidence": 0.9},
                    {"row": 1, "col": 0, "text": "1", "confidence": 0.9},
                    {"row": 1, "col": 1, "text": "2", "confidence": 0.9},
                ],
            },
            "table_debug": {
                "status": "coordinate_based_semi_reconstructed",
                "ocr": {"detected_boxes": 4, "recognized_cells": 4},
                "hard_column_boundary_count": 2,
                "logical_column_boundary_count": 3,
                "assignment": {
                    "average_row_overlap": 1.0,
                    "average_column_overlap": 1.0,
                    "empty_column_ratio": 0.0,
                    "hard_region_violation_count": 0,
                },
            },
            "table_semi_analysis": {"detected": True, "confidence": 0.72, "regions": [], "forced": True},
        }

        with patch.dict(sys.modules, {"paddleocr": fake_paddleocr}), patch(
            "app.table_recognition_v2_adapter.analyze_table_regions",
            return_value={"detected": False, "confidence": 0.0, "regions": [], "reason": "normal"},
        ), patch("app.table_recognition_v2_adapter.cv2.imwrite", return_value=True), patch(
            "app.table_recognition_v2_adapter.Path.unlink"
        ), patch("app.table_recognition_v2_adapter._recognize_coordinate_based_semi_table", return_value=forced_result), patch(
            "app.table_recognition_v2_adapter._recognize_ocr_table_fallback"
        ) as fallback:
            result = recognize_table_v2_local(image)

        fallback.assert_not_called()
        self.assertEqual(result["table_selected_method"], "coordinate_based_semi_forced")
        self.assertEqual(result["table_debug"]["status"], "coordinate_based_semi_forced")
        self.assertTrue(result["table_debug"]["forced_after_empty_slanext"])
        self.assertEqual(result["table_rows"], [["A", "B"], ["1", "2"]])

    def test_ocr_table_fallback_is_selected_when_slanext_has_no_usable_table(self) -> None:
        image = np.zeros((120, 260, 3), dtype=np.uint8)
        fake_paddleocr = types.SimpleNamespace(TableRecognitionPipelineV2=EmptyTableRecognitionPipelineV2)
        fallback_result = {
            "text": "| A | B |\n| --- | --- |\n| 1 | 2 |",
            "confidence": 0.82,
            "segments": [],
            "attempts": [{"step": "ocr_table_fallback"}],
            "preprocessing": "ocr_table_fallback_text_detection_clustering",
            "engine": "table_recognition_v2",
            "model": "SLANeXt_wired/SLANeXt_wireless",
            "table_rows": [["A", "B"], ["1", "2"]],
            "table_structured": {
                "rows": [["A", "B"], ["1", "2"]],
                "cells": [
                    {"row": 0, "col": 0, "text": "A", "rowSpan": 1, "colSpan": 1},
                    {"row": 0, "col": 1, "text": "B", "rowSpan": 1, "colSpan": 1},
                    {"row": 1, "col": 0, "text": "1", "rowSpan": 1, "colSpan": 1},
                    {"row": 1, "col": 1, "text": "2", "rowSpan": 1, "colSpan": 1},
                ],
                "headerRowCount": 1,
            },
            "table_debug": {"status": "ocr_table_fallback"},
        }

        with patch.dict(sys.modules, {"paddleocr": fake_paddleocr}), patch(
            "app.table_recognition_v2_adapter.analyze_table_regions",
            return_value={"detected": False, "confidence": 0.0, "regions": [], "reason": "normal"},
        ), patch("app.table_recognition_v2_adapter.cv2.imwrite", return_value=True), patch(
            "app.table_recognition_v2_adapter.Path.unlink"
        ), patch("app.table_recognition_v2_adapter._recognize_ocr_table_fallback", return_value=fallback_result):
            result = recognize_table_v2_local(image)

        self.assertEqual(result["table_selected_method"], "ocr_table_fallback")
        self.assertEqual(result["table_debug"]["status"], "ocr_table_fallback")
        self.assertEqual(result["table_rows"], [["A", "B"], ["1", "2"]])
        self.assertNotEqual(result["table_rows"], [["Column 1"], [""]])

    def test_cell_assignment_dedup_prefers_per_cell_ocr_for_same_text_inside_cell(self) -> None:
        cells = [
            {
                "row": 0,
                "col": 0,
                "text": "100",
                "ocrText": "100",
                "assignment_source": "text_detection",
                "bbox": {"x": 54, "y": 14, "width": 18, "height": 8},
                "x": 54,
                "y": 14,
            },
            {
                "row": 0,
                "col": 1,
                "text": "100",
                "ocrText": "100",
                "assignment_source": "per_cell_ocr",
                "bbox": {"x": 50, "y": 10, "width": 45, "height": 24},
                "x": 50,
                "y": 10,
            },
        ]

        deduped, debug = _deduplicate_assigned_table_cells(cells)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["row"], 0)
        self.assertEqual(deduped[0]["col"], 1)
        self.assertEqual(deduped[0]["assignment_source"], "per_cell_ocr")
        self.assertEqual(debug["removed"], 1)


if __name__ == "__main__":
    unittest.main()
