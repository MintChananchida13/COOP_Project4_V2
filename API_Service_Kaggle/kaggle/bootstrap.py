"""One-cell, repeatable startup for the temporary Kaggle CPU stack."""

import argparse
import hashlib
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


REPOSITORY = Path("/kaggle/working/COOP_Project4_Kaggle")
REMOTE = "https://github.com/MintChananchida13/COOP_Project4_V2.git"
BRANCH = "kaggle-cpu-runtime"
KAGGLE_DIRECTORY = Path("API_Service_Kaggle/kaggle")
REQUIRED_SECRETS = ("DATABASE_URL", "MODEL_GATEWAY_API_KEY")
CLOUDFLARED_VERSION = "2026.7.1"
CLOUDFLARED_SHA256 = "79a0ade7fc854f62c1aaef48424d9d979e8c2fcd039189d24db82b84cd146be1"
CLOUDFLARED_URL = (
    f"https://github.com/cloudflare/cloudflared/releases/download/"
    f"{CLOUDFLARED_VERSION}/cloudflared-linux-amd64"
)


def redact(value, secrets):
    result = value
    for secret in sorted((item for item in secrets.values() if item), key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    return result


def run(arguments, *, cwd=None, environment=None, secrets=None, log=None, timeout=1800):
    try:
        completed = subprocess.run(
            [str(argument) for argument in arguments], cwd=cwd, env=environment,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Timed out: {arguments[0]}; log={log or '-'}") from error
    output = redact(completed.stdout or "", secrets or {})
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as destination:
            destination.write(output)
    if completed.returncode:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {Path(str(arguments[0])).name}; "
            f"log={log or '-'}; last output={output[-600:]}"
        )
    return output


def git(*arguments):
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY), *arguments],
        text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"Git {arguments[0]} failed; inspect repository {REPOSITORY}")
    return completed.stdout.strip()


def ensure_repository(update=False):
    if not REPOSITORY.exists():
        REPOSITORY.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--branch", BRANCH, "--single-branch", REMOTE, REPOSITORY], timeout=600)
        print("Repository: cloned requested branch")
    if not (REPOSITORY / ".git").exists():
        raise RuntimeError(f"Repository path exists but is not a Git checkout: {REPOSITORY}")
    if git("remote", "get-url", "origin") != REMOTE:
        raise RuntimeError("Existing checkout has a different origin; refusing to update or start")
    branch = git("branch", "--show-current")
    if branch != BRANCH:
        raise RuntimeError(f"Expected branch {BRANCH}; found {branch or 'detached HEAD'}; refusing to switch")
    if update:
        if git("status", "--porcelain"):
            print("Repository: local changes detected; skipping update without reset")
        else:
            git("fetch", "origin", BRANCH)
            git("merge", "--ff-only", f"origin/{BRANCH}")
            print("Repository: fast-forward update complete")
    if not (REPOSITORY / KAGGLE_DIRECTORY / "manage.py").is_file():
        raise RuntimeError("Checkout lacks Kaggle startup scripts; update safely before retrying")
    return branch


def modules():
    directory = str(REPOSITORY / KAGGLE_DIRECTORY)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    return tuple(importlib.import_module(name) for name in (
        "install", "manage", "tunnel", "kaggle_secrets_loader"
    ))


def environment_issues(installer, secrets=None):
    issues = []
    environment = {**os.environ, **(secrets or {})}
    for name, system_packages in (
        ("backend_env", False),
        ("api_env", True),
    ):
        directory = installer.WORK_ROOT / name
        if installer.environment_state(directory, system_site_packages=system_packages) != "healthy":
            issues.append(f"{name}: missing or incomplete")
            continue
        python = installer.python_path(directory)
        critical, external = installer.validate_runtime(python, api=name == "api_env", environment=environment)
        issues.extend(f"{name}: {problem}" for problem in critical)
        for conflict in external:
            print(f"{name}: Kaggle system package conflict (non-blocking): {conflict}")
    return issues


def ensure_environments(installer, management, secrets):
    issues = environment_issues(installer, secrets)
    if not issues:
        print("Environments: reusing verified Backend and API environments")
        return
    if management.live_pid("api") or management.live_pid("backend"):
        raise RuntimeError("Environments need repair while services are running; stop services manually first")
    print("Environments: installing or repairing:", "; ".join(issues))
    log = installer.WORK_ROOT / "bootstrap_install.log"
    output = run(
        [sys.executable, REPOSITORY / KAGGLE_DIRECTORY / "install.py"],
        cwd=REPOSITORY / KAGGLE_DIRECTORY, environment={**os.environ, **secrets},
        secrets=secrets, log=log, timeout=3600,
    )
    print(output[-600:])
    remaining = environment_issues(installer, secrets)
    if remaining:
        raise RuntimeError(f"Environment verification failed: {'; '.join(remaining)}; log={log}")


def ensure_cloudflared(work_root, tunnel):
    if tunnel.live_pid() or shutil.which("cloudflared"):
        return
    target = work_root / "cloudflared"
    if target.is_file():
        if hashlib.sha256(target.read_bytes()).hexdigest() != CLOUDFLARED_SHA256:
            raise RuntimeError(f"Existing cloudflared checksum mismatch; inspect {target}")
        return
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError("Automatic cloudflared install supports Linux amd64 only")
    work_root.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".download")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(CLOUDFLARED_URL, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != CLOUDFLARED_SHA256:
            raise RuntimeError("cloudflared SHA256 mismatch; refusing to execute download")
        temporary.chmod(0o755)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"cloudflared: installed verified {CLOUDFLARED_VERSION} binary")


def health(url, *, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = json.load(response)
    data = body.get("data", body)
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise RuntimeError(f"Health status is not ok: {url}")
    return data


def public_health(url, attempts=5):
    for attempt in range(attempts):
        try:
            health(url + "/health", timeout=15)
            return True
        except (OSError, ValueError, RuntimeError):
            if attempt + 1 < attempts:
                time.sleep(3)
    return False


def bootstrap(update=False):
    summary = {
        "Repository / Branch": "not ready",
        "Backend": "not ready", "API Service": "not ready",
        "Database": "not ready", "Tunnel": "not ready",
        "Public Backend URL": "-",
        "Frontend variable": "NEXT_PUBLIC_API_URL (set to verified public URL)",
    }
    installer = management = tunnel = None
    secrets = {}
    failure = None
    try:
        branch = ensure_repository(update=update)
        summary["Repository / Branch"] = f"{REPOSITORY} / {branch}"
        installer, management, tunnel, loader = modules()
        secrets = loader.load_secrets(REQUIRED_SECRETS + ("FRONTEND_URL",))
        management.validate_config(secrets)
        ensure_environments(installer, management, secrets)
        directory = REPOSITORY / KAGGLE_DIRECTORY
        environment = os.environ.copy()
        environment.update(secrets)
        log = installer.WORK_ROOT / "bootstrap_manage.log"
        run([sys.executable, directory / "manage.py", "start"], cwd=directory, environment=environment, secrets=secrets, log=log, timeout=600)
        print("Services: start/reuse completed")
        print(redact(run([sys.executable, directory / "manage.py", "status"], cwd=directory, environment=environment, secrets=secrets, log=log), secrets))
        run([sys.executable, directory / "manage.py", "check"], cwd=directory, environment=environment, secrets=secrets, log=log)
        health(management.API_URL + "/health")
        health(management.BACKEND_URL + "/health")
        health(management.BACKEND_URL + "/health/db")
        summary["API Service"] = "healthy"
        summary["Backend"] = "healthy"
        summary["Database"] = "healthy (Neon SELECT 1)"
        ensure_cloudflared(installer.WORK_ROOT, tunnel)
        tunnel_log = installer.WORK_ROOT / "bootstrap_tunnel.log"
        tunnel_environment = {name: value for name, value in os.environ.items() if name not in REQUIRED_SECRETS}
        run([sys.executable, directory / "tunnel.py", "start"], cwd=directory, environment=tunnel_environment, secrets=secrets, log=tunnel_log, timeout=120)
        url = tunnel.public_url()
        if not url:
            raise RuntimeError(f"Tunnel produced no live public URL; inspect {tunnel.LOG_FILE}")
        summary["Public Backend URL"] = url
        if public_health(url):
            summary["Tunnel"] = "healthy (public /health verified)"
            summary["Frontend variable"] = f"NEXT_PUBLIC_API_URL={url}"
        else:
            summary["Tunnel"] = "started, public /health NOT VERIFIED"
            raise RuntimeError(f"Public /health could not be verified; inspect {tunnel.LOG_FILE} and Kaggle network access")
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        failure = redact(str(error), secrets)
    finally:
        if management is not None:
            for name, url, label in (
                ("api", management.API_URL + "/health", "API Service"),
                ("backend", management.BACKEND_URL + "/health", "Backend"),
            ):
                try:
                    if management.live_pid(name):
                        health(url)
                        summary[label] = "healthy"
                except (OSError, ValueError, RuntimeError):
                    pass
            if summary["Backend"] == "healthy":
                try:
                    health(management.BACKEND_URL + "/health/db")
                    summary["Database"] = "healthy (Neon SELECT 1)"
                except (OSError, ValueError, RuntimeError):
                    pass
            if summary["Backend"] != "healthy" and summary["Tunnel"].startswith("healthy"):
                summary["Tunnel"] = "not ready (backend unavailable)"
                summary["Frontend variable"] = "NEXT_PUBLIC_API_URL (not ready)"
        print("\nKaggle startup summary")
        for label, value in summary.items():
            print(f"  {label}: {value}")
        if failure:
            print(f"Startup failed: {failure}", file=sys.stderr)
            if installer is not None:
                print(f"Logs: {installer.WORK_ROOT / 'bootstrap_install.log'}, {installer.WORK_ROOT / 'bootstrap_manage.log'}, {installer.WORK_ROOT / 'api.log'}, {installer.WORK_ROOT / 'backend.log'}, {installer.WORK_ROOT / 'tunnel.log'}", file=sys.stderr)
    return not failure


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="Fast-forward clean checkouts only; never reset local changes")
    arguments = parser.parse_args()
    sys.exit(0 if bootstrap(update=arguments.update) else 1)
