"""SigLIP V2.4 policy configuration."""

import os
from app.model_runtime import siglip_policy as policy

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
