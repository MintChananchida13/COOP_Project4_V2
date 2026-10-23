import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import tunnel


class TunnelTests(unittest.TestCase):
    def test_extracts_only_fresh_quick_tunnel_url(self):
        stale = "https://stale.trycloudflare.com\n"
        contents = stale + "Registered https://fresh.trycloudflare.com\n"
        with patch.object(tunnel, "LOG_FILE", Path("fake_tunnel.log")), \
                patch.object(Path, "exists", return_value=True), \
                patch.object(Path, "open", return_value=io.StringIO(contents)), \
                patch.object(tunnel.time, "sleep"):
            self.assertEqual(tunnel.wait_url(Mock(poll=Mock(return_value=None)), len(stale), seconds=1), "https://fresh.trycloudflare.com")

    def test_running_tunnel_reuses_url_without_spawning(self):
        with patch.object(tunnel, "live_pid", return_value=123), \
                patch.object(tunnel, "public_url", return_value="https://live.trycloudflare.com"), \
                patch.object(tunnel.subprocess, "Popen") as spawn:
            self.assertEqual(tunnel.start(), "https://live.trycloudflare.com")
        spawn.assert_not_called()

    def test_running_tunnel_without_url_is_not_claimed_ready(self):
        with patch.object(tunnel, "live_pid", return_value=123), patch.object(tunnel, "public_url", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "without a recorded URL"):
                tunnel.start()

    def test_url_file_must_match_live_process(self):
        with patch.object(tunnel, "live_pid", return_value=123), \
                patch.object(Path, "exists", return_value=True), \
                patch.object(Path, "read_text", return_value='{"pid": 456, "url": "https://stale.trycloudflare.com"}'):
            self.assertIsNone(tunnel.public_url())

    def test_network_check_accepts_backend_health_envelope(self):
        backend_response = Mock()
        backend_response.__enter__ = Mock(return_value=backend_response)
        backend_response.__exit__ = Mock(return_value=False)
        cloudflare_response = Mock(status=403)
        cloudflare_response.__enter__ = Mock(return_value=cloudflare_response)
        cloudflare_response.__exit__ = Mock(return_value=False)
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        with patch.object(tunnel.socket, "create_connection", return_value=connection), \
                patch.object(tunnel.urllib.request, "urlopen", side_effect=[backend_response, cloudflare_response]), \
                patch.object(tunnel.json, "load", return_value={"status": "success", "data": {"status": "ok"}}):
            tunnel.network_check()


if __name__ == "__main__":
    unittest.main()
