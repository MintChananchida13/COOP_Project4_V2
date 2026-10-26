import unittest
from pathlib import Path
from unittest.mock import patch

import user_flow


class UserFlowTests(unittest.TestCase):
    def test_input_cases_group_by_mode(self):
        root = Path("input")
        files = [root / "standard" / "a.png", root / "strict" / "b.jpg", root / "ignore.txt"]
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "rglob", return_value=files), patch.object(Path, "is_file", return_value=True):
            self.assertEqual([(path.name, mode) for path, mode in user_flow.input_cases(root, None)],
                             [("a.png", "standard"), ("b.jpg", "strict")])

    def test_mode_conflict_is_rejected(self):
        root = Path("input")
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "rglob", return_value=[root / "strict" / "a.png"]), patch.object(Path, "is_file", return_value=True):
            with self.assertRaisesRegex(ValueError, "Mode conflict"):
                user_flow.input_cases(root, "standard")

    def test_completed_job_and_timing(self):
        result = {"matched": True, "best_candidate": {"verification_strategy": "standard"},
                  "debug": {"timing": {"backend_runtime_ms": 23, "total_detection_ms": 50}}}
        self.assertEqual(user_flow.detection_result({"data": {"status": "completed", "result": result}}), result)
        self.assertEqual(user_flow.actual_mode(result), "standard")
        values = user_flow.timing_values([[{"stage": "uploadJobMs", "elapsedMs": 5},
                                           {"metric": "end_to_end_ms", "valueMs": 80}]], result)
        self.assertEqual(values["uploadJobMs"], 5)
        self.assertEqual(values["end_to_end_ms"], 80)
        self.assertEqual(values["backend_runtime_ms"], 23)
        self.assertEqual(values["total_runtime_ms"], 50)
        self.assertIsNone(values["frontend_render_ms"])

    def test_excel_formula_is_not_evaluated(self):
        self.assertEqual(user_flow.excel_value("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")


if __name__ == "__main__":
    unittest.main()
