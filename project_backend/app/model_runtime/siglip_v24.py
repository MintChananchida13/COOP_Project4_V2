"""Load the same pure policy shipped with the repository's API Service."""

import importlib.util
import os
from pathlib import Path
import sys

_name = "coop_shared_siglip_v24"
if _name not in sys.modules:
    _path = Path(__file__).resolve().parents[3] / "API_Service_Kaggle/app/siglip_policy.py"
    _spec = importlib.util.spec_from_file_location(_name, _path)
    if _spec is None or _spec.loader is None:
        raise ImportError("Shared SigLIP V2.4 policy is missing from the repository")
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    sys.modules[_name] = _module
policy = sys.modules[_name]
SCORING_VERSION = policy.SCORING_VERSION


def gate_config():
    values = {}
    for key, environment in (
        ("confidence_min_relative_score", "SIGLIP_V24_CONFIDENCE_MIN_RELATIVE_SCORE"),
        ("margin_min_relative_score", "SIGLIP_V24_MARGIN_MIN_RELATIVE_SCORE"),
    ):
        value = os.getenv(environment, "").strip()
        values[key] = float(value) if value else None
    return policy.normalize_config(values)
