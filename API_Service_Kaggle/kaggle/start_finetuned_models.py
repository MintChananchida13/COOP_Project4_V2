"""Clone if needed and start the Kaggle stack with fine-tuned weights.

Run from a Kaggle notebook or shell. The weights must be available separately
when they are not part of the cloned repository.
The normal bootstrap still handles secrets, environments and the tunnel.
"""

import os
import subprocess
import sys
from pathlib import Path


def main():
    repo = Path("/kaggle/working/COOP_Project4_Kaggle")
    if not repo.exists():
        subprocess.run([
            "git", "clone", "--branch", "kaggle-cpu-runtime", "--single-branch",
            "https://github.com/MintChananchida13/COOP_Project4_V2.git", str(repo),
        ], check=True)
    kaggle_dir = repo / "API_Service_Kaggle" / "kaggle"
    weights_root = Path(os.environ.get("MODEL_WEIGHTS_ROOT", str(repo / "API_Service_Kaggle" / "weights")))
    if not (kaggle_dir / "bootstrap.py").is_file() or not (kaggle_dir / "manage.py").is_file():
        raise SystemExit(f"Kaggle startup scripts not found: {kaggle_dir}")
    if not weights_root.is_dir():
        print(f"Weights directory not found: {weights_root}; fine-tuned models will be unavailable.", flush=True)

    models = {
        "detection thai_ft_v2": weights_root / "det-v6/finetuneV/infer",
        "recognition thai_ft_v3": weights_root / "rec-th/finetuneHv3/infer",
        "recognition thai_ft_v4": weights_root / "testdataset6_best",
        "recognition thai_ft_v5": weights_root / "testdataset9",
        "recognition thai_ft_v1": weights_root / "rec-v6/finetuneH/infer",
    }
    for name, directory in models.items():
        missing = [file for file in ("inference.yml", "inference.pdiparams") if not (directory / file).is_file()]
        if not any((directory / file).is_file() for file in ("inference.json", "inference.pdmodel")):
            missing.append("inference.json or inference.pdmodel")
        status = "files found" if not missing else "missing: " + ", ".join(missing)
        print(f"{name}: {status} ({directory})", flush=True)

    environment = os.environ.copy()
    environment["MODEL_WEIGHTS_ROOT"] = str(weights_root)
    print(f"MODEL_WEIGHTS_ROOT={weights_root}", flush=True)
    print("Restarting model service and backend using the existing Kaggle bootstrap...", flush=True)
    subprocess.run([sys.executable, str(kaggle_dir / "manage.py"), "stop"], env=environment, check=True)
    subprocess.run([sys.executable, str(kaggle_dir / "bootstrap.py")], env=environment, check=True)
    print("Startup finished. Use Admin > Manage OCR models > Check model to load and verify each model.", flush=True)


if __name__ == "__main__":
    main()
