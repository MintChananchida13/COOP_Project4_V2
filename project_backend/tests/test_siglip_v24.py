import ast
import __future__
import copy
import math
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from app.model_runtime import siglip_image_verification_adapter as adapter
from app.model_runtime.siglip_v23 import SINGLE, NEGATIVE, runtime_categories
from app.model_runtime.siglip_v24 import gate_config, policy


def raw(**values):
    return [values.get(name, -30.0) for name in (*SINGLE, *NEGATIVE)]


def categories():
    return [{"value": name, "prompt": prompt, "match_threshold": 0.5, "margin_threshold": 0.05}
            for name, prompt in SINGLE.items()]


class SiglipV24Tests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"SIGLIP_V24_CONFIDENCE_MIN_RELATIVE_SCORE": "",
                                              "SIGLIP_V24_MARGIN_MIN_RELATIVE_SCORE": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_default_gates_are_explicitly_disabled(self):
        result = policy.decide(raw(portrait=1))
        self.assertEqual(result["scoring_version"], "siglip-single-prompt-v2.4")
        self.assertTrue(result["negative_gate_passed"])
        self.assertEqual(result["confidence_gate_passed"], "not_applied")
        self.assertEqual(result["margin_gate_passed"], "not_applied")

    def test_all_positive_categories_share_same_policy(self):
        for name in SINGLE:
            with self.subTest(category=name):
                result = adapter.verify_image_category_from_logits(raw(**{name: -7}), name, categories())
                self.assertTrue(result.passed)
                self.assertEqual(result.predicted_category, name)
                self.assertEqual(result.decision["raw_top1_category"], name)

    def test_all_negative_categories_reject_rank_one_positive(self):
        for name in NEGATIVE:
            with self.subTest(category=name):
                result = policy.decide(raw(portrait=1, **{name: 2}))
                self.assertEqual(result["raw_top1_category"], "portrait")
                self.assertEqual(result["final_predicted_category"], "other")
                self.assertEqual(result["decision_reason"], "negative_category_won")

    def test_unknown_ties_reject_instead_of_arbitrary_positive(self):
        result = policy.decide([0.0] * 16)
        self.assertEqual(result["final_predicted_category"], "other")
        self.assertEqual(result["decision_reason"], "negative_category_tied")

    def test_garuda_close_to_emblem_is_never_substituted(self):
        result = adapter.verify_image_category_from_logits(raw(thai_garuda=8.1, emblem=6.5818), "thai_garuda", categories())
        self.assertTrue(result.passed)
        self.assertEqual(result.labels[0]["relative_score"], 0.8203)
        self.assertEqual(result.raw_pair_score, 0.9997)
        self.assertEqual(result.score_margin, 1.5182)
        self.assertEqual(result.predicted_category, "thai_garuda")
        self.assertEqual(result.decision["raw_top2_category"], "emblem")
        self.assertEqual(result.decision["margin_gate_passed"], "not_applied")

    def test_portrait_depends_on_negative_not_low_sigmoid(self):
        for negative, expected in ((-8.0, "other"), (-20.0, "portrait")):
            result = adapter.verify_image_category_from_logits(raw(portrait=-8.1219, barcode=-9.7214, printed_text=negative), "portrait", categories())
            self.assertEqual(result.predicted_category, expected)
            self.assertEqual(result.raw_pair_score, 0.0003)

    def test_confidence_gate_enabled_only_by_new_explicit_config(self):
        scores = [0.0] * 8 + [-1.0] * 8
        accepted = policy.decide(scores)
        rejected = policy.decide(scores, {"confidence_min_relative_score": 0.2})
        self.assertEqual(accepted["final_predicted_category"], "emblem")
        self.assertEqual(rejected["decision_reason"], "confidence_gate_failed")
        self.assertFalse(rejected["confidence_gate_passed"])

    def test_relative_margin_gate_uses_unrounded_dimensionless_values(self):
        scores = raw(thai_garuda=0.1, emblem=0)
        result = policy.decide(scores, {"margin_min_relative_score": 0.05})
        self.assertLess(result["positive_relative_margin"], 0.05)
        self.assertAlmostEqual(result["positive_top1_top2_margin"], 0.1)
        self.assertEqual(result["decision_reason"], "margin_gate_failed")

    def test_boundaries_and_gate_priority(self):
        scores = [0.0] * 8 + [-1.0] * 8
        boundary = policy.decide(scores, {"confidence_min_relative_score": 0.125, "margin_min_relative_score": 0})
        self.assertTrue(boundary["confidence_gate_passed"])
        self.assertTrue(boundary["margin_gate_passed"])
        rejected = policy.decide(raw(portrait=0, printed_text=1), {"confidence_min_relative_score": 0.99, "margin_min_relative_score": 0.9})
        self.assertEqual(rejected["confidence_gate_passed"], "not_evaluated")
        self.assertEqual(rejected["margin_gate_passed"], "not_evaluated")
        self.assertEqual(rejected["decision_reason"], "negative_category_won")

    def test_old_category_thresholds_are_not_silently_activated(self):
        settings = [{**item, "match_threshold": 1.0, "margin_threshold": 1.0} for item in categories()]
        result = adapter.verify_image_category_from_logits(raw(portrait=0.001, barcode=0), "portrait", settings)
        self.assertTrue(result.passed)
        self.assertEqual(result.decision["confidence_gate_passed"], "not_applied")

    def test_invalid_configuration_and_logits_fail_closed(self):
        for value in (-1, 1.1, True, math.nan, "0.5"):
            with self.assertRaises(ValueError):
                policy.decide(raw(portrait=1), {"confidence_min_relative_score": value})
        for scores in ([0] * 8, [math.nan] * 16, [True] * 16):
            with self.assertRaises(ValueError):
                policy.decide(scores)

    def test_api_decision_is_consumed_and_corruption_is_not_accepted(self):
        scores = raw(thai_garuda=8.1, emblem=6.5818)
        decision = policy.decide(scores)
        with patch.object(adapter, "is_runtime_configured", return_value=True), \
                patch.object(adapter, "remote_verify_image_logits", return_value={"logits": scores, "decision": decision}):
            result = adapter.verify_image_category("image.png", "thai_garuda", categories())
        self.assertTrue(result.passed)
        self.assertEqual(result.decision, decision)
        changed = copy.deepcopy(decision)
        changed["final_predicted_category"] = "emblem"
        with self.assertRaises(ValueError):
            policy.consume_decision(scores, None, changed)
        with self.assertRaises(ValueError):
            policy.consume_decision(scores, {"confidence_min_relative_score": 0.9}, decision)

    def test_single_batch_compatibility_and_target_verification(self):
        scores = raw(thai_garuda=8.1, emblem=6.5818)
        decision = policy.decide(scores)
        with patch.object(adapter, "is_runtime_configured", return_value=True), \
                patch.object(adapter, "remote_verify_image_logits_batch", return_value={"results": [{"logits": scores, "decision": decision}]}):
            results = adapter.verify_image_categories_batch(["image.png"], [["thai_garuda", "emblem"]], list(reversed(categories())))[0]
        self.assertEqual([result.passed for result in results], [True, False])
        self.assertEqual(results[0].decision, results[1].decision)
        self.assertEqual(results[1].failure_reason, "predicted_category_mismatch")
        self.assertEqual(len(runtime_categories()), 16)

    def test_environment_defaults_and_opt_in_are_unambiguous(self):
        self.assertEqual(gate_config(), {"confidence_min_relative_score": None, "margin_min_relative_score": None})
        with patch.dict(os.environ, {"SIGLIP_V24_CONFIDENCE_MIN_RELATIVE_SCORE": "0"}):
            self.assertEqual(gate_config()["confidence_min_relative_score"], 0.0)
        with patch.dict(os.environ, {"SIGLIP_V24_MARGIN_MIN_RELATIVE_SCORE": "nan"}):
            with self.assertRaises(ValueError):
                gate_config()

    def test_v23_comparison_is_explicit_and_not_active_fallback(self):
        scores = raw(thai_garuda=8.1, emblem=6.5818)
        legacy = adapter.verify_image_category_from_logits_v23(scores, "thai_garuda", categories())
        current = adapter.verify_image_category_from_logits(scores, "thai_garuda", categories())
        self.assertEqual(legacy.scoring_version, "siglip-single-prompt-v2.3")
        self.assertEqual(legacy.predicted_category, "emblem")
        self.assertEqual(current.scoring_version, "siglip-single-prompt-v2.4")
        self.assertEqual(current.predicted_category, "thai_garuda")

    def test_backend_trace_image_anchor_score_and_final_decision(self):
        source = Path(__file__).parents[1] / "app/business/services.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and
                   node.name in ("_image_match_from_siglip_result", "_verification_summary")]
        decision_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DecisionService")
        namespace = {"VERIFICATION_STRATEGY_STANDARD": "standard"}
        exec(compile(ast.Module(body=[*methods, decision_class], type_ignores=[]), str(source), "exec",
                     flags=__future__.annotations.compiler_flag), namespace)
        for negative, expected in ((-30, True), (9, False)):
            result = adapter.verify_image_category_from_logits(raw(thai_garuda=8.1, emblem=6.5818, printed_text=negative), "thai_garuda", categories())
            mapped = namespace["_image_match_from_siglip_result"](None, result, None)
            self.assertEqual(mapped["final_predicted_category"], result.predicted_category)
            self.assertEqual(mapped["decision_reason"], result.decision["decision_reason"])
            summary = namespace["_verification_summary"](None, "template", [{**mapped, "anchor_type": "image", "required": True, "weight": 1}])
            self.assertEqual(summary["image_anchor_score"], int(expected))
            final = namespace["DecisionService"]().decide_candidate_strict(0.9, summary, 0.75)
            self.assertEqual(final["final_passed"], expected)
            if expected:
                self.assertFalse(namespace["DecisionService"]().decide_candidate_strict(0.1, summary, 0.75)["final_passed"])
