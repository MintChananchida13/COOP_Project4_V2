import copy
import io
import json
import math
import random
import unittest
import urllib.error
from dataclasses import asdict
from unittest.mock import MagicMock, patch

from app.core import model_runtime_client as client
from app.model_runtime import siglip_image_verification_adapter as adapter
from app.model_runtime import siglip_service_contract as contract
from app.model_runtime.siglip_v23 import SINGLE, NEGATIVE
from siglip_contract_fixtures import categories, payloads, leaf, batch, decision, reference_score


class SiglipServiceContractTests(unittest.TestCase):
    def single(self, data, target="portrait", settings=None):
        with patch.object(adapter, "is_runtime_configured", return_value=True), \
             patch.object(adapter, "remote_verify_image_logits", return_value=data) as remote:
            result = adapter.verify_image_category("fixture.png", target, settings or categories())
        remote.assert_called_once()
        return result

    def many(self, data, targets=None, settings=None):
        targets = targets or [["portrait"] for _ in data["results"]]
        with patch.object(adapter, "is_runtime_configured", return_value=True), \
             patch.object(adapter, "remote_verify_image_logits_batch", return_value=data) as remote:
            result = adapter.verify_image_categories_batch([f"{i}.png" for i in range(len(targets))], targets, settings or categories())
        remote.assert_called_once()
        return result

    def test_single_current_decision_without_logits(self):
        data = decision(scores={"portrait": -8, "barcode": -9})
        self.assertNotIn("logits", data)
        result = self.single(data)
        self.assertTrue(result.passed)
        self.assertEqual(result.scoring_version, contract.SCORING_VERSION)
        self.assertEqual(result.evidence_score, 1)
        self.assertEqual(result.raw_pair_score, .0003)
        self.assertNotEqual(result.raw_pair_score, result.score)
        self.assertIsNone(result.decision)  # no fabricated V2.4 decision trace
        for key, value in contract.parse_decision(data, "portrait", payloads()).items():
            self.assertEqual(getattr(result, key), value)

    def test_all_positive_and_negative_categories_single_and_batch(self):
        for positive in SINGLE:
            with self.subTest(positive=positive):
                scores = {positive: 1}
                self.assertTrue(self.single(decision(positive, scores), positive).passed)
                self.assertTrue(self.many(batch(leaf(scores)), [[positive]])[0][0].passed)
        for negative in NEGATIVE:
            with self.subTest(negative=negative):
                # V2.4 would allow this inside its old 0.8 tolerance. binary-v3 rejects it.
                scores = {"portrait": 0, negative: .01}
                a = self.single(decision(scores=scores))
                b = self.many(batch(leaf(scores)))[0][0]
                for result in (a, b):
                    self.assertFalse(result.passed)
                    self.assertEqual(result.predicted_category, negative)
                    self.assertEqual(result.score, 0)
                    self.assertEqual(result.failure_reason, "predicted_category_mismatch")

    def test_batch_matches_real_service_scorer_including_ties_and_rounding(self):
        generator = random.Random(83)
        vectors = [[0.] * len(payloads()), [1000.] + [-1000.] * (len(payloads()) - 1)]
        vectors += [[generator.uniform(-30, 10) for _ in payloads()] for _ in range(40)]
        for vector in vectors:
            raw = leaf(vector)
            for target in SINGLE:
                with self.subTest(target=target, vector=vector):
                    expected = reference_score(target_value=target, categories=payloads(), classification=raw)
                    parsed = contract.score_classification(raw, target, payloads())
                    for key, value in parsed.items():
                        if key not in {"model_name", "model_version"}:
                            self.assertEqual(value, expected[key], key)

    def test_batch_order_count_and_multiple_targets(self):
        result = self.many(batch(leaf({"signature": 2}), leaf({"portrait": 2}), leaf({"qr_code": 2})),
                           [["portrait", "signature"], ["portrait"], ["barcode", "qr_code"]])
        self.assertEqual([[r.passed for r in group] for group in result], [[False, True], [True], [False, True]])
        self.assertEqual([[r.image_category for r in group] for group in result],
                         [["portrait", "signature"], ["portrait"], ["barcode", "qr_code"]])
        for bad in ({"results": [], "count": 0}, {"results": [leaf()], "count": 2},
                    {"results": [leaf()]}, {"results": [leaf()], "count": True},
                    {"results": [leaf(), leaf()], "count": 2}, {"error": {"code": "FAILED"}, "results": [leaf()]}):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                self.many(bad, [["portrait"]])
        with self.assertRaises(RuntimeError):
            adapter.verify_image_categories_batch([], [["portrait"]], [])
        self.assertEqual(adapter.verify_image_categories_batch([], [], []), [])

    def test_single_batch_decision_and_display_parity(self):
        for scores, target in (({"portrait": .0000001, "emblem": 0}, "portrait"),
                               ({"blank": .01, "portrait": 0}, "portrait"),
                               ([0.] * len(payloads()), "emblem")):
            single = asdict(self.single(decision(target, scores), target))
            multiple = asdict(self.many(batch(leaf(scores)), [[target]])[0][0])
            for item in (single, multiple):
                item.pop("model_name")
                item.pop("model_version")
            self.assertEqual(single, multiple)

    def test_missing_unknown_and_disabled_targets_never_pass(self):
        with patch.object(adapter, "is_runtime_configured", return_value=True), \
             patch.object(adapter, "remote_verify_image_logits") as remote:
            for target in (None, "unknown", "printed_text"):
                result = adapter.verify_image_category("fixture.png", target, categories())
                self.assertFalse(result.passed)
                self.assertEqual(result.status, "error")
            remote.assert_not_called()
        for target in ("", "unknown", "printed_text"):
            result = self.many(batch(leaf({"portrait": 2})), [[target]])[0][0]
            self.assertFalse(result.passed)
            self.assertEqual(result.status, "error")

    def test_invalid_single_data_fails_closed(self):
        data = decision(scores={"portrait": 2})
        corruptions = []
        for key, value in (("image_category", "signature"), ("predicted_category", "unknown"),
                           ("score", None), ("evidence_score", .99), ("passed", "true"),
                           ("target_rank", 2), ("target_rank", True), ("scoring_version", "siglip-single-prompt-v2.4"),
                           ("raw_logit", math.nan), ("raw_pair_score", math.inf),
                           ("relative_percentage", -1), ("error", "upstream failed"), ("status", "error")):
            bad = {**data, key: value}
            bad.pop("verifications")
            bad.pop("requested_categories")
            corruptions.append(bad)
        for key in ("score", "evidence_score", "labels", "target_rank", "raw_pair_score", "ui_percentages"):
            bad = copy.deepcopy(data)
            bad.pop("verifications")
            bad.pop(key)
            corruptions.append(bad)
        for key, value in (("prompt", "wrong prompt"), ("image_category", "unknown"),
                           ("raw_logit", None), ("raw_logit", -100), ("raw_pair_score", "0.5"),
                           ("match_threshold", None), ("relative_score", True)):
            bad = copy.deepcopy(data["verifications"][0])
            bad["labels"][0][key] = value
            corruptions.append(bad)
        corruptions += [None, [], {}, {"logits": [1.] * 15}, {"data": data, "meta": {}},
                        {**data, "requested_categories": ["signature"]}]
        for bad in corruptions:
            with self.subTest(bad=bad):
                result = self.single(bad)
                self.assertFalse(result.passed)
                self.assertEqual(result.score, 0)
                self.assertEqual(result.status, "error")
                self.assertEqual(result.failure_reason, "invalid_service_response")
                self.assertTrue(result.error)

    def test_invalid_batch_item_does_not_shift_other_images(self):
        good = leaf({"portrait": 2})
        invalid = [None, {}, {"logits": [1.] * 15}]
        for key, value in (("categories", list(reversed(good["categories"]))), ("kind", "other"),
                           ("raw_output", []), ("error", {"code": "MODEL_ERROR"})):
            invalid.append({**good, key: value})
        for logits in ([], [0.] * 14, [0.] * 16, [math.nan] * 15, [math.inf] * 15,
                       [10 ** 400] * 15, ["1"] * 15, [True] * 15, [[1.]] * 15):
            invalid.append({**good, "raw_output": [{"logits_per_image": logits}]})
        for bad in invalid:
            with self.subTest(bad=bad):
                results = self.many(batch(bad, good))
                self.assertFalse(results[0][0].passed)
                self.assertTrue(results[0][0].error)
                self.assertTrue(results[1][0].passed)

    def test_registry_order_disabled_and_legacy_thresholds(self):
        settings = list(reversed(categories()))
        for item in settings:
            item.update(match_threshold=1., margin_threshold=1.)
        data = leaf({"portrait": .001, "barcode": 0}, settings)
        with patch.dict("os.environ", {"SIGLIP_V24_CONFIDENCE_MIN_RELATIVE_SCORE": ".99",
                                       "SIGLIP_V24_MARGIN_MIN_RELATIVE_SCORE": ".99"}):
            result = self.many(batch(data), settings=settings)[0][0]
        self.assertTrue(result.passed)
        self.assertEqual(result.verification_threshold, 1)
        self.assertEqual(result.margin_threshold, 1)
        self.assertEqual(result.scoring_version, contract.SCORING_VERSION)
        settings = [{**item, "enabled": item["value"] != "portrait"} for item in categories()]
        with patch.object(adapter, "remote_verify_image_logits") as remote:
            rejected = adapter.verify_image_category("fixture.png", "portrait", settings)
        remote.assert_not_called()
        self.assertFalse(rejected.passed)
        self.assertEqual(rejected.failure_reason, "category_disabled")
        # A disabled positive remains a competitor; it cannot cause a false match for another target.
        result = self.many(batch(leaf({"portrait": 2, "signature": 1}, settings)), [["signature"]], settings)[0][0]
        self.assertFalse(result.passed)
        self.assertEqual(result.predicted_category, "portrait")

    def test_standard_strict_decisions_use_current_adapter_without_strategy_changes(self):
        from app.business.services import VerificationService
        from app.processing import detection_service
        verifier = VerificationService()
        for scores, passed in (({"portrait": 2}, True), ({"portrait": 0, "blank": .01}, False)):
            result = self.many(batch(leaf(scores)))[0][0]
            mapped = verifier._image_match_from_siglip_result(result, None)
            summary = verifier._verification_summary("template", [{**mapped, "anchor_type": "image", "required": True, "weight": 1}])
            for mode in ("standard", "strict"):
                final = detection_service._candidate_from_anchor_strategy(
                    {"template_id": "template", "final_confidence_threshold": .75},
                    summary, [{"data_type": "image"}], {}, mode, {})
                self.assertEqual(final["final_passed"], passed)
                self.assertEqual(summary["image_anchor_score"], float(passed))

    def test_staged_verification_with_real_crop_adapter_and_service_contract(self):
        from PIL import Image
        from app.business import services
        from app.business.image_verification_category_service import ImageVerificationCategory
        from app.processing import detection_service as detection
        registry = [ImageVerificationCategory(**{**item, "evidence_temperature": 1.}) for item in categories()]
        field = {"id": "image-anchor", "data_type": "image", "page_number": 1,
                 "image_category": "portrait", "field_name": "photo", "display_label": "Photo",
                 "required_for_verification": True, "verification_weight": 1,
                 "roi": {"x_ratio": 0., "y_ratio": 0., "width_ratio": .5, "height_ratio": .5}}
        for mode in ("standard", "strict"):
            for payload, expected in ((batch(leaf({"portrait": 2})), True),
                                      (batch(leaf({"blank": 2})), False),
                                      (batch({}), False), ({"results": [], "count": 0}, False)):
                verifier = services.VerificationService()
                with self.subTest(mode=mode, payload=payload), \
                     patch.object(detection, "verification_service", verifier), \
                     patch.object(services, "list_image_verification_categories", return_value=registry), \
                     patch.object(services, "_load_image_source", side_effect=lambda _: Image.new("RGB", (40, 40))), \
                     patch.object(adapter, "is_runtime_configured", return_value=True), \
                     patch.object(adapter, "remote_verify_image_logits_batch", return_value=payload) as remote, \
                     patch.object(adapter, "remote_verify_image_logits", side_effect=AssertionError("No single retry")), \
                     patch.object(detection, "ocr_rois", side_effect=AssertionError("No OCR")):
                    results, debug, *_ = detection._run_staged_anchor_batches(
                        [({"template_id": "template"}, [field])], {1: "page.png"}, {}, mode)
                remote.assert_called_once()
                self.assertEqual(results["template"]["passed"], expected)
                self.assertEqual(results["template"]["checked_fields"][0]["passed"], expected)
                self.assertEqual(debug["stage_count"], 1)


class SiglipGatewayContractTests(unittest.TestCase):
    def request(self, envelope):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
        with patch.object(client, "GATEWAY_URL", "https://gateway.invalid"), \
             patch.object(client.urllib.request, "urlopen", return_value=response):
            return client._post_predict(client.ModelRuntimeKind.IMAGE_VERIFICATION, {})

    def test_current_envelope_unwrapped_once(self):
        data = decision(scores={"portrait": 2})
        self.assertEqual(self.request({"data": data, "meta": {"model": "pipeline-router-v1"}}), data)

    def test_invalid_envelopes_rejected(self):
        for envelope in (None, [], {}, {"data": None, "meta": {}}, {"data": [], "meta": {}},
                         {"data": decision()}, {"data": {}, "meta": {}, "error": {"code": "FAILED"}},
                         {"data": {}, "meta": {}, "success": False}, {"result": decision(), "success": True}):
            with self.subTest(envelope=envelope), self.assertRaises(client.ModelRuntimeUnavailableError):
                self.request(envelope)

    def test_gateway_error_timeout_and_invalid_json(self):
        for error in (TimeoutError("timed out"), urllib.error.URLError("offline"),
                      urllib.error.HTTPError("https://gateway.invalid", 502, "Bad Gateway", {},
                                             io.BytesIO(b'{"error":{"code":"UPSTREAM_MODEL_ERROR"}}'))):
            with self.subTest(error=error), patch.object(client, "GATEWAY_URL", "https://gateway.invalid"), \
                 patch.object(client.urllib.request, "urlopen", side_effect=error), \
                 patch.object(client, "_path_to_data_url", return_value="data:image/png;base64,fixture"):
                with self.assertRaisesRegex(RuntimeError, "runtime unavailable"):
                    adapter.verify_image_category("fixture.png", "portrait", categories())
                with self.assertRaisesRegex(RuntimeError, "runtime unavailable"):
                    adapter.verify_image_categories_batch(["fixture.png"], [["portrait"]], categories())
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'not-json'
        with patch.object(client, "GATEWAY_URL", "https://gateway.invalid"), \
             patch.object(client.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(client.ModelRuntimeUnavailableError, "invalid JSON"):
                client._post_predict(client.ModelRuntimeKind.IMAGE_VERIFICATION, {})

    def test_request_paths_payloads_and_no_v24_gate_config(self):
        with patch.object(client, "is_runtime_configured", return_value=True), \
             patch.object(client, "_path_to_data_url", return_value="encoded"), \
             patch.object(client, "_post_predict", return_value={}) as post:
            client.remote_verify_image_logits("image.png", "portrait", payloads())
            self.assertEqual(post.call_args.args[1], {"image": "encoded", "image_category": "portrait", "categories": payloads()})
            self.assertEqual(client.MODEL_RUNTIME_GATEWAY_PATH[client.ModelRuntimeKind.IMAGE_VERIFICATION], "/paddle/api/v1/image-verifications")
            client.remote_verify_image_logits_batch(["image.png"], payloads())
            self.assertEqual(post.call_args.args[1], {"images": ["encoded"], "categories": payloads()})
            self.assertEqual(post.call_args.kwargs["path_override"], "/paddle/api/v1/image-classification-batches")
            self.assertEqual(post.call_args.kwargs["timeout"], 240)
