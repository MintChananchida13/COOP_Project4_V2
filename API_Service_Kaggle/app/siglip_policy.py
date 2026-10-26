"""Shared, model-free Single Prompt V2.4 decision policy."""

import hashlib
import json
import math

SCORING_VERSION = "siglip-single-prompt-v2.4"
POSITIVE = ("emblem", "thai_garuda", "company_logo", "document_stamp", "portrait", "signature", "barcode", "qr_code")
NEGATIVE = ("printed_text", "numbers", "table", "blank", "background", "illustration", "pattern", "bar_chart")


def normalize_config(config=None):
    config = {} if config is None else config
    if not isinstance(config, dict) or set(config) - {"confidence_min_relative_score", "margin_min_relative_score"}:
        raise ValueError("Invalid V2.4 gate configuration")
    result = {}
    for key in ("confidence_min_relative_score", "margin_min_relative_score"):
        value = config.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or
                                  not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError(f"{key} must be null or a finite number in [0,1]")
        result[key] = float(value) if value is not None else None
    return result


def validate_logits(logits):
    if not isinstance(logits, list) or len(logits) != len(POSITIVE) + len(NEGATIVE):
        raise ValueError("V2.4 requires 16 ordered raw logits")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in logits):
        raise ValueError("V2.4 requires finite numeric raw logits")


def fingerprint(logits, config=None):
    validate_logits(logits)
    data = [SCORING_VERSION, POSITIVE, NEGATIVE, [float(value) for value in logits], normalize_config(config)]
    return hashlib.sha256(json.dumps(data, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def decide(logits, config=None):
    validate_logits(logits)
    config = normalize_config(config)
    scores = dict(zip((*POSITIVE, *NEGATIVE), logits))
    ranked = sorted(POSITIVE, key=scores.get, reverse=True)
    first, second = ranked[:2]
    negative = max(NEGATIVE, key=scores.get)
    exponents = [math.exp(scores[name] - scores[first]) for name in POSITIVE]
    total = sum(exponents)
    relatives = dict(zip(POSITIVE, [value / total for value in exponents]))
    if not all(math.isfinite(value) for value in (scores[first] - scores[negative], scores[first] - scores[second])):
        raise ValueError("Raw logit margin overflow")
    confidence = relatives[first]
    relative_margin = relatives[first] - relatives[second]
    negative_passed = scores[first] > scores[negative]
    confidence_passed = "not_applied" if config["confidence_min_relative_score"] is None else "not_evaluated"
    margin_passed = "not_applied" if config["margin_min_relative_score"] is None else "not_evaluated"
    reason = "positive_rank1_accepted"
    if not negative_passed:
        reason = "negative_category_won" if scores[negative] > scores[first] else "negative_category_tied"
    else:
        if config["confidence_min_relative_score"] is not None:
            confidence_passed = confidence >= config["confidence_min_relative_score"]
        if confidence_passed is False:
            reason = "confidence_gate_failed"
        else:
            if config["margin_min_relative_score"] is not None:
                margin_passed = relative_margin >= config["margin_min_relative_score"]
            if margin_passed is False:
                reason = "margin_gate_failed"
    prediction = first if reason == "positive_rank1_accepted" else "other"
    return {
        "scoring_version": SCORING_VERSION,
        "raw_top1_category": first, "raw_top1_logit": scores[first],
        "raw_top2_category": second, "raw_top2_logit": scores[second],
        "final_predicted_category": prediction,
        "decision_rule": "rank1_negative_competition_then_optional_relative_gates",
        "decision_reason": reason,
        "positive_top1_logit": scores[first],
        "negative_top1_category": negative, "negative_top1_logit": scores[negative],
        "positive_negative_margin": scores[first] - scores[negative],
        "positive_top1_top2_margin": scores[first] - scores[second],
        "positive_relative_score": confidence, "positive_relative_margin": relative_margin,
        "confidence_gate_passed": confidence_passed,
        "margin_gate_passed": margin_passed, "negative_gate_passed": negative_passed,
        "decision_config": config, "decision_fingerprint": fingerprint(logits, config),
        "ranking_basis": "positive_raw_logits", "confidence_basis": "positive_relative_score_not_probability",
    }


def consume_decision(logits, config=None, decision=None):
    if decision is None:
        return decide(logits, config)
    required = {"scoring_version", "raw_top1_category", "raw_top1_logit", "raw_top2_category", "raw_top2_logit",
                "final_predicted_category", "decision_rule", "decision_reason", "positive_top1_logit",
                "negative_top1_category", "negative_top1_logit", "positive_negative_margin",
                "positive_top1_top2_margin", "confidence_gate_passed", "margin_gate_passed", "negative_gate_passed"}
    if not isinstance(decision, dict) or not required.issubset(decision):
        raise ValueError("Incomplete V2.4 decision trace")
    if decision.get("scoring_version") != SCORING_VERSION or decision.get("decision_fingerprint") != fingerprint(logits, config):
        raise ValueError("V2.4 decision version/vector/config mismatch")
    if decision.get("decision_config") != normalize_config(config):
        raise ValueError("V2.4 decision configuration mismatch")
    if decision["final_predicted_category"] not in (*POSITIVE, "other"):
        raise ValueError("Invalid V2.4 final category")
    expected = decide(logits, config)
    if any(decision.get(key) != value for key, value in expected.items()):
        raise ValueError("V2.4 decision trace does not match the shared policy")
    return dict(decision)
