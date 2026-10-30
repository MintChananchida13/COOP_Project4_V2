import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_external_api_conflicts_reuse_environment(self):
        installer = SimpleNamespace(
            WORK_ROOT=Path("fake_work"),
            environment_state=Mock(return_value="healthy"),
            python_path=Mock(return_value=Path("python")),
            validate_runtime=Mock(side_effect=[([], []), ([], ["torchvision has requirement torch"])]),
        )
        with patch("builtins.print"):
            self.assertEqual(bootstrap.environment_issues(installer), [])
        self.assertEqual(installer.validate_runtime.call_count, 2)

    def test_critical_api_failure_requires_repair(self):
        installer = SimpleNamespace(
            WORK_ROOT=Path("fake_work"),
            environment_state=Mock(return_value="healthy"),
            python_path=Mock(return_value=Path("python")),
            validate_runtime=Mock(side_effect=[([], []), (["SigLIP forward smoke failed"], [])]),
        )
        self.assertEqual(bootstrap.environment_issues(installer), ["api_env: SigLIP forward smoke failed"])

    def test_redacts_secrets_from_subprocess_output(self):
        self.assertEqual(
            bootstrap.redact("database=pw1 key=key123", {"DATABASE_URL": "pw1", "MODEL_GATEWAY_API_KEY": "key123"}),
            "database=[REDACTED] key=[REDACTED]",
        )

    def test_command_output_redacts_secret(self):
        completed = SimpleNamespace(returncode=0, stdout="connection to postgresql://user:secret@host/db failed")
        with patch.object(bootstrap.subprocess, "run", return_value=completed):
            output = bootstrap.run(["python", "manage.py"], secrets={"DATABASE_URL": "postgresql://user:secret@host/db"})
        self.assertNotIn("secret", output)
        self.assertIn("[REDACTED]", output)

    def test_missing_checkout_clones_requested_branch(self):
        with patch.object(bootstrap, "REPOSITORY", Path("fake_repo")), \
                patch.object(Path, "exists", side_effect=[False, True]), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "mkdir"), \
                patch.object(bootstrap, "run") as run, \
                patch.object(bootstrap, "git", side_effect=[bootstrap.REMOTE, bootstrap.BRANCH]):
            self.assertEqual(bootstrap.ensure_repository(), bootstrap.BRANCH)
        self.assertEqual(run.call_args.args[0][0:4], ["git", "clone", "--branch", bootstrap.BRANCH])

    def test_dirty_checkout_skips_update_without_reset(self):
        with patch.object(bootstrap, "REPOSITORY", Path("fake_repo")), \
                patch.object(Path, "exists", return_value=True), patch.object(Path, "is_file", return_value=True), \
                patch.object(bootstrap, "git", side_effect=[bootstrap.REMOTE, bootstrap.BRANCH, " M local.py"]) as git:
            self.assertEqual(bootstrap.ensure_repository(update=True), bootstrap.BRANCH)
        self.assertEqual(git.call_count, 3)

    def test_clean_checkout_fast_forwards_only(self):
        with patch.object(bootstrap, "REPOSITORY", Path("fake_repo")), \
                patch.object(Path, "exists", return_value=True), patch.object(Path, "is_file", return_value=True), \
                patch.object(bootstrap, "git", side_effect=[bootstrap.REMOTE, bootstrap.BRANCH, "", "", ""]) as git:
            bootstrap.ensure_repository(update=True)
        self.assertEqual(git.call_args_list[-1].args, ("merge", "--ff-only", f"origin/{bootstrap.BRANCH}"))

    def test_wrong_branch_is_rejected(self):
        with patch.object(bootstrap, "REPOSITORY", Path("fake_repo")), \
                patch.object(Path, "exists", return_value=True), \
                patch.object(bootstrap, "git", side_effect=[bootstrap.REMOTE, "main"]):
            with self.assertRaisesRegex(RuntimeError, "Expected branch"):
                bootstrap.ensure_repository()

    def test_verified_environments_skip_installer(self):
        with patch.object(bootstrap, "environment_issues", return_value=[]), patch.object(bootstrap, "run") as run:
            bootstrap.ensure_environments(Mock(), Mock(), {})
        run.assert_not_called()

    def test_missing_secrets_stop_before_install_and_start(self):
        management = SimpleNamespace(validate_config=Mock(side_effect=RuntimeError("Missing Kaggle secrets: DATABASE_URL")), live_pid=Mock(return_value=None), API_URL="http://127.0.0.1:8080", BACKEND_URL="http://127.0.0.1:18081")
        installer = SimpleNamespace(WORK_ROOT=Path("fake_work"))
        loader = SimpleNamespace(load_secrets=Mock(return_value={}))
        with patch.object(bootstrap, "ensure_repository", return_value=bootstrap.BRANCH), \
                patch.object(bootstrap, "modules", return_value=(installer, management, Mock(), loader)), \
                patch.object(bootstrap, "ensure_environments") as install, \
                patch.object(bootstrap, "run") as run, \
                patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            self.assertFalse(bootstrap.bootstrap())
        install.assert_not_called()
        run.assert_not_called()

    def test_full_startup_checks_services_before_tunnel(self):
        with patch.object(bootstrap, "REPOSITORY", Path("fake_repo")):
            values = {"DATABASE_URL": "postgresql://user:password@host/db", "MODEL_GATEWAY_API_KEY": "secret-key"}
            installer = SimpleNamespace(WORK_ROOT=Path("fake_work"), python_path=lambda path: path / "python")
            management = SimpleNamespace(
                API_URL="http://127.0.0.1:8080", BACKEND_URL="http://127.0.0.1:18081",
                validate_config=Mock(), live_pid=Mock(return_value=123),
            )
            tunnel = SimpleNamespace(public_url=Mock(return_value="https://real.trycloudflare.com"), LOG_FILE=Path("fake_work/tunnel.log"))
            loader = SimpleNamespace(load_secrets=Mock(return_value=values))
            events = []

            def run_command(arguments, **kwargs):
                events.append(str(arguments[-1]))
                return ""

            output = io.StringIO()
            with patch.object(bootstrap, "ensure_repository", return_value=bootstrap.BRANCH), \
                    patch.object(bootstrap, "modules", return_value=(installer, management, tunnel, loader)), \
                    patch.object(bootstrap, "ensure_environments"), \
                    patch.object(bootstrap, "ensure_cloudflared"), \
                    patch.object(bootstrap, "run", side_effect=run_command), \
                    patch.object(bootstrap, "health", return_value={"status": "ok"}), \
                    patch.object(bootstrap, "public_health", return_value=True), \
                    patch("sys.stdout", output):
                self.assertTrue(bootstrap.bootstrap())
            self.assertEqual(events, ["start", "status", "check", str(Path("fake_repo") / "API_Service_Kaggle/kaggle/warmup_models.py"), "start"])
            self.assertIn("NEXT_PUBLIC_API_URL=https://real.trycloudflare.com", output.getvalue())
            self.assertNotIn("password", output.getvalue())
            self.assertNotIn("secret-key", output.getvalue())

    def test_public_health_failure_does_not_report_ready_tunnel(self):
        with patch.object(bootstrap, "REPOSITORY", Path("fake_repo")):
            management = SimpleNamespace(
                API_URL="http://127.0.0.1:8080", BACKEND_URL="http://127.0.0.1:18081",
                validate_config=Mock(), live_pid=Mock(return_value=123),
            )
            tunnel = SimpleNamespace(public_url=Mock(return_value="https://real.trycloudflare.com"), LOG_FILE=Path("fake_work/tunnel.log"))
            loader = SimpleNamespace(load_secrets=Mock(return_value={"DATABASE_URL": "postgresql://host/db", "MODEL_GATEWAY_API_KEY": "secret-key"}))
            output = io.StringIO()
            with patch.object(bootstrap, "ensure_repository", return_value=bootstrap.BRANCH), \
                    patch.object(bootstrap, "modules", return_value=(SimpleNamespace(WORK_ROOT=Path("fake_work"), python_path=lambda path: path / "python"), management, tunnel, loader)), \
                    patch.object(bootstrap, "ensure_environments"), \
                    patch.object(bootstrap, "ensure_cloudflared"), \
                    patch.object(bootstrap, "run", return_value=""), \
                    patch.object(bootstrap, "health", return_value={"status": "ok"}), \
                    patch.object(bootstrap, "public_health", return_value=False), \
                    patch("sys.stdout", output), patch("sys.stderr", io.StringIO()):
                self.assertFalse(bootstrap.bootstrap())
            self.assertIn("NOT VERIFIED", output.getvalue())
            self.assertNotIn("Tunnel: healthy", output.getvalue())


if __name__ == "__main__":
    unittest.main()
