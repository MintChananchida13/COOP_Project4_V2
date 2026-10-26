"""Characterize existing V2.3, including policy gaps; not V2.4 acceptance tests."""

import ast
import __future__
import logging
import math
import json
from pathlib import Path
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from app.model_runtime import siglip_image_verification_adapter as adapter
from app.model_runtime.siglip_v23 import SINGLE, NEGATIVE, classify, runtime_categories
from app.core import model_runtime_client


def raw(**values):
    return [values.get(name, -30.0) for name in (*SINGLE, *NEGATIVE)]


def categories(match=0.5, margin=0.05, temperature=1):
    return [{"value": name, "prompt": prompt, "match_threshold": match,
             "margin_threshold": margin, "evidence_temperature": temperature}
            for name, prompt in SINGLE.items()]


def portrait_case(negative=-30):
    top, gap, relative = -8.1219, 1.5995, 0.6463
    remainder = (1 / relative - 1 - math.exp(-gap)) / 6
    scores = {name: top + math.log(remainder) for name in SINGLE}
    scores.update(portrait=top, barcode=top - gap, printed_text=negative)
    return raw(**scores)


class SiglipV23AuditTests(unittest.TestCase):
    def setUp(self):
        original = adapter._result_from_logits
        def legacy_result(*args, **kwargs):
            kwargs["legacy"] = True
            return original(*args, **kwargs)
        legacy_patch = patch.object(adapter, "_result_from_logits", side_effect=legacy_result)
        legacy_patch.start()
        self.addCleanup(legacy_patch.stop)

    def verify(self, scores, target="portrait", settings=None):
        return adapter.verify_image_category_from_logits_v23(scores, target, settings or categories())

    def test_case_a_reproduces_reported_scores_and_guard_override(self):
        result = self.verify(raw(thai_garuda=8.1, emblem=6.5818), "thai_garuda")
        self.assertEqual(result.labels[0]["image_category"], "thai_garuda")
        self.assertEqual(result.labels[0]["relative_score"], 0.8203)
        self.assertEqual(result.raw_pair_score, 0.9997)
        self.assertEqual(result.score_margin, 1.5182)
        self.assertEqual(result.predicted_category, "emblem")
        self.assertEqual(result.decision_rule, "garuda_emblem_ambiguity")
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_reason, "predicted_category_mismatch")
        self.assertEqual(result.evidence_score, 0)

    def test_case_b_other_requires_negative_competition_under_this_policy(self):
        rejected = self.verify(portrait_case(-8.0))
        accepted = self.verify(portrait_case(-30))
        for result in (rejected, accepted):
            self.assertEqual(result.labels[0]["image_category"], "portrait")
            self.assertEqual(result.labels[1]["image_category"], "barcode")
            self.assertEqual(result.labels[0]["relative_score"], 0.6463)
            self.assertEqual(result.raw_logit, -8.1219)
            self.assertEqual(result.raw_pair_score, 0.0003)
            self.assertEqual(result.score_margin, 1.5995)
            self.assertEqual(result.decision_rule, "unchanged")
        self.assertEqual(rejected.predicted_category, "other")
        self.assertFalse(rejected.passed)
        self.assertTrue(accepted.passed)
        self.assertEqual(accepted.predicted_category, "portrait")

    def test_all_positive_categories_can_pass_existing_policy(self):
        for name in SINGLE:
            with self.subTest(category=name):
                result = self.verify(raw(**{name: 5}), name)
                self.assertTrue(result.passed)
                self.assertEqual(result.predicted_category, name)
                self.assertEqual(result.scoring_version, "siglip-single-prompt-v2.3")

    def test_every_negative_category_can_win_and_produce_other(self):
        for name in NEGATIVE:
            with self.subTest(category=name):
                result = self.verify(raw(portrait=0, **{name: 1}))
                self.assertEqual(result.predicted_category, "other")
                self.assertEqual(result.evidence_score, 0)

    def test_low_relative_and_pair_scores_do_not_gate_generic_positive(self):
        scores = [-10.0] * 8 + [-11.0] * 7
        scores[list(SINGLE).index("portrait")] = -9.999
        result = self.verify(scores, settings=categories(0.99, 1, 100))
        self.assertEqual(result.target_rank, 1)
        self.assertLess(result.labels[0]["relative_score"], 0.13)
        self.assertLess(result.raw_pair_score, 0.001)
        self.assertLess(result.score_margin, 0.05)
        self.assertTrue(result.passed)

    def test_configured_confidence_margin_and_temperature_are_display_only_in_v23(self):
        low = self.verify(portrait_case(), settings=categories(0.01, 0, 0.01))
        high = self.verify(portrait_case(), settings=categories(0.99, 1, 100))
        self.assertEqual(low.passed, high.passed)
        self.assertEqual(low.predicted_category, high.predicted_category)
        self.assertEqual(low.relative_percentage, high.relative_percentage)
        self.assertNotEqual(low.verification_threshold, high.verification_threshold)

    def test_close_negative_is_category_specific_not_general_margin_gate(self):
        portrait = self.verify(raw(portrait=0, printed_text=-1.999))
        garuda = self.verify(raw(thai_garuda=0, printed_text=-1.999), "thai_garuda")
        self.assertTrue(portrait.passed)
        self.assertEqual(garuda.decision_rule, "weak_garuda_vs_negative")
        self.assertEqual(garuda.predicted_category, "other")

    def test_threshold_boundaries_rule_order_and_weak_emblem_override(self):
        cases = [
            (raw(thai_garuda=2, emblem=0, printed_text=-10), ("thai_garuda", "unchanged")),
            (raw(thai_garuda=1.9999, emblem=0, printed_text=-10), ("emblem", "garuda_emblem_ambiguity")),
            (raw(thai_garuda=2, emblem=-10, printed_text=0), ("thai_garuda", "unchanged")),
            (raw(thai_garuda=1.9999, emblem=-10, printed_text=0), ("other", "weak_garuda_vs_negative")),
            (raw(emblem=-6.3), ("emblem", "unchanged")),
            (raw(emblem=-6.3001), ("other", "weak_emblem_score")),
            (raw(thai_garuda=-5, emblem=-6.5, printed_text=-7), ("emblem", "garuda_emblem_ambiguity")),
        ]
        for scores, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify(scores), expected)

    def test_ties_use_canonical_order_not_confidence_gating(self):
        self.assertEqual(classify(raw(company_logo=0, printed_text=0)), ("company_logo", "unchanged"))
        self.assertEqual(classify(raw(emblem=0, thai_garuda=0)), ("emblem", "unchanged"))
        self.assertEqual(classify(raw(portrait=0, barcode=-0.00001)), ("portrait", "unchanged"))

    def test_uniform_unknown_like_logits_do_not_guarantee_other(self):
        result = self.verify([0.0] * 15, "emblem", categories(0.99, 1))
        self.assertEqual(result.predicted_category, "emblem")
        self.assertEqual(result.labels[0]["relative_score"], 0.125)
        self.assertTrue(result.passed)

    def test_labels_rank_raw_positive_logits_and_ui_uses_fixed_positive_order(self):
        result = self.verify(raw(thai_garuda=8.1, emblem=6.5818), "thai_garuda")
        self.assertEqual([item["image_category"] for item in result.ui_percentages], list(SINGLE))
        self.assertEqual(result.ui_percentages[0]["image_category"], "emblem")
        self.assertEqual(result.labels[0]["image_category"], "thai_garuda")
        self.assertEqual([item["raw_logit"] for item in result.labels], sorted([item["raw_logit"] for item in result.labels], reverse=True))
        self.assertAlmostEqual(sum(item["percentage"] for item in result.ui_percentages), 100, places=1)
        self.assertEqual(next(item for item in result.labels if item["image_category"] == "emblem")["evidence_score"], 1)

    def test_api_single_batch_and_reordered_configs_keep_prompt_logit_alignment(self):
        garuda, portrait = raw(thai_garuda=8.1, emblem=6.5818), portrait_case(-8.0)
        with patch.object(adapter, "is_runtime_configured", return_value=True), \
                patch.object(adapter, "remote_verify_image_logits", return_value={"logits": garuda}) as single, \
                patch.object(adapter, "remote_verify_image_logits_batch", return_value={"results": [{"logits": garuda}, {"logits": portrait}]}) as batch:
            individual = adapter.verify_image_category("a.png", "thai_garuda", list(reversed(categories())))
            results = adapter.verify_image_categories_batch(["a.png", "b.png"], [["thai_garuda", "emblem"], ["portrait"]], categories())
        self.assertEqual(single.call_args.args[2], runtime_categories())
        self.assertEqual(batch.call_args.args[1], runtime_categories())
        self.assertEqual(individual.predicted_category, results[0][0].predicted_category)
        self.assertEqual([item.passed for item in results[0]], [False, True])
        self.assertEqual(results[1][0].predicted_category, "other")

    def test_disabled_competitor_still_participates_in_fixed_eight_positive_logits(self):
        settings = [item for item in categories() if item["value"] != "emblem"]
        result = self.verify(raw(emblem=5, portrait=0), settings=settings)
        self.assertEqual(result.predicted_category, "emblem")
        self.assertFalse(result.passed)
        target = self.verify(raw(emblem=5), "emblem", settings)
        self.assertEqual(target.status, "error")

    def test_http_envelope_client_and_adapter_preserve_raw_logit_order(self):
        garuda, portrait = raw(thai_garuda=8.1, emblem=6.5818), portrait_case(-8.0)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [
            json.dumps({"success": True, "model": adapter.SIGLIP_MODEL_NAME, "result": {"logits": garuda, "device": "cpu"}}).encode(),
            json.dumps({"success": True, "model": adapter.SIGLIP_MODEL_NAME,
                        "result": {"results": [{"logits": garuda}, {"logits": portrait}]}}).encode(),
        ]
        with patch.object(model_runtime_client, "GATEWAY_URL", "http://unit.invalid"), \
                patch.object(model_runtime_client, "_path_to_data_url", return_value="image"), \
                patch.object(model_runtime_client.urllib.request, "urlopen", return_value=response) as http:
            single = adapter.verify_image_category("a.png", "thai_garuda", categories())
            batch = adapter.verify_image_categories_batch(["a.png", "b.png"], [["thai_garuda"], ["portrait"]], categories())
        self.assertEqual(single.predicted_category, "emblem")
        self.assertEqual([items[0].predicted_category for items in batch], ["emblem", "other"])
        self.assertEqual(http.call_count, 2)
        for call in http.call_args_list:
            payload = json.loads(call.args[0].data)
            self.assertEqual([item["value"] for item in payload["categories"]], [*SINGLE, *NEGATIVE])

    def test_real_backend_mapping_summary_and_standard_final_gate(self):
        source = Path(__file__).parents[1] / "app/business/services.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and
                   node.name in ("_image_match_from_siglip_result", "_verification_summary", "verify_template_strict")]
        decision_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DecisionService")
        namespace = {"VERIFICATION_STRATEGY_STANDARD": "standard", "time": time, "logger": logging.getLogger(__name__)}
        exec(compile(ast.Module(body=[*methods, decision_class], type_ignores=[]), str(source), "exec",
                     flags=__future__.annotations.compiler_flag), namespace)
        for scores, expected in ((raw(portrait=5), True), (portrait_case(-8.0), False)):
            result = self.verify(scores)
            mapped = namespace["_image_match_from_siglip_result"](None, result, None)
            self.assertIsNone(mapped.get("siglip_decision"))
            self.assertNotIn("negative_scores", mapped)
            self.assertEqual(mapped["field_score"], 1 if expected else 0)
            checked = {**mapped, "anchor_type": "image", "required": True, "weight": 2}
            service = SimpleNamespace(_image_anchor_check=lambda *args, **kwargs: checked)
            service._verification_summary = MethodType(namespace["_verification_summary"], service)
            summary = namespace["verify_template_strict"](service, "template", {1: "unused"}, [{"data_type": "image"}])
            self.assertEqual(summary["passed"], expected)
            self.assertEqual(summary["image_anchor_score"], 1 if expected else 0)
            final = namespace["DecisionService"]().decide_candidate_strict(0.9, summary, 0.75)
            self.assertEqual(final["final_passed"], expected)
            if not expected:
                self.assertEqual(final["decision_path"], "standard_image_anchor_failed")
            else:
                self.assertFalse(namespace["DecisionService"]().decide_candidate_strict(0.1, summary, 0.75)["final_passed"])


if __name__ == "__main__":
    unittest.main()
