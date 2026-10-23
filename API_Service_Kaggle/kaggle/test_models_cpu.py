import ast
import logging
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


MODEL_FILE = Path(__file__).resolve().parents[1] / "app" / "models.py"


class CpuModelTests(unittest.TestCase):
    def test_paddle_models_use_cpu_without_unsupported_flag(self):
        source = ast.parse(MODEL_FILE.read_text(encoding="utf-8"), filename=str(MODEL_FILE))
        model_manager = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "ModelManager")
        names = {"layout": "PP-DocLayoutV3", "det": "PP-OCRv6_medium_det", "rec": "th_PP-OCRv5_mobile_rec", "table": "TableRecognitionPipelineV2", "siglip": "google/siglip-so400m-patch14-384"}
        namespace = {"NAMES": names, "threading": threading, "time": time, "logger": logging.getLogger(__name__), "settings": types.SimpleNamespace(cpu_threads=2)}
        exec(compile(ast.Module(body=[model_manager], type_ignores=[]), str(MODEL_FILE), "exec"), namespace)

        paddle = types.ModuleType("paddle")
        paddle.set_device = Mock()
        paddle.set_flags = Mock(side_effect=ValueError("unsupported FLAGS_cpu_num_threads"))
        paddleocr = types.ModuleType("paddleocr")
        constructors = {}
        for name in ("LayoutDetection", "TextDetection", "TextRecognition", "TableRecognitionPipelineV2"):
            constructors[name] = Mock(return_value=object())
            setattr(paddleocr, name, constructors[name])

        with patch.dict(sys.modules, {"paddle": paddle, "paddleocr": paddleocr}):
            manager = namespace["ModelManager"]()
            for kind in ("layout", "det", "rec", "table"):
                manager.get(kind)
                manager.get(kind)

        self.assertEqual(paddle.set_device.call_count, 4)
        paddle.set_device.assert_any_call("cpu")
        paddle.set_flags.assert_not_called()
        for constructor in constructors.values():
            constructor.assert_called_once()
            self.assertEqual(constructor.call_args.kwargs["device"], "cpu")


if __name__ == "__main__":
    unittest.main()
