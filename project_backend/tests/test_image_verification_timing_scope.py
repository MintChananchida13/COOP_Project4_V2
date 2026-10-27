import ast
import __future__
import logging
from pathlib import Path
import time
from types import SimpleNamespace
import unittest


class ImageVerificationTimingScopeTests(unittest.TestCase):
    def test_non_batch_image_verification_receives_local_timing(self):
        source = Path(__file__).parents[1] / "app/business/services.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "verify_template")
        namespace = {
            "time": time, "logger": logging.getLogger(__name__),
            "DETECTION_BATCH_MODE_GLOBAL": "global",
            "normalize_detection_batch_mode": lambda value: value,
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec",
                     flags=__future__.annotations.compiler_flag), namespace)

        def score(field, image_path, timing):
            self.assertIsInstance(timing, dict)
            timing.setdefault("model_calls", []).append({"call_id": "image-call"})
            return {"score": 1.0, "passed": True, "status": "verified", "failure_reason": None,
                    "verification_threshold": 0.5, "image_category": "emblem"}

        service = SimpleNamespace(_score_image_anchor=score)
        field = {"id": "image-field", "field_name": "verification_1", "display_label": "Image",
                 "data_type": "image", "page_number": 1, "roi": {},
                 "required_for_verification": False}
        result = namespace["verify_template"](service, "template", {1: "image.png"}, [field])
        self.assertEqual(result["image_anchor_score"], 1.0)
        self.assertTrue(result["checked_fields"][0]["passed"])
        self.assertIsNone(result["checked_fields"][0]["error"])
        self.assertEqual(result["timing"]["model_calls"], [{"call_id": "image-call"}])
