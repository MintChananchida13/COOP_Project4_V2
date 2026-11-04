import unittest
from unittest.mock import patch

from app.business.services import VerificationService
from app.processing import detection_service as detection, staged_verification


def anchor(anchor_id, kind='text', page=1, passed=True):
    return {'id': anchor_id, 'data_type': kind, 'page_number': page, 'expected_text': 'MATCH',
            'match_type': 'exact', 'field_name': anchor_id, 'display_label': anchor_id,
            'required_for_verification': False, 'verification_weight': 99,
            'image_category': 'photo', 'roi': {'x_ratio': .1, 'y_ratio': .1,
                'width_ratio': .2, 'height_ratio': .2, 'fixture_pass': passed}}


def fake_batch(templates, *args):
    text, images = {}, {}
    for _, fields in templates:
        for field in fields:
            (images if field.get('data_type') == 'image' else text)[field['id']] = {}
    return text, {}, {}, images, {}


class StagedVerificationTests(unittest.TestCase):
    def execute(self, mode, templates, page_paths=None):
        paths = page_paths or {1: 'page1.png', 2: 'page2.png'}
        seen, text_batches, image_batches = [], [], []
        def ocr(path, items, timing):
            text_batches.append([item['id'] for item in items])
            timing['roi_crop_count'] = timing.get('roi_crop_count', 0) + len(items)
            timing['text_det_inference_ms'] = timing.get('text_det_inference_ms', 0) + 1
            timing['text_rec_inference_ms'] = timing.get('text_rec_inference_ms', 0) + 1
            timing.setdefault('model_calls', []).extend([{'call_id': f'det-{len(text_batches)}'}, {'call_id': f'rec-{len(text_batches)}'}])
            result = {}
            for item in items:
                self.assertNotIn(item['id'], seen)
                seen.append(item['id'])
                result[item['id']] = {'text': 'MATCH' if item['roi']['fixture_pass'] else 'UNRELATED', 'confidence': 1}
            return result
        def images(fields, paths):
            image_batches.append([field['id'] for field in fields])
            result = {}
            for field in fields:
                self.assertNotIn(field['id'], seen)
                seen.append(field['id'])
                passed = field['roi']['fixture_pass']
                result[field['id']] = {'passed': passed, 'score': float(passed), 'field_score': float(passed),
                                      'image_category': 'photo', 'verification_threshold': .5,
                                      'failure_reason': 'passed' if passed else 'category_mismatch',
                                      'status': 'matched' if passed else 'mismatched'}
            return result, {'image_roi_count': len(fields), 'siglip_inference_ms': 1 if fields else 0,
                            'model_calls': [{'call_id': f'image-{len(image_batches)}'}] if fields else []}
        verifier = VerificationService()
        with patch.object(detection, 'verification_service', verifier), \
             patch.object(detection, 'ocr_rois', side_effect=ocr), \
             patch.object(verifier, 'score_image_anchors_batch', side_effect=images), \
             patch.object(verifier, '_score_image_anchor', side_effect=AssertionError('No single-image retry')), \
             patch('app.business.services.ocr_roi', side_effect=AssertionError('No OCR retry')), \
             patch('app.business.services._crop_anchor_roi_to_temp', side_effect=AssertionError('No diagnostic recrop')):
            result = detection._run_staged_anchor_batches(templates, paths, {}, mode)
        return result, seen, text_batches, image_batches

    def test_mixed_multi_page_unequal_counts_early_rejection_and_optional_failure(self):
        templates = [({'template_id': 'reject-first'}, [anchor('dup', passed=False), anchor('must-not-run')]),
                     ({'template_id': 'short'}, [anchor('dup', 'image')]),
                     ({'template_id': 'long'}, [anchor('dup', page=2), anchor('photo', 'image')]),
                     ({'template_id': 'reject-second'}, [anchor('first'), anchor('bad', 'image', passed=False), anchor('never')])]
        for mode in ('standard', 'strict'):
            with self.subTest(mode=mode):
                (results, debug, _, _), seen, _, _ = self.execute(mode, templates)
                self.assertEqual({tid for tid, result in results.items() if result['passed']}, {'short', 'long'})
                self.assertEqual(len(results['reject-first']['checked_fields']), 1)
                self.assertEqual(len(results['reject-second']['checked_fields']), 2)
                self.assertEqual(debug['stage_count'], 2)
                self.assertEqual(debug['roi_crop_count'], 6)
                self.assertEqual(len(seen), len(set(seen)))
                self.assertFalse(any('must-not-run' in key or 'never' in key for key in seen))
                mapped = results['long']['checked_fields'][0]
                self.assertEqual((mapped['template_id'], mapped['anchor_id'], mapped['page_number']), ('long', 'dup', 2))

    def test_selection_uses_anchor_count_then_template_id_not_score(self):
        candidates = [{'template_id': 'z', 'verification_anchor_count': 3, 'final_passed': True, 'final_score': .1},
                      {'template_id': 'a', 'verification_anchor_count': 3, 'final_passed': True, 'final_score': .01},
                      {'template_id': 'short', 'verification_anchor_count': 1, 'final_passed': True, 'final_score': 1},
                      {'template_id': 'failed', 'verification_anchor_count': 99, 'final_passed': False, 'final_score': 1}]
        self.assertEqual([item['template_id'] for item in sorted(candidates, key=staged_verification.candidate_order)],
                         ['a', 'z', 'short', 'failed'])

    def test_completed_short_template_is_not_scheduled_in_later_stages(self):
        for mode in ('standard', 'strict'):
            (results, debug, _, _), seen, _, _ = self.execute(mode, [
                ({'template_id': 'short'}, [anchor('one', 'image')]),
                ({'template_id': 'long'}, [anchor('one'), anchor('two', 'image'), anchor('three', page=2)])])
            self.assertTrue(all(result['passed'] for result in results.values()))
            self.assertEqual(debug['stage_count'], 3)
            self.assertEqual(len(seen), 4)
            self.assertEqual([entry['template_id'] for entry in debug['stages'][2]['mapping']], ['long'])

    def test_all_templates_failing_returns_no_complete_pass(self):
        for mode in ('standard', 'strict'):
            (results, debug, _, _), seen, _, _ = self.execute(mode, [
                ({'template_id': 'text'}, [anchor('first', passed=False), anchor('never')]),
                ({'template_id': 'image'}, [anchor('first', 'image', passed=False), anchor('never')])])
            self.assertFalse(any(result['passed'] for result in results.values()))
            self.assertEqual(debug['stage_count'], 1)
            self.assertEqual(len(seen), 2)

    def test_no_anchors_and_missing_page_cannot_match_or_call_models(self):
        for mode in ('standard', 'strict'):
            (results, debug, _, _), seen, _, _ = self.execute(mode, [
                ({'template_id': 'empty'}, []), ({'template_id': 'missing'}, [anchor('a', page=3)])])
            self.assertFalse(any(result['passed'] for result in results.values()))
            self.assertEqual(seen, [])
            self.assertEqual(debug['roi_crop_count'], 0)

    def test_missing_batch_result_fails_closed_without_single_call_fallback(self):
        for mode in ('standard', 'strict'):
            with patch.object(detection, '_run_parallel_anchor_batches', return_value=({}, {}, {}, {}, {})), \
                 patch.object(detection, '_run_global_text_anchor_batch', return_value=({}, {}, {})), \
                 patch.object(detection.verification_service, 'score_image_anchors_batch', return_value=({}, {})), \
                 patch.object(detection.verification_service, 'verify_template', side_effect=AssertionError('No retry')):
                results, *_ = detection._run_staged_anchor_batches([
                    ({'template_id': 'text'}, [anchor('t')]), ({'template_id': 'image'}, [anchor('i', 'image')])],
                    {1: 'page.png'}, {}, mode)
            self.assertFalse(any(result['passed'] for result in results.values()))

    def test_strict_batches_text_before_images_within_each_stage(self):
        order = []
        def text(*args):
            order.append('text')
            return {}, {}, {}
        def image(*args):
            order.append('image')
            return {}, {}
        with patch.object(detection, '_run_global_text_anchor_batch', side_effect=text), \
             patch.object(detection.verification_service, 'score_image_anchors_batch', side_effect=image):
            batch, _ = detection._staged_anchor_callbacks({1: 'page.png'}, {}, 'strict')
            batch([({'template_id': 'a'}, [anchor('t')]), ({'template_id': 'b'}, [anchor('i', 'image')])])
        self.assertEqual(order, ['text', 'image'])

    def test_pre_publish_does_not_reverify_prefiltered_or_empty_staged_candidates(self):
        from app.business.services import AdminTemplateService
        candidate = {'template_id': 'skipped', 'selection_rule': 'all_anchors_then_count_then_template_id',
                     'verification_details': [], 'final_passed': False}
        admin = AdminTemplateService()
        with patch.object(admin, '_prepublish_detection_page_paths', return_value={1: 'page.png'}), \
             patch.object(VerificationService, 'verify_template', side_effect=AssertionError('Filtered template must not run')):
            self.assertEqual(admin._enrich_prepublish_candidate_verification_details([candidate], {}), [candidate])
