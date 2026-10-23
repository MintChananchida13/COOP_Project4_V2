"""Start, inspect, test, and stop both temporary Kaggle services."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from install import BACKEND_ROOT, SERVICE_ROOT, WORK_ROOT, python_path
from kaggle_secrets_loader import load_secrets


API_URL = "http://127.0.0.1:8080"
BACKEND_URL = "http://127.0.0.1:18081"
REQUIRED_SECRETS = ("DATABASE_URL", "MODEL_GATEWAY_API_KEY")
OPTIONAL_SECRETS = ("FRONTEND_URL",)


def request(url, *, payload=None, token=None, timeout=10):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as response:
        return json.load(response)


def port_open(port):
    with socket.socket() as connection:
        return connection.connect_ex(("127.0.0.1", port)) == 0


def pid_file(name):
    return WORK_ROOT / f"{name}.pid"


def live_pid(name):
    path = pid_file(name)
    if not path.exists():
        return None
    try:
        process_id = int(path.read_text().strip())
        os.kill(process_id, 0)
        command_line = Path(f"/proc/{process_id}/cmdline")
        if command_line.exists():
            command = command_line.read_bytes().replace(b"\0", b" ")
            expected = b"app.main:app" if name == "api" else b"main:app"
            if b"uvicorn" not in command or expected not in command:
                return None
        return process_id
    except (OSError, ValueError):
        return None


def wait_health(url, process, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Process exited ({process.returncode}); inspect its log")
        try:
            return request(url)
        except (OSError, ValueError):
            time.sleep(1)
    raise RuntimeError(f"Health timeout: {url}")


def spawn(name, python, cwd, module, port, environment):
    if live_pid(name) or port_open(port):
        raise RuntimeError(f"{name} already running or port {port} occupied")
    log = WORK_ROOT / f"{name}.log"
    with log.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(
            [str(python), "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
            cwd=cwd,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_file(name).write_text(str(process.pid), encoding="utf-8")
    print(f"{name}: pid={process.pid}, log={log}")
    return process


def validate_config(values):
    missing = [name for name in REQUIRED_SECRETS if not values.get(name)]
    if missing:
        raise RuntimeError("Missing Kaggle secrets or environment variables: " + ", ".join(missing))
    parsed = urlparse(values["DATABASE_URL"])
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise RuntimeError("DATABASE_URL must be a PostgreSQL URL")
    if len(values["MODEL_GATEWAY_API_KEY"].strip()) < 8:
        raise RuntimeError("MODEL_GATEWAY_API_KEY must contain at least 8 characters")
    print("Configuration: DATABASE_URL, MODEL_GATEWAY_API_KEY present; GATEWAY_URL fixed to local API")


def start():
    values = load_secrets(REQUIRED_SECRETS + OPTIONAL_SECRETS)
    validate_config(values)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    api_python = python_path(WORK_ROOT / "api_env")
    backend_python = python_path(WORK_ROOT / "backend_env")
    if not api_python.exists() or not backend_python.exists():
        raise RuntimeError("Run install.py first")
    environment = os.environ.copy()
    environment.update(values)
    environment.update({
        "GATEWAY_URL": API_URL,
        "MODEL_DEVICE": "cpu",
        "CUDA_VISIBLE_DEVICES": "",
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
        "SAVE_CROPPED_ROIS": "false",
    })
    started_names = []
    try:
        api_process = spawn("api", api_python, SERVICE_ROOT, "app.main:app", 8080, environment)
        started_names.append("api")
        print("API health:", json.dumps(wait_health(API_URL + "/health", api_process)))
        backend_process = spawn("backend", backend_python, BACKEND_ROOT, "main:app", 18081, environment)
        started_names.append("backend")
        print("Backend health:", json.dumps(wait_health(BACKEND_URL + "/health", backend_process)))
        check()
    except Exception:
        stop_selected(reversed(started_names))
        raise


def check():
    for name, url in (("api", API_URL + "/health"), ("backend", BACKEND_URL + "/health"), ("database", BACKEND_URL + "/health/db"), ("models_config", BACKEND_URL + "/health/models")):
        try:
            response = request(url)
            if name == "models_config":
                data = response.get("data", {})
                response = {"status": data.get("status"), "runtimes": data.get("runtimes")}
            else:
                data = response.get("data", response)
            if (response if name == "models_config" else data).get("status") != "ok":
                raise RuntimeError(f"{name} status is not ok")
            print(f"{name}: {json.dumps(response)}")
        except (OSError, ValueError) as error:
            print(f"{name}: failed ({type(error).__name__})")
            raise RuntimeError(f"{name} check failed") from error


def integration():
    check()
    values = load_secrets(("MODEL_GATEWAY_API_KEY",))
    token = values.get("MODEL_GATEWAY_API_KEY")
    if not token:
        raise RuntimeError("MODEL_GATEWAY_API_KEY required for integration")
    backend_python = python_path(WORK_ROOT / "backend_env")
    code = """import numpy as np
from app.core.model_runtime_client import remote_analyze_layout
result = remote_analyze_layout(np.zeros((64, 128, 3), dtype=np.uint8), layout_only=True)
assert isinstance(result, dict) and 'raw' in result
print('Backend -> API real layout inference: ok')"""
    environment = os.environ.copy()
    environment.update(values)
    environment["GATEWAY_URL"] = API_URL
    subprocess.run([str(backend_python), "-c", code], cwd=BACKEND_ROOT, env=environment, check=True, timeout=240)


def status():
    for name, port in (("api", 8080), ("backend", 18081)):
        process_id = live_pid(name)
        print(f"{name}: pid={process_id or '-'}, port={port}, listening={port_open(port)}, log={WORK_ROOT / (name + '.log')}")
    try:
        import psutil

        for name in ("api", "backend"):
            process_id = live_pid(name)
            if process_id:
                process = psutil.Process(process_id)
                print(f"{name}: rss_mb={process.memory_info().rss / 1048576:.1f}, cpu_percent={process.cpu_percent(interval=0.2):.1f}")
    except ImportError:
        print("Install psutil for per-process RAM/CPU measurements")


def stop_selected(names):
    for name in names:
        process_id = live_pid(name)
        if process_id:
            os.kill(process_id, signal.SIGTERM)
            print(f"Stopped {name} pid={process_id}")
        pid_file(name).unlink(missing_ok=True)


def stop():
    stop_selected(("backend", "api"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("start", "check", "integration", "status", "stop"))
    arguments = parser.parse_args()
    try:
        {"start": start, "check": check, "integration": integration, "status": status, "stop": stop}[arguments.command]()
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"Failed: {error}", file=sys.stderr)
        sys.exit(1)
