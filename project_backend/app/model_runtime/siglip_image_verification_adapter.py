from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.model_runtime_client import (
    ModelRuntimeKind,
    ModelRuntimeUnavailableError,
    is_runtime_configured,
    remote_verify_image_logits,
)


SIGLIP_MODEL_NAME = "google/siglip-so400m-patch14-384"
SIGLIP_MODEL_VERSION = "google/siglip-so400m-patch14-384"
SIGLIP_SCORING_VERSION = "siglip-image-category-binary-v3"

@dataclass(frozen=True)
class SiglipCategoryConfig:
    value: str
    label: str
    prompt: str
    match_threshold: float = 0.70
    margin_threshold: float = 0.05
    evidence_temperature: float = 1.0
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "SiglipCategoryConfig":
        return cls(
            value=str(value.get("value") or "").strip(),
            label=str(value.get("label") or "").strip(),
            prompt=str(value.get("prompt") or "").strip(),
            match_threshold=_clamp01(value.get("match_threshold"), 0.70),
            margin_threshold=_clamp01(value.get("margin_threshold"), 0.05),
            evidence_temperature=max(0.01, _as_float(value.get("evidence_temperature"), 1.0)),
            enabled=bool(value.get("enabled", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "label": self.label,
            "prompt": self.prompt,
            "match_threshold": self.match_threshold,
            "margin_threshold": self.margin_threshold,
            "evidence_temperature": self.evidence_temperature,
            "enabled": self.enabled,
        }


@dataclass
class SiglipImageVerificationResult:
    evidence_score: float
    score: float
    passed: bool
    status: str
    failure_reason: str
    image_category: str
    image_category_label: str
    prompt: str
    predicted_category: str
    predicted_label: str
    predicted_prompt: str
    target_rank: int
    score_margin: float
    raw_logit: float
    raw_pair_score: float
    relative_percentage: float
    verification_threshold: float
    margin_threshold: float
    model_name: str
    model_version: str
    scoring_version: str
    version: str
    device: str
    labels: List[Dict[str, Any]]
    ui_percentages: List[Dict[str, Any]]
    error: Optional[str] = None


def _as_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(parsed) or math.isinf(parsed):
        return fallback
    return parsed


def _clamp01(value: Any, fallback: float) -> float:
    return max(0.0, min(1.0, _as_float(value, fallback)))


def _normalize_categories(categories: List[Dict[str, Any]] | List[SiglipCategoryConfig]) -> List[SiglipCategoryConfig]:
    normalized: List[SiglipCategoryConfig] = []
    seen = set()
    for item in categories:
        category = item if isinstance(item, SiglipCategoryConfig) else SiglipCategoryConfig.from_dict(item)
        if not category.enabled:
            continue
        if not category.value or not category.prompt:
            continue
        if category.value == "other":
            continue
        if category.value in seen:
            continue
        seen.add(category.value)
        normalized.append(category)
    return normalized


def _empty_result(
    image_category: str,
    categories: List[SiglipCategoryConfig],
    status: str,
    failure_reason: str,
    error: Optional[str] = None,
    device: str = "unavailable",
) -> SiglipImageVerificationResult:
    target = next((category for category in categories if category.value == image_category), None)
    return SiglipImageVerificationResult(
        evidence_score=0.0,
        score=0.0,
        passed=False,
        status=status,
        failure_reason=failure_reason,
        image_category=image_category,
        image_category_label=target.label if target else image_category,
        prompt=target.prompt if target else "",
        predicted_category="",
        predicted_label="",
        predicted_prompt="",
        target_rank=0,
        score_margin=0.0,
        raw_logit=0.0,
        raw_pair_score=0.0,
        relative_percentage=0.0,
        verification_threshold=target.match_threshold if target else 0.0,
        margin_threshold=target.margin_threshold if target else 0.0,
        model_name=SIGLIP_MODEL_NAME,
        model_version=SIGLIP_MODEL_VERSION,
        scoring_version=SIGLIP_SCORING_VERSION,
        version=SIGLIP_SCORING_VERSION,
        device=device,
        labels=[],
        ui_percentages=[],
        error=error,
    )


def _rounded_relative_percentages(known: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rounded = [
        {**item, "percentage": round(max(0.0, float(item.get("percentage") or 0.0)) * 100.0, 1)}
        for item in known
    ]
    delta = round(100.0 - sum(float(item["percentage"]) for item in rounded), 1)
    if rounded:
        best_index = max(range(len(rounded)), key=lambda index: rounded[index]["percentage"])
        rounded[best_index]["percentage"] = round(float(rounded[best_index]["percentage"]) + delta, 1)
    return rounded


def _result_from_logits(
    logits: List[float],
    image_category: str,
    categories: List[SiglipCategoryConfig],
    device: str,
) -> SiglipImageVerificationResult:
    if not categories:
        return _empty_result(image_category, categories, "error", "no_active_categories", "No active SigLIP image categories are configured.", device)
    if len(logits) != len(categories):
        return _empty_result(
            image_category,
            categories,
            "error",
            "logit_category_count_mismatch",
            f"SigLIP returned {len(logits)} logits for {len(categories)} categories.",
            device,
        )
    target_index = next((index for index, category in enumerate(categories) if category.value == image_category), -1)
    if target_index < 0:
        return _empty_result(image_category, categories, "error", "category_not_found", f"Image category not found: {image_category}", device)

    target_category = categories[target_index]
    exp_values = [math.exp(logit - max(logits)) for logit in logits]
    exp_total = sum(exp_values) or 1.0
    relative_scores = [value / exp_total for value in exp_values]
    pair_scores = [1.0 / (1.0 + math.exp(-logit)) for logit in logits]
    ranked_indices = sorted(range(len(categories)), key=lambda index: logits[index], reverse=True)
    top_index = ranked_indices[0]
    second_index = ranked_indices[1] if len(ranked_indices) > 1 else None
    target_pair_score = float(pair_scores[target_index])
    target_rank = ranked_indices.index(target_index) + 1
    top_logit = float(logits[top_index])
    second_logit = float(logits[second_index]) if second_index is not None else top_logit
    margin = round(top_logit - second_logit if target_rank == 1 else float(logits[target_index]) - top_logit, 4)

    top_category = categories[top_index]
    passed = target_rank == 1
    evidence = 1.0 if passed else 0.0
    if passed:
        status = "matched"
        failure_reason = "passed"
    else:
        status = "mismatched"
        failure_reason = "predicted_category_mismatch"

    labels: List[Dict[str, Any]] = []
    for rank, index in enumerate(ranked_indices, start=1):
        category = categories[index]
        relative_percentage = round(float(relative_scores[index]) * 100.0, 2)
        labels.append(
            {
                "rank": rank,
                "image_category": category.value,
                "label": category.label,
                "prompt": category.prompt,
                "raw_logit": round(float(logits[index]), 4),
                "raw_pair_score": round(float(pair_scores[index]), 4),
                "relative_score": round(float(relative_scores[index]), 4),
                "relative_percentage": relative_percentage,
                "field_score": 1.0 if index == top_index else 0.0,
                "evidence_score": 1.0 if index == top_index else 0.0,
                "match_threshold": round(float(category.match_threshold), 4),
                "margin_threshold": round(float(category.margin_threshold), 4),
                "target": index == target_index,
            }
        )

    ui_percentages = _rounded_relative_percentages([
        {
            "image_category": categories[index].value,
            "label": categories[index].label,
            "prompt": categories[index].prompt,
            "percentage": relative_scores[index],
        }
        for index in range(len(categories))
    ])
    return SiglipImageVerificationResult(
        evidence_score=evidence,
        score=evidence,
        passed=passed,
        status=status,
        failure_reason=failure_reason,
        image_category=target_category.value,
        image_category_label=target_category.label,
        prompt=target_category.prompt,
        predicted_category=top_category.value,
        predicted_label=top_category.label,
        predicted_prompt=top_category.prompt,
        target_rank=target_rank,
        score_margin=margin,
        raw_logit=round(float(logits[target_index]), 4),
        raw_pair_score=round(target_pair_score, 4),
        relative_percentage=round(float(relative_scores[target_index]) * 100.0, 2),
        verification_threshold=round(float(target_category.match_threshold), 4),
        margin_threshold=round(float(target_category.margin_threshold), 4),
        model_name=SIGLIP_MODEL_NAME,
        model_version=SIGLIP_MODEL_VERSION,
        scoring_version=SIGLIP_SCORING_VERSION,
        version=SIGLIP_SCORING_VERSION,
        device=device,
        labels=labels,
        ui_percentages=ui_percentages,
    )


def verify_image_category(
    image_path: str,
    image_category: Optional[str],
    categories: Optional[List[Dict[str, Any]]] = None,
) -> SiglipImageVerificationResult:
    category_value = str(image_category or "").strip()
    active_categories = _normalize_categories(categories or [])
    if not active_categories:
        return _empty_result(category_value, active_categories, "error", "no_active_categories", "No active SigLIP image categories are configured.")
    if not category_value:
        return _empty_result(category_value, active_categories, "error", "category_missing", "Image anchor category is required.")
    category = next((item for item in active_categories if item.value == category_value), None)
    if category is None:
        known_disabled = any((item.get("value") if isinstance(item, dict) else item.value) == category_value for item in (categories or []))
        reason = "category_disabled" if known_disabled else "category_not_found"
        return _empty_result(category_value, active_categories, "error", reason, f"Image category is not active: {category_value}")

    runtime_configured = is_runtime_configured(ModelRuntimeKind.IMAGE_VERIFICATION)
    if not runtime_configured:
        raise RuntimeError("GATEWAY_URL is not configured.")

    try:
        remote_result = remote_verify_image_logits(image_path, [item.to_dict() for item in active_categories])
    except ModelRuntimeUnavailableError as error:
        raise RuntimeError(f"SigLIP model runtime unavailable: {error}") from error

    if not isinstance(remote_result, dict):
        raise RuntimeError("SigLIP model runtime returned an invalid response.")

    logits = remote_result.get("logits")
    if not isinstance(logits, list):
        raise RuntimeError("SigLIP model runtime must return raw logits.")
    device = str(remote_result.get("device") or "remote")
    return _result_from_logits([_as_float(item, 0.0) for item in logits], category_value, active_categories, device)


def _result_from_remote(
    remote_result: Dict[str, Any],
    image_category: str,
    categories: List[SiglipCategoryConfig],
) -> SiglipImageVerificationResult:
    labels = list(remote_result.get("labels") or [])
    ui_percentages = list(remote_result.get("ui_percentages") or remote_result.get("display_percentages") or [])
    target = next((item for item in categories if item.value == image_category), None)
    return SiglipImageVerificationResult(
        evidence_score=round(float(remote_result.get("evidence_score") or remote_result.get("score") or 0.0), 4),
        score=round(float(remote_result.get("score") or remote_result.get("evidence_score") or 0.0), 4),
        passed=bool(remote_result.get("passed")),
        status=str(remote_result.get("status") or ("matched" if remote_result.get("passed") else "mismatched")),
        failure_reason=str(remote_result.get("failure_reason") or ("passed" if remote_result.get("passed") else "predicted_category_mismatch")),
        image_category=str(remote_result.get("image_category") or image_category),
        image_category_label=str(remote_result.get("image_category_label") or (target.label if target else image_category)),
        prompt=str(remote_result.get("prompt") or (target.prompt if target else "")),
        predicted_category=str(remote_result.get("predicted_category") or ""),
        predicted_label=str(remote_result.get("predicted_label") or ""),
        predicted_prompt=str(remote_result.get("predicted_prompt") or ""),
        target_rank=int(remote_result.get("target_rank") or 0),
        score_margin=round(float(remote_result.get("score_margin") or 0.0), 4),
        raw_logit=round(float(remote_result.get("raw_logit") or 0.0), 4),
        raw_pair_score=round(float(remote_result.get("raw_pair_score") or 0.0), 4),
        relative_percentage=round(float(remote_result.get("relative_percentage") or 0.0), 2),
        verification_threshold=round(float(remote_result.get("verification_threshold") or (target.match_threshold if target else 0.0)), 4),
        margin_threshold=round(float(remote_result.get("margin_threshold") or (target.margin_threshold if target else 0.0)), 4),
        model_name=str(remote_result.get("model_name") or SIGLIP_MODEL_NAME),
        model_version=str(remote_result.get("model_version") or SIGLIP_MODEL_VERSION),
        scoring_version=str(remote_result.get("scoring_version") or SIGLIP_SCORING_VERSION),
        version=str(remote_result.get("version") or SIGLIP_SCORING_VERSION),
        device=str(remote_result.get("device") or "remote"),
        labels=labels,
        ui_percentages=ui_percentages,
        error=remote_result.get("error"),
    )


def verify_image_category_from_logits(
    logits: List[float],
    image_category: str,
    categories: List[Dict[str, Any]],
    device: str = "test",
) -> SiglipImageVerificationResult:
    return _result_from_logits(logits, image_category, _normalize_categories(categories), device)
