"""Model-free parsing/scoring for API_Service's current SigLIP contract.

Reference: pipelines/verification/scoring.py and shared/inference_adapters.py
in API_Service. Binary evidence, sigmoid pair scores and softmax display scores
have distinct meanings. V2.4 gates/tolerance do not apply to binary-v3.
"""
from __future__ import annotations

import math
from typing import Any

SCORING_VERSION = "siglip-image-category-binary-v3"


def number(value: Any, field: str, low=None, high=None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    try:
        parsed = float(value)
    except OverflowError as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be a finite number")
    if (low is not None and parsed < low) or (high is not None and parsed > high):
        raise ValueError(f"{field} is out of range")
    return parsed


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing/invalid {key}")
    return value


def _object(data: Any) -> dict:
    if not isinstance(data, dict) or data.get("error") is not None:
        raise ValueError("Invalid SigLIP response or upstream error")
    return data


def classification_logits(data: Any, categories: list[dict]) -> list[float]:
    data = _object(data)
    if data.get("contract_version") != "leaf-inference-v1" or data.get("kind") != "siglip":
        raise ValueError("Unsupported SigLIP leaf contract")
    # Prompt order is part of the vector contract; never infer it from scores.
    if data.get("categories") != [item["prompt"] for item in categories]:
        raise ValueError("SigLIP category/prompt order does not match request")
    raw = data.get("raw_output")
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ValueError("Expected one raw_output item per image")
    logits = raw[0].get("logits_per_image")
    if not isinstance(logits, list) or len(logits) != len(categories):
        raise ValueError("SigLIP logit/category count mismatch")
    _text(data, "device")
    return [number(value, "logits_per_image") for value in logits]


def score_classification(data: dict, target: str, categories: list[dict]) -> dict:
    """Mirror verify_classification's raw-logit path, including stable tie order."""
    logits = classification_logits(data, categories)
    if target not in {item["value"] for item in categories}:
        raise ValueError("Target is not in request categories")
    indices = sorted(range(len(logits)), key=lambda index: logits[index], reverse=True)
    top_logit = logits[indices[0]]
    weights = [math.exp(value - top_logit) for value in logits]
    total = sum(weights)
    relative = [value / total for value in weights]
    labels = []
    for rank, index in enumerate(indices, 1):
        item, logit = categories[index], logits[index]
        pair = 1 / (1 + math.exp(-logit)) if logit >= 0 else math.exp(logit) / (1 + math.exp(logit))
        labels.append({
            "rank": rank, "image_category": item["value"], "value": item["value"],
            "label": item.get("label") or item["value"], "prompt": item["prompt"],
            "raw_logit": round(logit, 4), "raw_pair_score": round(pair, 4), "score": round(pair, 6),
            "relative_score": round(relative[index], 4), "relative_percentage": round(relative[index] * 100, 2),
            "field_score": float(rank == 1), "evidence_score": float(rank == 1),
            "match_threshold": round(item.get("match_threshold", .7), 4),
            "margin_threshold": round(item.get("margin_threshold", .05), 4),
            "target": item["value"] == target,
        })
    selected = next(item for item in labels if item["image_category"] == target)
    target_index = next(index for index, item in enumerate(categories) if item["value"] == target)
    target_config = categories[target_index]
    passed = selected["rank"] == 1
    second_logit = logits[indices[1]] if len(indices) > 1 else top_logit
    margin = top_logit - second_logit if passed else logits[target_index] - top_logit
    number(margin, "score_margin")  # finite inputs can still overflow on subtraction
    # Service uses the rounded relative_score in labels to construct UI values.
    ui = [{"image_category": item["image_category"], "label": item["label"], "prompt": item["prompt"],
           "percentage": round(max(0, item["relative_score"]) * 100, 1)} for item in labels]
    delta = round(100 - sum(item["percentage"] for item in ui), 1)
    best = max(range(len(ui)), key=lambda index: ui[index]["percentage"])
    ui[best]["percentage"] = round(ui[best]["percentage"] + delta, 1)
    return {
        "passed": passed, "status": "matched" if passed else "mismatched",
        "failure_reason": "passed" if passed else "predicted_category_mismatch",
        "image_category": target, "image_category_label": selected["label"], "prompt": selected["prompt"],
        "predicted_category": labels[0]["image_category"], "predicted_label": labels[0]["label"],
        "predicted_prompt": labels[0]["prompt"], "score": float(passed), "evidence_score": float(passed),
        "target_rank": selected["rank"], "score_margin": round(margin, 6),
        "raw_logit": selected["raw_logit"], "raw_pair_score": selected["raw_pair_score"],
        "relative_percentage": selected["relative_percentage"],
        "verification_threshold": target_config.get("match_threshold", .7),
        "margin_threshold": target_config.get("margin_threshold", .05),
        # Batch leaf data does not identify model name/version. Do not invent it
        # or use the gateway router name as the inference model's identity.
        "model_name": data.get("model_name", ""), "model_version": data.get("model_version", ""),
        "scoring_version": SCORING_VERSION, "version": SCORING_VERSION, "device": data["device"],
        "labels": labels, "ui_percentages": ui, "error": None,
    }


def parse_decision(data: Any, target: str, categories: list[dict]) -> dict:
    """Validate and consume a service decision without re-scoring rounded logits."""
    data = _object(data)
    if "verifications" in data:
        if data.get("requested_categories") != [target]:
            raise ValueError("SigLIP requested target mismatch")
        results = data["verifications"]
        if not isinstance(results, list) or len(results) != 1:
            raise ValueError("Expected exactly one target verification")
        selected = _object(results[0])
        if any(data.get(key) != value for key, value in selected.items()):
            raise ValueError("SigLIP selected decision and verification disagree")
        data = selected
    registry = {item["value"]: item for item in categories}
    if data.get("image_category") != target or target not in registry:
        raise ValueError("SigLIP target category mismatch")
    if data.get("scoring_version") != SCORING_VERSION or data.get("version") != SCORING_VERSION:
        raise ValueError("Unsupported SigLIP decision scoring version")
    labels = data.get("labels")
    if not isinstance(labels, list) or len(labels) != len(categories):
        raise ValueError("Incomplete SigLIP category scores")
    seen = set()
    previous_logit = math.inf
    for rank, item in enumerate(labels, 1):
        item = _object(item)
        name = item.get("image_category")
        if not isinstance(name, str) or name not in registry or name in seen:
            raise ValueError("Unknown/duplicate SigLIP result category")
        seen.add(name)
        if item.get("rank") != rank or isinstance(item.get("rank"), bool):
            raise ValueError("Invalid SigLIP ranking")
        if item.get("prompt") != registry[name]["prompt"] or item.get("value") != name:
            raise ValueError("SigLIP result category mapping mismatch")
        if item.get("target") is not (name == target):
            raise ValueError("SigLIP target flag mismatch")
        _text(item, "label")
        raw_logit = number(item.get("raw_logit"), "label.raw_logit")
        if raw_logit > previous_logit:
            raise ValueError("SigLIP ranking contradicts raw logits")
        previous_logit = raw_logit
        for key in ("raw_pair_score", "score", "relative_score"):
            number(item.get(key), "label." + key, 0, 1)
        number(item.get("relative_percentage"), "label.relative_percentage", 0, 100)
        for key in ("match_threshold", "margin_threshold"):
            number(item.get(key), "label." + key, 0, 1)
        for key in ("field_score", "evidence_score"):
            if number(item.get(key), "label." + key, 0, 1) != float(rank == 1):
                raise ValueError("Invalid SigLIP label evidence")
    selected = next(item for item in labels if item["image_category"] == target)
    passed = selected["rank"] == 1
    if data.get("passed") is not passed or type(data.get("target_rank")) is not int or data["target_rank"] != selected["rank"]:
        raise ValueError("SigLIP decision/rank mismatch")
    if data.get("predicted_category") != labels[0]["image_category"]:
        raise ValueError("SigLIP predicted category mismatch")
    if data.get("status") != ("matched" if passed else "mismatched") or data.get("failure_reason") != ("passed" if passed else "predicted_category_mismatch"):
        raise ValueError("SigLIP decision status mismatch")
    for key in ("score", "evidence_score"):
        if number(data.get(key), key, 0, 1) != float(passed):
            raise ValueError("Invalid binary SigLIP evidence")
    for key, low, high in (("raw_logit", None, None), ("raw_pair_score", 0, 1), ("relative_percentage", 0, 100)):
        number(data.get(key), key, low, high)
        if data[key] != selected[key]:
            raise ValueError("SigLIP target score mismatch")
    number(data.get("score_margin"), "score_margin")
    for key in ("verification_threshold", "margin_threshold"):
        number(data.get(key), key, 0, 1)
    for key, label in (("image_category_label", selected), ("predicted_label", labels[0])):
        if data.get(key) != label["label"]:
            raise ValueError("SigLIP label mismatch")
    if data.get("prompt") != selected["prompt"] or data.get("predicted_prompt") != labels[0]["prompt"]:
        raise ValueError("SigLIP decision prompt mismatch")
    ui = data.get("ui_percentages")
    if not isinstance(ui, list) or len(ui) != len(categories):
        raise ValueError("Missing SigLIP UI percentages")
    for item, label in zip(ui, labels):
        item = _object(item)
        if any(item.get(key) != label[key] for key in ("image_category", "label", "prompt")):
            raise ValueError("SigLIP UI category mismatch")
        number(item.get("percentage"), "ui.percentage", 0, 100)
    for key in ("model_name", "model_version", "device"):
        _text(data, key)
    keys = ("evidence_score", "score", "passed", "status", "failure_reason", "image_category",
            "image_category_label", "prompt", "predicted_category", "predicted_label", "predicted_prompt",
            "target_rank", "score_margin", "raw_logit", "raw_pair_score", "relative_percentage",
            "verification_threshold", "margin_threshold", "model_name", "model_version", "scoring_version",
            "version", "device", "labels", "ui_percentages")
    return {key: data[key] for key in keys}
