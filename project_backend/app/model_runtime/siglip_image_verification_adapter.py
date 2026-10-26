from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.model_runtime_client import (
    ModelRuntimeKind,
    ModelRuntimeUnavailableError,
    is_runtime_configured,
    remote_verify_image_logits,
    remote_verify_image_logits_batch,
)
from app.model_runtime.siglip_v23 import LABELS, NEGATIVE, SCORING_VERSION, SINGLE, classify, runtime_categories
from app.model_runtime.siglip_v24 import SCORING_VERSION as V24_SCORING_VERSION, gate_config, policy


SIGLIP_MODEL_NAME = "google/siglip-so400m-patch14-384"
SIGLIP_MODEL_VERSION = "google/siglip-so400m-patch14-384"
SIGLIP_SCORING_VERSION = V24_SCORING_VERSION
if tuple(SINGLE) != policy.POSITIVE or tuple(NEGATIVE) != policy.NEGATIVE:
    raise RuntimeError("SigLIP prompt and V2.4 policy category ordering disagree")

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
    decision_rule: str = ""
    negative_scores: Optional[Dict[str, float]] = None
    positive_negative_margin: Optional[float] = None
    decision: Optional[Dict[str, Any]] = None


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
        if category.value not in SINGLE:
            continue
        if category.value in seen:
            continue
        seen.add(category.value)
        normalized.append(SiglipCategoryConfig(
            value=category.value, label=category.label or LABELS[category.value], prompt=SINGLE[category.value],
            match_threshold=category.match_threshold, margin_threshold=category.margin_threshold,
            evidence_temperature=category.evidence_temperature, enabled=category.enabled,
        ))
    return normalized


def _empty_result(
    image_category: str,
    categories: List[SiglipCategoryConfig],
    status: str,
    failure_reason: str,
    error: Optional[str] = None,
    device: str = "unavailable",
    scoring_version: str = SIGLIP_SCORING_VERSION,
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
        scoring_version=scoring_version,
        version=scoring_version,
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
    *,
    legacy: bool = False,
    decision: Optional[Dict[str, Any]] = None,
) -> SiglipImageVerificationResult:
    version = SCORING_VERSION if legacy else SIGLIP_SCORING_VERSION
    if not categories:
        return _empty_result(image_category, categories, "error", "no_active_categories", "No active SigLIP image categories are configured.", device, version)
    try:
        if legacy:
            prediction, decision_rule = classify(logits)
            decision = None
        else:
            decision = policy.consume_decision(logits, gate_config(), decision)
            prediction = decision["final_predicted_category"]
            decision_rule = decision["decision_rule"]
    except ValueError as error:
        return _empty_result(
            image_category,
            categories,
            "error",
            "logit_category_count_mismatch" if legacy else "decision_contract_error",
            str(error),
            device,
            version,
        )
    if not any(category.value == image_category for category in categories):
        return _empty_result(image_category, categories, "error", "category_not_found", f"Image category not active: {image_category}", device, version)
    configured = {category.value: category for category in categories}
    categories = [configured.get(name) or SiglipCategoryConfig(name, LABELS[name], prompt)
                  for name, prompt in SINGLE.items()]
    negative_scores = dict(zip(NEGATIVE, logits[len(SINGLE):]))
    positive_negative_margin = max(logits[:len(SINGLE)]) - max(negative_scores.values())
    logits = logits[:len(SINGLE)]
    target_index = next((index for index, category in enumerate(categories) if category.value == image_category), -1)
    if target_index < 0:
        return _empty_result(image_category, categories, "error", "category_not_found", f"Image category not found: {image_category}", device, version)

    target_category = categories[target_index]
    exp_values = [math.exp(logit - max(logits)) for logit in logits]
    exp_total = sum(exp_values) or 1.0
    relative_scores = [value / exp_total for value in exp_values]
    pair_scores = [1.0 / (1.0 + math.exp(-logit)) if logit >= 0
                   else math.exp(logit) / (1.0 + math.exp(logit)) for logit in logits]
    ranked_indices = sorted(range(len(categories)), key=lambda index: logits[index], reverse=True)
    top_index = ranked_indices[0]
    second_index = ranked_indices[1] if len(ranked_indices) > 1 else None
    target_pair_score = float(pair_scores[target_index])
    target_rank = ranked_indices.index(target_index) + 1
    top_logit = float(logits[top_index])
    second_logit = float(logits[second_index]) if second_index is not None else top_logit
    margin = round(top_logit - second_logit if target_rank == 1 else float(logits[target_index]) - top_logit, 4)

    passed = prediction == image_category
    evidence = 1.0 if passed else 0.0
    if passed:
        status = "matched"
        failure_reason = "passed"
    else:
        status = "mismatched"
        failure_reason = decision["decision_reason"] if decision and prediction == "other" else "predicted_category_mismatch"

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
                "field_score": 1.0 if category.value == prediction else 0.0,
                "evidence_score": 1.0 if category.value == prediction else 0.0,
                "match_threshold": round(float(category.match_threshold), 4),
                "margin_threshold": round(float(category.margin_threshold), 4),
                "target": index == target_index,
                "final_selected": category.value == prediction,
            }
        )

    ui_percentages = _rounded_relative_percentages([
        {
            "image_category": categories[index].value,
            "label": categories[index].label,
            "prompt": categories[index].prompt,
            "percentage": relative_scores[index],
            "final_selected": categories[index].value == prediction,
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
        predicted_category=prediction,
        predicted_label=LABELS.get(prediction, "Other"),
        predicted_prompt=SINGLE.get(prediction, ""),
        target_rank=target_rank,
        score_margin=margin,
        raw_logit=round(float(logits[target_index]), 4),
        raw_pair_score=round(target_pair_score, 4),
        relative_percentage=round(float(relative_scores[target_index]) * 100.0, 2),
        verification_threshold=round(float(target_category.match_threshold), 4),
        margin_threshold=round(float(target_category.margin_threshold), 4),
        model_name=SIGLIP_MODEL_NAME,
        model_version=SIGLIP_MODEL_VERSION,
        scoring_version=SCORING_VERSION if legacy else SIGLIP_SCORING_VERSION,
        version=SCORING_VERSION if legacy else SIGLIP_SCORING_VERSION,
        device=device,
        labels=labels,
        ui_percentages=ui_percentages,
        decision_rule=decision_rule,
        negative_scores=negative_scores,
        positive_negative_margin=round(positive_negative_margin, 4),
        decision=decision,
    )


def verify_image_category(
    image_path: str,
    image_category: Optional[str],
    categories: Optional[List[Dict[str, Any]]] = None,
    timing: Optional[Dict[str, Any]] = None,
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
        remote_result = remote_verify_image_logits(image_path, category_value, runtime_categories(), timing=timing)
    except ModelRuntimeUnavailableError as error:
        raise RuntimeError(f"SigLIP model runtime unavailable: {error}") from error

    if not isinstance(remote_result, dict):
        raise RuntimeError("SigLIP model runtime returned an invalid response.")

    raw_result = remote_result.get("raw") if isinstance(remote_result.get("raw"), dict) else {}
    logits = remote_result.get("logits") or raw_result.get("logits")
    if not isinstance(logits, list):
        return _empty_result(category_value, active_categories, "error", "raw_logits_required", "V2.3 requires raw positive and negative logits")
    device = str(remote_result.get("device") or "remote")
    return _result_from_logits(logits, category_value, active_categories, device, decision=remote_result.get("decision"))


def verify_image_categories_batch(
    image_paths: List[str],
    image_categories: List[List[str]],
    categories: Optional[List[Dict[str, Any]]] = None,
    timing: Optional[Dict[str, Any]] = None,
) -> List[List[SiglipImageVerificationResult]]:
    active_categories = _normalize_categories(categories or [])
    if not active_categories:
        return [
            [
                _empty_result(
                    str(category or ""),
                    active_categories,
                    "error",
                    "no_active_categories",
                    "No active SigLIP image categories are configured.",
                )
                for category in (category_values or [""])
            ]
            for category_values in image_categories
        ]
    if len(image_paths) != len(image_categories):
        raise RuntimeError("Image batch request count does not match target category count.")
    runtime_configured = is_runtime_configured(ModelRuntimeKind.IMAGE_VERIFICATION)
    if not runtime_configured:
        raise RuntimeError("GATEWAY_URL is not configured.")

    try:
        remote_result = remote_verify_image_logits_batch(
            image_paths,
            runtime_categories(),
            timing=timing,
        )
    except ModelRuntimeUnavailableError as error:
        raise RuntimeError(f"SigLIP batch model runtime unavailable: {error}") from error

    if not isinstance(remote_result, dict):
        raise RuntimeError("SigLIP batch model runtime returned an invalid response.")
    results = remote_result.get("results")
    if results is None and isinstance(remote_result.get("data"), dict):
        results = remote_result["data"].get("results")
    if results is None and isinstance(remote_result.get("data"), list):
        results = remote_result.get("data")
    if not isinstance(results, list):
        raise RuntimeError("SigLIP batch model runtime returned no results list.")
    if len(results) != len(image_paths):
        raise RuntimeError(f"SigLIP batch returned {len(results)} results for {len(image_paths)} images.")

    classified: List[List[SiglipImageVerificationResult]] = []
    for raw_item, target_categories in zip(results, image_categories):
        item = raw_item if isinstance(raw_item, dict) else {}
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        logits = item.get("logits") or raw.get("logits")
        device = str(item.get("device") or remote_result.get("device") or "remote")
        item_results: List[SiglipImageVerificationResult] = []
        for category_value in (target_categories or [""]):
            category = str(category_value or "").strip()
            if not category:
                item_results.append(_empty_result(category, active_categories, "error", "category_missing", "Image anchor category is required.", device))
            elif not any(active.value == category for active in active_categories):
                item_results.append(_empty_result(category, active_categories, "error", "category_not_found", f"Image category is not active: {category}", device))
            elif isinstance(logits, list):
                item_results.append(_result_from_logits(logits, category, active_categories, device, decision=item.get("decision")))
            else:
                item_results.append(_empty_result(category, active_categories, "error", "raw_logits_required", "V2.3 requires raw positive and negative logits", device))
        classified.append(item_results)
    return classified


def verify_image_category_from_logits(
    logits: List[float],
    image_category: str,
    categories: List[Dict[str, Any]],
    device: str = "test",
) -> SiglipImageVerificationResult:
    return _result_from_logits(logits, image_category, _normalize_categories(categories), device)


def verify_image_category_from_logits_v23(logits, image_category, categories, device="legacy-test"):
    return _result_from_logits(logits, image_category, _normalize_categories(categories), device, legacy=True)
