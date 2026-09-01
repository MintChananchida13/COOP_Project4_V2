import os
from pathlib import Path


def load_local_env() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    repo_root = backend_root.parent
    for path in (repo_root / ".env.local", backend_root / ".env.local"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
