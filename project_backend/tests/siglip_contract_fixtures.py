"""Synthetic logits evaluated by the real model-free API_Service scorer.

No service apps, model loaders, database or inference are imported or called.
"""
import ast
import math
from pathlib import Path

from app.model_runtime import siglip_image_verification_adapter as adapter
from app.model_runtime.siglip_v23 import SINGLE, NEGATIVE


def categories():
    return [{"value": value, "label": value, "prompt": prompt,
             "match_threshold": .5, "margin_threshold": .05} for value, prompt in SINGLE.items()]


def payloads(settings=None):
    return adapter._runtime_category_payloads(adapter._normalize_categories(settings or categories()))


def leaf(scores=None, settings=None):
    configured = payloads(settings)
    scores = scores or {}
    logits = [scores.get(item["value"], -20.0) for item in configured] if isinstance(scores, dict) else scores
    return {"contract_version": "leaf-inference-v1", "kind": "siglip",
            "raw_output": [{"logits_per_image": logits}],
            "categories": [item["prompt"] for item in configured], "device": "cpu"}


def batch(*items):
    return {"results": list(items), "count": len(items)}


def _reference_scorer():
    path = Path(__file__).resolve().parents[2] / "API_Service/pipelines/verification/scoring.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    names = {"_ui_percentages", "verify_classification"}
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"math": math, "Any": object, "ModelAPIError": ValueError}
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace["verify_classification"]


reference_score = _reference_scorer()


def decision(target="portrait", scores=None, settings=None):
    item = reference_score(target_value=target, categories=payloads(settings), classification=leaf(scores, settings))
    return {**item, "verifications": [item.copy()], "requested_categories": [target]}
