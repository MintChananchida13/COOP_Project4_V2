import ast
import math
import random
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from app.model_runtime import siglip_image_verification_adapter as adapter
from app.model_runtime.siglip_v23 import LABELS, NEGATIVE, SINGLE, classify, runtime_categories


def logits(**scores):
    return [scores.get(name, -20.0) for name in (*SINGLE, *NEGATIVE)]


def categories():
    return [{"value": name, "label": LABELS[name], "prompt": prompt} for name, prompt in SINGLE.items()]


class SiglipV23Tests(unittest.TestCase):
    def test_all_eight_positive_categories(self):
        for name in SINGLE:
            with self.subTest(name=name):
                result = adapter.verify_image_category_from_logits(logits(**{name: 5}), name, categories())
                self.assertEqual(result.predicted_category, name)
                self.assertTrue(result.passed)
                self.assertEqual(len(result.labels), 8)
                self.assertAlmostEqual(sum(item["percentage"] for item in result.ui_percentages), 100, places=1)

    def test_every_negative_rejects_to_other(self):
        for name in NEGATIVE:
            with self.subTest(name=name):
                result = adapter.verify_image_category_from_logits(logits(company_logo=0, **{name: 5}), "company_logo", categories())
                self.assertEqual(result.predicted_category, "other")
                self.assertEqual(result.predicted_label, "Other")
                self.assertFalse(result.passed)
                self.assertEqual(result.evidence_score, 0)
                self.assertTrue(all(item["field_score"] == 0 for item in result.labels))

    def test_guard_boundaries_and_order(self):
        cases = [
            ({"thai_garuda": -5, "emblem": -6.5, "printed_text": -7}, ("emblem", "garuda_emblem_ambiguity")),
            ({"thai_garuda": 2, "emblem": 0, "printed_text": -5}, ("thai_garuda", "unchanged")),
            ({"thai_garuda": 2, "emblem": -3, "printed_text": 0.01}, ("other", "weak_garuda_vs_negative")),
            ({"thai_garuda": 2, "emblem": -3, "printed_text": 0}, ("thai_garuda", "unchanged")),
            ({"emblem": -6.3}, ("emblem", "unchanged")),
            ({"emblem": -6.3001}, ("other", "weak_emblem_score")),
            ({"thai_garuda": 2, "emblem": 1, "printed_text": 1}, ("emblem", "garuda_emblem_ambiguity")),
            ({"company_logo": 0, "printed_text": 0}, ("company_logo", "unchanged")),
        ]
        for scores, expected in cases:
            with self.subTest(scores=scores):
                self.assertEqual(classify(logits(**scores)), expected)

    def test_experiment_parity_without_importing_model_or_ground_truth(self):
        source = Path(__file__).resolve().parents[2] / "siglip_single_v2_3.py"
        if not source.exists():
            self.skipTest("Original experiment not present")
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        selected = [node for node in tree.body if (
            isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in {"SINGLE", "NEGATIVE"} for target in node.targets)
        ) or isinstance(node, ast.FunctionDef) and node.name == "guarded_decision"]
        original = {}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), original)
        self.assertEqual(SINGLE, original["SINGLE"])
        expected_negative = {name: prompts[0] for name, prompts in original["NEGATIVE"].items()}
        expected_negative["numbers"] = "A document image region primarily consisting of printed numbers and numerical digits."
        self.assertEqual(NEGATIVE, expected_negative)
        generator = random.Random(23)
        names = [*SINGLE, *NEGATIVE]
        for iteration in range(1000):
            raw = [generator.uniform(-30, 10) for name in names]
            scores = dict(zip(names, raw))
            top = max(names, key=scores.get)
            baseline = top if top in SINGLE else "Other"
            prediction, rule = original["guarded_decision"](scores, baseline)
            self.assertEqual(classify(raw), (prediction.lower() if prediction == "Other" else prediction, rule))

    def test_bad_logits_fail_instead_of_fabricating_scores(self):
        for raw in ([0] * 8, [math.nan] * 16, [math.inf] * 16, ["0"] * 16):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    classify(raw)

    def test_legacy_categories_are_not_active(self):
        for name in ("official_stamp", "government_emblem", "thailand_symbol"):
            result = adapter.verify_image_category_from_logits(logits(emblem=5), name, categories())
            self.assertEqual(result.status, "error")
            self.assertFalse(result.passed)

    def test_single_and_batch_send_same_fixed_prompt_order(self):
        raw = logits(qr_code=5)
        with patch.object(adapter, "is_runtime_configured", return_value=True), patch.object(adapter, "remote_verify_image_logits", return_value={"logits": raw, "device": "cpu"}) as single, patch.object(adapter, "remote_verify_image_logits_batch", return_value={"results": [{"logits": raw}, {"logits": raw}], "device": "cpu"}) as batch:
            result = adapter.verify_image_category("one.png", "qr_code", categories())
            results = adapter.verify_image_categories_batch(["one.png", "two.png"], [["qr_code"], ["barcode", "qr_code"]], categories())
        self.assertEqual(single.call_args.args[2], runtime_categories())
        self.assertEqual(batch.call_args.args[1], runtime_categories())
        self.assertEqual(len(runtime_categories()), 16)
        self.assertTrue(result.passed)
        self.assertEqual([item.passed for item in results[1]], [False, True])
        keys = asdict(result)
        for key in ("score", "passed", "labels", "ui_percentages", "raw_logit", "predicted_category", "model_name", "version"):
            self.assertIn(key, keys)

    def test_extreme_finite_logits_keep_display_scores_finite(self):
        result = adapter.verify_image_category_from_logits([1000] + [-1000] * 15, "emblem", categories())
        self.assertTrue(result.passed)
        self.assertTrue(math.isfinite(result.raw_pair_score))


if __name__ == "__main__":
    unittest.main()
