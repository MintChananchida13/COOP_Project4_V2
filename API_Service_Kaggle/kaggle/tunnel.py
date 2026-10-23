"""Optional temporary HTTPS tunnel for the backend port only."""

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.error import HTTPError
from pathlib import Path

from install import WORK_ROOT


PID_FILE = WORK_ROOT / "tunnel.pid"
LOG_FILE = WORK_ROOT / "tunnel.log"
URL_FILE = WORK_ROOT / "tunnel.url"
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def live_pid():
    if not PID_FILE.exists():
        return None
    try:
        process_id = int(PID_FILE.read_text().strip())
        command_line = Path(f"/proc/{process_id}/cmdline")
        if command_line.exists() and b"cloudflared" in command_line.read_bytes():
            os.kill(process_id, 0)
            return process_id
    except (OSError, ValueError):
        pass
    return None


def public_url():
    if live_pid() and URL_FILE.exists():
        try:
            state = json.loads(URL_FILE.read_text())
            url = state.get("url")
            if state.get("pid") == live_pid() and isinstance(url, str) and URL_PATTERN.fullmatch(url):
                return url
        except (OSError, ValueError, AttributeError):
            pass
    return None


def wait_url(process, offset, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"cloudflared exited; inspect {LOG_FILE}")
        if LOG_FILE.exists():
            with LOG_FILE.open(encoding="utf-8", errors="replace") as log:
                log.seek(offset)
                match = URL_PATTERN.search(log.read())
            if match:
                return match.group(0)
        time.sleep(1)
    raise RuntimeError(f"Tunnel URL timeout; inspect {LOG_FILE}")


def network_check():
    with socket.create_connection(("127.0.0.1", 18081), timeout=3):
        pass
    with urllib.request.urlopen("http://127.0.0.1:18081/health", timeout=5) as response:
        body = json.load(response)
        data = body.get("data", body)
        if not isinstance(data, dict) or data.get("status") != "ok":
            raise RuntimeError("Backend health is not ok")
    try:
        with urllib.request.urlopen("https://api.cloudflare.com/client/v4", timeout=10) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    print(f"Cloudflare HTTPS reachability: HTTP {status}")
    print("HTTPS reachable; tunnel protocol connectivity remains unverified until start")


def start():
    if live_pid():
        url = public_url()
        if not url:
            raise RuntimeError(f"Tunnel is running without a recorded URL; inspect {LOG_FILE}")
        print(f"Tunnel already running: {url}")
        return url
    binary = shutil.which("cloudflared") or (str(WORK_ROOT / "cloudflared") if (WORK_ROOT / "cloudflared").is_file() else None)
    if not binary:
        raise RuntimeError("cloudflared executable is not installed; install it separately on Kaggle")
    network_check()
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    PID_FILE.unlink(missing_ok=True)
    URL_FILE.unlink(missing_ok=True)
    offset = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
    with LOG_FILE.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(
            [binary, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:18081"],
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    url = wait_url(process, offset)
    URL_FILE.write_text(json.dumps({"pid": process.pid, "url": url}), encoding="utf-8")
    print(f"Tunnel PID {process.pid}; URL {url}; log {LOG_FILE}")
    return url


def status():
    print(f"Tunnel PID {live_pid() or '-'}; URL {public_url() or '-'}; log {LOG_FILE}")


def stop():
    if PID_FILE.exists():
        process_id = int(PID_FILE.read_text().strip())
        command_line = Path(f"/proc/{process_id}/cmdline")
        if command_line.exists() and b"cloudflared" in command_line.read_bytes():
            os.kill(process_id, signal.SIGTERM)
            print("Tunnel stopped")
        PID_FILE.unlink()
    URL_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "start", "status", "stop"))
    arguments = parser.parse_args()
    try:
        {"check": network_check, "start": start, "status": status, "stop": stop}[arguments.command]()
    except (RuntimeError, OSError) as error:
        print(f"Tunnel unavailable: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
