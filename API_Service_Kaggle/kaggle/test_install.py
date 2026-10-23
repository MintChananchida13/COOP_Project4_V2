import unittest
from pathlib import Path
from unittest.mock import patch

import install


class InstallPlanTests(unittest.TestCase):
    def test_backend_numpy_override_and_original_unchanged(self):
        original = (install.BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
        items = install.requirements(install.BACKEND_ROOT / "requirements.txt", {"numpy": "2.1.3"})
        numpy = next(item for item in items if item["name"] == "numpy")
        self.assertEqual(numpy["original"], "numpy==1.26.4")
        self.assertEqual(numpy["specification"], "numpy==2.1.3")
        self.assertEqual(original, (install.BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8"))

    def test_preflight_reuses_working_heavy_packages(self):
        report = {
            "python": "3.13.15",
            "packages": {
                "paddlepaddle": "3.2.1", "paddleocr": "3.7.0", "paddlex": "3.7.2",
                "torch": "2.11.0+cpu", "transformers": "5.16.1", "numpy": "2.1.3",
            },
        }
        with patch.object(install, "version_report", return_value=report), patch.object(install, "smoke", return_value=(True, "")), patch("builtins.print"):
            backend, api_install, reused = install.preflight(Path("python"))
        self.assertEqual(next(item["version"] for item in backend if item["name"] == "numpy"), "2.1.3")
        self.assertEqual(reused["torch"], "2.11.0+cpu")
        self.assertEqual(reused["transformers"], "5.16.1")
        self.assertNotIn("paddlepaddle==3.2.1", api_install)
        self.assertIn("paddleocr[doc-parser]==3.7.0", api_install)

    def test_preflight_falls_back_from_incompatible_transformers(self):
        report = {"python": "3.13.15", "packages": {"transformers": "5.16.1"}}
        with patch.object(install, "version_report", return_value=report), patch.object(install, "smoke", side_effect=[(False, "OCR absent"), (False, "SigLIP failed")]), patch("builtins.print"):
            _, api_install, reused = install.preflight(Path("python"))
        self.assertNotIn("transformers", reused)
        self.assertIn("transformers==4.51.3", api_install)

    def test_python_312_rejected(self):
        with patch.object(install, "version_report", return_value={"python": "3.12.10", "packages": {}}):
            with self.assertRaisesRegex(RuntimeError, "Python 3.13"):
                install.preflight(Path("python"))


if __name__ == "__main__":
    unittest.main()
