import unittest
from pathlib import Path
from unittest.mock import patch

import manage


class ConfigTests(unittest.TestCase):
    def test_requires_secrets(self):
        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
            manage.validate_config({"MODEL_GATEWAY_API_KEY": "12345678"})

    def test_rejects_non_postgres(self):
        with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
            manage.validate_config({"DATABASE_URL": "sqlite:///tmp/db", "MODEL_GATEWAY_API_KEY": "12345678"})

    def test_valid_config_does_not_print_secret(self):
        with patch("builtins.print") as output:
            manage.validate_config({"DATABASE_URL": "postgresql://user:password@db.example/test", "MODEL_GATEWAY_API_KEY": "secret-key"})
        printed = str(output.call_args_list)
        self.assertNotIn("password", printed)
        self.assertNotIn("secret-key", printed)

    def test_cpu_threads_are_set_before_service_spawn(self):
        values = {"DATABASE_URL": "postgresql://user:password@db.example/test", "MODEL_GATEWAY_API_KEY": "secret-key"}
        with patch.object(manage, "load_secrets", return_value=values), patch.object(manage, "python_path", return_value=Path(__file__)), patch.object(Path, "mkdir"), patch.object(manage, "spawn") as spawn, patch.object(manage, "wait_health", return_value={"status": "ok"}), patch.object(manage, "check"), patch.dict(manage.os.environ, {"MODEL_CPU_THREADS": "3"}):
            manage.start()
        for call in spawn.call_args_list:
            environment = call.args[-1]
            self.assertEqual(environment["MODEL_DEVICE"], "cpu")
            self.assertEqual(environment["OMP_NUM_THREADS"], "3")
            self.assertEqual(environment["MKL_NUM_THREADS"], "3")

    def test_start_reuses_healthy_processes_without_spawn(self):
        values = {"DATABASE_URL": "postgresql://user:password@db.example/test", "MODEL_GATEWAY_API_KEY": "secret-key"}
        with patch.object(manage, "load_secrets", return_value=values), \
                patch.object(manage, "python_path", return_value=Path(__file__)), \
                patch.object(manage, "live_pid", return_value=123), \
                patch.object(manage, "request", side_effect=[{"status": "ok"}, {"status": "success", "data": {"status": "ok"}}]), \
                patch.object(Path, "mkdir"), \
                patch.object(manage, "spawn") as spawn, patch.object(manage, "check"):
            manage.start()
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
