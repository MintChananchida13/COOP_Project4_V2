import ast
from pathlib import Path
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]

class PrepublishOriginalImageTests(unittest.TestCase):
    def test_skip_normalization_preserves_paths_and_never_calls_crop_or_resize(self):
        source = (ROOT / "app/processing/detection_service.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_normalize_query_pages")
        # Run the real function with a filesystem stub; image processing must never be reached.
        function.returns = None
        for arg in function.args.args:
            arg.annotation = None
        directory = Mock()
        directory.__truediv__ = Mock(return_value=directory)
        service = Mock()
        context = {"time": time, "_storage_path": lambda: directory, "normalization_service": service}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "normalization", "exec"), context)
        paths = [Path("page_1.png"), Path("page_2.png")]
        for source_type in ("image", "pdf"):
            result = context["_normalize_query_pages"]("test", paths, skip_normalization=True, source_type=source_type)
            self.assertEqual([r["matching_path"] for r in result], [str(p) for p in paths])
            for page in result:
                self.assertFalse(page["normalization"]["crop_applied"])
                self.assertFalse(page["normalization"]["perspective_applied"])
                self.assertEqual(page["normalization"]["normalization_status"], "skipped")
        self.assertEqual(service.mock_calls, [])

    def test_only_prepublish_opts_in_and_normal_detection_defaults_are_preserved(self):
        tree = ast.parse((ROOT / "app/processing/detection_service.py").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "detect_template_dev")
        defaults = dict(zip([a.arg for a in function.args.args][-len(function.args.defaults):], function.args.defaults))
        self.assertIs(ast.literal_eval(defaults["skip_query_normalization"]), False)
        assignment = next(n for n in ast.walk(function) if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "skip_normalization" for t in n.targets))
        self.assertEqual(assignment.value.id, "skip_query_normalization")
        tree = ast.parse((ROOT / "app/business/services.py").read_text(encoding="utf-8"))
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_prepublish_detection_test")
        call = next(n for n in ast.walk(method) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "detect_template_dev")
        kwargs = {k.arg: k.value for k in call.keywords}
        self.assertIs(ast.literal_eval(kwargs["skip_query_normalization"]), True)
