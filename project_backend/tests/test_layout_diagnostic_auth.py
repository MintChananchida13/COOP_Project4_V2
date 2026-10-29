import importlib.util
import io
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("layout_probe", Path(__file__).resolve().parents[1] / "tools/diagnose_layout_signature.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

class DiagnosticAuthTests(unittest.TestCase):
    def modules(self, values):
        return {"manage": types.SimpleNamespace(live_pid=lambda name: 123),
                "kaggle_secrets_loader": types.SimpleNamespace(load_secrets=lambda names: values)}

    def test_local_runtime_credential_wins_without_disclosure(self):
        output = io.StringIO()
        with patch.dict("sys.modules", self.modules({"MODEL_GATEWAY_API_KEY":"fallback"})), patch.dict(os.environ, {}, clear=True), patch.object(probe.Path,"read_bytes",return_value=b"OTHER=ignored\0MODEL_GATEWAY_API_KEY=runtime-token\0"), patch("sys.stdout",output):
            probe.configure_gateway("http://127.0.0.1:8080/")
            self.assertEqual(os.environ["MODEL_GATEWAY_API_KEY"],"runtime-token")
            self.assertEqual(os.environ["GATEWAY_URL"],"http://127.0.0.1:8080")
        self.assertNotIn("runtime-token",output.getvalue())

    def test_missing_secret_stops_before_inference(self):
        with patch.dict("sys.modules",self.modules({})), patch.object(probe.Path,"read_bytes",side_effect=PermissionError()):
            with self.assertRaisesRegex(RuntimeError,"credential unavailable"):
                probe.configure_gateway("http://127.0.0.1:8080")

    def test_remote_gateway_does_not_read_local_process_credential(self):
        with patch.dict("sys.modules",self.modules({"MODEL_GATEWAY_API_KEY":"configured-token"})), patch.dict(os.environ,{},clear=True), patch.object(probe.Path,"read_bytes") as read, patch("sys.stdout",io.StringIO()):
            probe.configure_gateway("https://models.example")
            read.assert_not_called()
            self.assertEqual(os.environ["MODEL_GATEWAY_API_KEY"],"configured-token")
