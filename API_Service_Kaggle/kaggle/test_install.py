import unittest
from pathlib import Path
from unittest.mock import patch

import install


class InstallPlanTests(unittest.TestCase):
    def test_complete_environment_is_reused(self):
        environment = Path("runtime") / "backend_env"
        with patch.object(install, "environment_state", return_value="healthy"), patch.object(install, "run") as create:
            self.assertEqual(install.ensure_environment(environment, system_site_packages=False), install.python_path(environment))
        create.assert_not_called()

    def test_incomplete_environment_is_recreated(self):
        root = Path("runtime")
        environment = root / "api_env"
        with patch.object(install, "WORK_ROOT", root), patch.object(install, "environment_state", side_effect=["incomplete", "healthy"]), patch.object(install, "run") as create, patch.object(Path, "is_dir", return_value=True), patch.object(install.shutil, "rmtree") as remove:
            install.ensure_environment(environment, system_site_packages=True)
        remove.assert_called_once_with(environment)
        arguments = create.call_args.args[0]
        self.assertIn("--system-site-packages", arguments)
        self.assertIn("virtualenv", arguments)

    def test_missing_pip_marks_environment_incomplete(self):
        environment = Path("runtime") / "backend_env"
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_text", return_value="include-system-site-packages = false\n"), patch.object(install, "pip_available", return_value=False):
            self.assertEqual(install.environment_state(environment, system_site_packages=False), "incomplete")

    def test_wrong_site_packages_marks_environment_incomplete(self):
        environment = Path("runtime") / "api_env"
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_text", return_value="include-system-site-packages = false\n"), patch.object(install, "pip_available") as pip_check:
            self.assertEqual(install.environment_state(environment, system_site_packages=True), "incomplete")
        pip_check.assert_not_called()

    def test_missing_python_marks_environment_incomplete(self):
        environment = Path("runtime") / "backend_env"
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "is_file", side_effect=[True, False]):
            self.assertEqual(install.environment_state(environment, system_site_packages=False), "incomplete")

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
