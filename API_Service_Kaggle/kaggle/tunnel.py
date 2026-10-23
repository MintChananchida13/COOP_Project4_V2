"""Optional temporary HTTPS tunnel for the backend port only."""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import urllib.request
from urllib.error import HTTPError
from pathlib import Path

from install import WORK_ROOT


PID_FILE = WORK_ROOT / "tunnel.pid"
LOG_FILE = WORK_ROOT / "tunnel.log"


def network_check():
    with socket.create_connection(("127.0.0.1", 18081), timeout=3):
        pass
    try:
        with urllib.request.urlopen("https://api.cloudflare.com/client/v4", timeout=10) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    print(f"Cloudflare HTTPS reachability: HTTP {status}")
    print("HTTPS reachable; tunnel protocol connectivity remains unverified until start")


def start():
    binary = shutil.which("cloudflared")
    if not binary:
        raise RuntimeError("cloudflared executable is not installed; install it separately on Kaggle")
    network_check()
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        raise RuntimeError("Tunnel PID file exists; inspect or stop the prior tunnel")
    with LOG_FILE.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(
            [binary, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:18081"],
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    print(f"Tunnel PID {process.pid}; inspect {LOG_FILE} for the generated HTTPS URL")


def stop():
    if PID_FILE.exists():
        process_id = int(PID_FILE.read_text().strip())
        command_line = Path(f"/proc/{process_id}/cmdline")
        if command_line.exists() and b"cloudflared" in command_line.read_bytes():
            os.kill(process_id, signal.SIGTERM)
            print("Tunnel stopped")
        PID_FILE.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "start", "stop"))
    arguments = parser.parse_args()
    try:
        {"check": network_check, "start": start, "stop": stop}[arguments.command]()
    except (RuntimeError, OSError) as error:
        print(f"Tunnel unavailable: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
