"""Install two isolated service environments in Kaggle working storage."""

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
import venv
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
BACKEND_ROOT = REPO_ROOT / "project_backend"
WORK_ROOT = Path(os.environ.get("KAGGLE_RUNTIME_DIR", "/kaggle/working/ocr_runtime"))


def python_path(environment):
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(arguments):
    subprocess.run(arguments, check=True)


def version_report(python):
    code = """import importlib.metadata as m, json, sys
names = ('torch', 'paddlepaddle', 'paddleocr', 'paddlex', 'transformers', 'fastapi', 'pydantic', 'numpy', 'opencv-python-headless')
found = {}
for name in names:
    try: found[name] = m.version(name)
    except m.PackageNotFoundError: found[name] = None
print(json.dumps({'python': sys.version.split()[0], 'packages': found}))"""
    output = subprocess.check_output([str(python), "-c", code], text=True)
    return json.loads(output)


def install():
    if not (BACKEND_ROOT / "main.py").exists():
        raise SystemExit("Expected project_backend/main.py next to API_Service_Kaggle")
    print("Base environment:", json.dumps(version_report(Path(sys.executable)), sort_keys=True))
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    backend_env = WORK_ROOT / "backend_env"
    api_env = WORK_ROOT / "api_env"
    if not python_path(backend_env).exists():
        venv.EnvBuilder(with_pip=True).create(backend_env)
    if not python_path(api_env).exists():
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(api_env)

    backend_python = python_path(backend_env)
    api_python = python_path(api_env)
    run([str(backend_python), "-m", "pip", "install", "-r", str(BACKEND_ROOT / "requirements.txt")])

    installed = version_report(api_python)["packages"]
    requirements = (SERVICE_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    heavy = {"torch", "paddlepaddle"}
    filtered = []
    skipped = {}
    for line in requirements:
        package = line.split("[", 1)[0].split("==", 1)[0].strip().lower()
        if package in heavy and installed.get(package):
            skipped[package] = installed[package]
        elif line.strip() and not line.lstrip().startswith("#"):
            filtered.append(line.strip())
    print("Reusing visible heavy packages:", json.dumps(skipped, sort_keys=True))
    if filtered:
        run([str(api_python), "-m", "pip", "install", *filtered])
    print("Backend environment:", json.dumps(version_report(backend_python), sort_keys=True))
    print("API environment:", json.dumps(version_report(api_python), sort_keys=True))
    run([str(backend_python), "-m", "pip", "check"])
    api_check = subprocess.run([str(api_python), "-m", "pip", "check"], check=False)
    if api_check.returncode:
        print("API pip check found dependency conflicts; inspect the report before real inference")
    run([str(api_python), "-c", "import torch, paddle, paddleocr, transformers, fastapi; print('API imports: ok')"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.report_only:
        print(json.dumps(version_report(Path(sys.executable)), indent=2, sort_keys=True))
    else:
        install()
