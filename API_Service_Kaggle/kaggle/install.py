"""Preflight and install isolated Kaggle Python 3.13 CPU environments."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
BACKEND_ROOT = REPO_ROOT / "project_backend"
WORK_ROOT = Path(os.environ.get("KAGGLE_RUNTIME_DIR", "/kaggle/working/ocr_runtime"))
OVERRIDES = Path(__file__).with_name("py313_overrides.json")
REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)(\[[^]]+\])?\s*([<>=!~].*)?$")
CORE_API = {"paddlepaddle", "paddleocr", "torch"}


def python_path(environment):
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(arguments, **kwargs):
    return subprocess.run(arguments, check=True, **kwargs)


def pip_available(python):
    return subprocess.run(
        [str(python), "-m", "pip", "--version"],
        capture_output=True,
        text=True,
        check=False,
    ).returncode == 0


def environment_state(environment, *, system_site_packages):
    if not environment.exists():
        return "missing"
    config = environment / "pyvenv.cfg"
    python = python_path(environment)
    if not config.is_file() or not python.is_file():
        return "incomplete"
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
        settings = {key.strip(): value.strip() for line in lines if "=" in line for key, value in [line.split("=", 1)]}
        enabled = settings.get("include-system-site-packages", "").strip().lower() == "true"
        if enabled != system_site_packages or not pip_available(python):
            return "incomplete"
        python_version = version_report(python)["python"]
        if tuple(map(int, python_version.split(".")[:2])) != (3, 13):
            return "incomplete"
    except (OSError, ValueError, subprocess.CalledProcessError):
        return "incomplete"
    return "healthy"


def ensure_environment(environment, *, system_site_packages):
    state = environment_state(environment, system_site_packages=system_site_packages)
    if state == "healthy":
        print(f"Reusing complete environment: {environment}")
        return python_path(environment)
    if state == "incomplete":
        root = WORK_ROOT.resolve()
        target = environment.resolve()
        if target.parent != root or environment.name not in {"backend_env", "api_env"}:
            raise RuntimeError(f"Refusing to remove environment outside runtime directory: {environment}")
        print(f"Recreating incomplete environment: {environment}")
        if environment.is_dir():
            shutil.rmtree(environment)
        else:
            environment.unlink()
    command = [sys.executable, "-m", "virtualenv", "--python", sys.executable]
    if system_site_packages:
        command.append("--system-site-packages")
    run(command + [str(environment)])
    python = python_path(environment)
    if environment_state(environment, system_site_packages=system_site_packages) != "healthy":
        raise RuntimeError(f"virtualenv did not create a complete environment: {environment}")
    print(f"pip available in {environment}")
    return python


def version_report(python):
    code = """import importlib.metadata as metadata, json, sys
names = ('torch', 'paddlepaddle', 'paddleocr', 'paddlex', 'transformers', 'fastapi', 'uvicorn', 'pydantic', 'numpy', 'opencv-python-headless', 'Pillow')
found = {}
for name in names:
    try: found[name] = metadata.version(name)
    except metadata.PackageNotFoundError: found[name] = None
print(json.dumps({'python': sys.version.split()[0], 'packages': found}))"""
    output = subprocess.check_output([str(python), "-c", code], text=True)
    return json.loads(output)


def requirements(path, overrides):
    parsed = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = REQUIREMENT.fullmatch(line)
        if not match:
            raise ValueError(f"Unsupported requirement in {path.name}: {line}")
        name, extra, original_specifier = match.groups()
        key = name.lower().replace("_", "-")
        original_specifier = (original_specifier or "").strip()
        version = overrides.get(key)
        specifier = f"=={version}" if version else original_specifier
        if version is None and original_specifier.startswith("=="):
            version = original_specifier[2:].strip()
        specification = name + (extra or "") + specifier
        parsed.append({"name": key, "original": line, "specification": specification, "version": version})
    unknown = set(overrides) - {item["name"] for item in parsed}
    if unknown:
        raise ValueError(f"Overrides do not match requirements: {sorted(unknown)}")
    return parsed


def smoke(python, code, *, cwd=None):
    result = subprocess.run([str(python), "-c", code], cwd=cwd, text=True, capture_output=True)
    return result.returncode == 0, (result.stderr.strip() or result.stdout.strip())[-1200:]


OCR_SMOKE = """import paddle, paddleocr, torch
from paddleocr import LayoutDetection, TableRecognitionPipelineV2, TextDetection, TextRecognition
assert paddle.get_device().startswith('cpu')
"""

SIGLIP_SMOKE = """import torch
from transformers import AutoProcessor, SiglipConfig, SiglipModel
config = SiglipConfig(
    text_config={'vocab_size': 32, 'hidden_size': 32, 'intermediate_size': 64, 'num_hidden_layers': 1, 'num_attention_heads': 4, 'max_position_embeddings': 8, 'projection_size': 32},
    vision_config={'hidden_size': 32, 'intermediate_size': 64, 'num_hidden_layers': 1, 'num_attention_heads': 4, 'image_size': 16, 'patch_size': 8, 'num_channels': 3, 'projection_size': 32},
)
model = SiglipModel(config).eval()
with torch.inference_mode():
    output = model(input_ids=torch.ones((1, 8), dtype=torch.long), pixel_values=torch.zeros((1, 3, 16, 16)))
assert tuple(output.logits_per_image.shape) == (1, 1)
assert callable(AutoProcessor.from_pretrained)
"""

BACKEND_SMOKE = """import cv2, numpy, fastapi, pydantic, fitz, psycopg2, pythainlp, bs4, lxml
import main
"""


def preflight(python=Path(sys.executable)):
    if not (BACKEND_ROOT / "main.py").exists():
        raise RuntimeError("Expected project_backend/main.py next to API_Service_Kaggle")
    report = version_report(python)
    if tuple(map(int, report["python"].split(".")[:2])) != (3, 13):
        raise RuntimeError(f"This Kaggle override targets Python 3.13; found {report['python']}")
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    backend = requirements(BACKEND_ROOT / "requirements.txt", overrides["backend"])
    api = requirements(SERVICE_ROOT / "requirements.txt", overrides["api"])
    packages = report["packages"]
    ocr_ok, ocr_detail = smoke(python, OCR_SMOKE)
    siglip_ok, siglip_detail = smoke(python, SIGLIP_SMOKE) if packages.get("transformers") else (False, "Transformers absent")
    print("Base Python:", report["python"])
    print("Base packages:", json.dumps(packages, sort_keys=True))
    print("OCR import smoke:", "PASS" if ocr_ok else f"FAIL ({ocr_detail})")
    print("SigLIP API smoke:", "PASS" if siglip_ok else f"FAIL ({siglip_detail})")
    print("Backend environment: isolated; effective requirements:")
    for item in backend:
        label = "override" if item["original"] != item["specification"] else "install"
        print(f"  {label}: {item['original']} -> {item['specification']}")
    api_install = []
    reused = {}
    print("API environment: system packages visible; planned actions:")
    for item in api:
        name = item["name"]
        installed = packages.get(name)
        can_reuse = (
            (name in CORE_API and installed and ocr_ok)
            or (name == "transformers" and installed and siglip_ok)
            or (name == "numpy" and installed == item["version"])
        )
        if can_reuse and name != "paddleocr":
            reused[name] = installed
            print(f"  reuse: {name}=={installed}")
        else:
            api_install.append(item["specification"])
            action = "ensure extras" if name == "paddleocr" and installed == item["version"] and ocr_ok else "install/change"
            print(f"  {action}: {item['specification']} (existing: {installed or 'absent'})")
    if packages.get("transformers") and not siglip_ok:
        print("Transformers is not assumed compatible; API requirement pin will be installed")
    print("Actual checkpoint/processor compatibility requires real SigLIP inference after installation")
    return backend, api_install, reused


def install():
    backend, api_install, reused = preflight()
    if not pip_available(Path(sys.executable)):
        raise RuntimeError("Base Python has no pip; run /usr/bin/python3 -m pip --version")
    if subprocess.run([sys.executable, "-m", "virtualenv", "--version"], capture_output=True, check=False).returncode:
        print("Installing virtualenv with base pip")
        run([sys.executable, "-m", "pip", "install", "virtualenv"])
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    backend_env = WORK_ROOT / "backend_env"
    api_env = WORK_ROOT / "api_env"
    backend_python = ensure_environment(backend_env, system_site_packages=False)
    api_python = ensure_environment(api_env, system_site_packages=True)

    run([str(backend_python), "-m", "pip", "install", *(item["specification"] for item in backend)])
    run([str(backend_python), "-m", "pip", "check"])
    backend_ok, backend_detail = smoke(backend_python, BACKEND_SMOKE, cwd=BACKEND_ROOT)
    if not backend_ok:
        raise RuntimeError(f"Backend import smoke failed: {backend_detail}")

    constraints = WORK_ROOT / "reused_constraints.txt"
    constraints.write_text("".join(f"{name}=={version}\n" for name, version in reused.items()), encoding="utf-8")
    if api_install:
        command = [str(api_python), "-m", "pip", "install"]
        if reused:
            command += ["-c", str(constraints)]
        run(command + api_install)
    run([str(api_python), "-m", "pip", "check"])
    ocr_ok, ocr_detail = smoke(api_python, OCR_SMOKE)
    if not ocr_ok:
        raise RuntimeError(f"API OCR import smoke failed: {ocr_detail}")
    siglip_ok, siglip_detail = smoke(api_python, SIGLIP_SMOKE)
    if not siglip_ok:
        raise RuntimeError(f"API SigLIP API smoke failed: {siglip_detail}")
    print("Backend environment:", json.dumps(version_report(backend_python), sort_keys=True))
    print("API environment:", json.dumps(version_report(api_python), sort_keys=True))
    print("Install and import checks passed; real models remain untested by this script")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-only", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.report_only:
            preflight()
        else:
            install()
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        sys.exit(1)
