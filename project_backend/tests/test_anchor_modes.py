import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

from test_admin_anchor_detection import load

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.business import services
from app.api.schemas import VerificationStrategyUpdate
from pydantic import ValidationError


class AnchorModeTests(unittest.TestCase):
    def strict_service(self, failed_type=None):
        calls = []
        def verify(template_id, paths, fields, **kwargs):
            kind = fields[0]['data_type']
            calls.append(kind)
            return {'passed': kind != failed_type, 'checked_fields': [
                {'passed': kind != failed_type, 'anchor_type': kind, 'required': False,
                 'weight': 1, 'score': 0 if kind == failed_type else 1} for field in fields],
                 'timing': {'total_verification': .1, 'model_calls': [kind]}}
        verification = services.VerificationService()
        namespace = dict(time=time, verification_service=SimpleNamespace(
            verify_template=verify, _verification_summary=verification._verification_summary))
        return load({'_verify_sequential_anchor_template'}, namespace), calls

    def test_strict_finishes_text_before_image_even_if_fields_are_reversed(self):
        service, calls = self.strict_service()
        fields = [{'data_type': 'image'}, {'data_type': 'text'}]
        result = service._verify_sequential_anchor_template('template', fields, {1: 'page.png'})
        self.assertEqual(calls, ['text', 'image'])
        self.assertTrue(result['passed'])
        self.assertEqual(result['timing']['model_calls'], ['text', 'image'])

    def test_strict_text_failure_skips_image_including_optional_anchor_failure(self):
        service, calls = self.strict_service('text')
        result = service._verify_sequential_anchor_template('template',
            [{'data_type': 'text'}, {'data_type': 'image'}], {1: 'page.png'})
        self.assertEqual(calls, ['text'])
        self.assertFalse(result['passed'])
        self.assertTrue(result['image_verification_skipped'])

    def test_strict_image_failure_is_rejected(self):
        service, calls = self.strict_service('image')
        result = service._verify_sequential_anchor_template('template',
            [{'data_type': 'text'}, {'data_type': 'image'}], {1: 'page.png'})
        self.assertEqual(calls, ['text', 'image'])
        self.assertFalse(result['passed'])
        self.assertEqual(result['failed_stage'], 'image')

    def test_standard_text_and_image_overlap_and_preserve_request_context(self):
        barrier = Barrier(2, timeout=2)
        flag = ContextVar('test_request_flag', default=False)
        seen = []
        def text(*args):
            seen.append(('text', flag.get()))
            barrier.wait()
            return {'text': {}}, {}, {'text_model_request_count': 1}
        def image(*args):
            seen.append(('image', flag.get()))
            barrier.wait()
            return {'image': {}}, {'image_model_request_count': 1}
        service = load({'_run_parallel_anchor_batches'}, dict(time=time,
            ThreadPoolExecutor=ThreadPoolExecutor, copy_context=copy_context,
            _run_global_text_anchor_batch=text,
            verification_service=SimpleNamespace(score_image_anchors_batch=image)))
        token = flag.set(True)
        try:
            result = service._run_parallel_anchor_batches([({}, [{'data_type': 'image'}])], {1: 'page.png'}, {})
        finally:
            flag.reset(token)
        self.assertEqual(set(seen), {('text', True), ('image', True)})
        self.assertEqual(result[0], {'text': {}})
        self.assertEqual(result[3], {'image': {}})

    def test_standard_evaluates_entire_pool_and_selects_highest_passing_score(self):
        templates = [dict(template_id=name, page_count=2, verification_anchors=[
            dict(id=name, data_type='text', expected_text=name)]) for name in ['first', 'best', 'failed']]
        scores = {'first': .8, 'best': .95, 'failed': .99}
        verify = Mock(side_effect=lambda template_id, *args, **kwargs: {'id': template_id})
        namespace = dict(time=time, normalize_verification_strategy=services.normalize_verification_strategy,
            verification_anchor_types=lambda strategy: {'text', 'image'},
            VERIFICATION_STRATEGY_STANDARD='standard', VERIFICATION_STRATEGY_STRICT='strict',
            DETECTION_BATCH_MODE_GLOBAL='global',
            _load_anchor_strategy_templates=Mock(return_value=(templates, {})),
            _run_parallel_anchor_batches=Mock(return_value=({}, {}, {}, {}, {})),
            verification_service=SimpleNamespace(verify_template=verify, DEFAULT_VERIFICATION_THRESHOLD=.75),
            _candidate_from_anchor_strategy=lambda template, *args: dict(template_id=template['template_id'],
                final_score=scores[template['template_id']], final_passed=template['template_id'] != 'failed'),
            _persist_selected_processing_pages=Mock(return_value=[]), _detection_timing_debug=Mock(return_value={}),
            _detection_preview_url=lambda path: path,
            DETECTION_VERSION='test', _ms=lambda value: value,
            PIPELINE_CONFIG=SimpleNamespace(to_debug_dict=lambda: {}))
        service = load({'_detect_template_anchor_strategy', '_ordered_anchor_templates',
                        '_anchor_strategy_template_fields'}, namespace)
        result = service._detect_template_anchor_strategy('query', [
                {'page_index': 1, 'normalized_path': 'page1.png'},
                {'page_index': 2, 'normalized_path': 'page2.png'}],
                {}, None, time.perf_counter(), 'image', 'standard', 'global')
        self.assertEqual(verify.call_count, 3)
        self.assertEqual(result['best_candidate']['template_id'], 'best')
        self.assertEqual(result['pages'][1]['best_candidate']['template_id'], 'best')
        self.assertEqual(result['pages'][1]['best_candidate']['query_page_index'], 2)
        self.assertEqual(result['pages'][1]['best_candidate']['extraction_image_path'], 'page2.png')
        self.assertFalse(result['debug']['anchor_strategy']['early_exit'])
        self.assertTrue(result['debug']['layout_retrieval_skipped'])

    def test_user_main_page_keeps_auto_roi_for_other_pages(self):
        template = dict(id='template', name='Card', status='active', detection_mode='main_page',
                        main_page_number=2, page_count=2, final_confidence_threshold=.75)
        field = dict(id='photo', data_type='image', page_number=2)
        namespace = dict(time=time, normalize_verification_strategy=services.normalize_verification_strategy,
            verification_anchor_types=lambda strategy: {'text', 'image'},
            VERIFICATION_STRATEGY_STANDARD='standard', VERIFICATION_STRATEGY_STRICT='strict',
            DETECTION_BATCH_MODE_GLOBAL='global',
            _fetch_template_cached=Mock(return_value=(template, False, {})),
            _fetch_verification_fields_cached=Mock(return_value=([field], False, {})),
            _run_parallel_anchor_batches=Mock(return_value=({}, {}, {}, {}, {})),
            verification_service=SimpleNamespace(verify_template=Mock(return_value=dict(
                passed=True, score=1, text_anchor_score=1, image_anchor_score=1, checked_fields=[])),
                DEFAULT_VERIFICATION_THRESHOLD=.75),
            DEFAULT_FINAL_CONFIDENCE_THRESHOLD=.75,
            _auto_roi_region_items=lambda page: {'page_index': page['page_index'], 'regions': [{'id': 'auto'}]},
            _persist_selected_processing_pages=Mock(return_value=[]), _detection_timing_debug=Mock(return_value={}),
            _detection_preview_url=lambda path: path, _runtime_ms=lambda value: value,
            DETECTION_VERSION='test', _ms=lambda value: value,
            PIPELINE_CONFIG=SimpleNamespace(to_debug_dict=lambda: {}))
        service = load({'_detect_template_anchor_strategy', '_ordered_anchor_templates',
                        '_anchor_strategy_template_fields', '_candidate_from_anchor_strategy',
                        '_attach_main_page_auto_roi_pages'}, namespace)
        pages = [dict(page_index=number, normalized_path=f'page{number}.png') for number in [1, 2, 3]]
        result = service._detect_template_anchor_strategy('query', pages, {}, None,
            time.perf_counter(), 'image', 'standard', 'global', selected_template_id='template', user_context=True)
        self.assertTrue(result['matched'])
        self.assertEqual(result['best_candidate']['query_page_index'], 2)
        self.assertEqual(result['best_candidate']['extraction_image_path'], 'page2.png')
        self.assertEqual([page['page_index'] for page in result['main_page_auto_roi_pages']], [1, 3])
        self.assertEqual(result['main_page_auto_roi_total_regions'], 2)

    def test_score_uses_normalized_text_image_weights_and_ignores_layout(self):
        service = load({'_candidate_from_anchor_strategy'}, dict(
            DEFAULT_FINAL_CONFIDENCE_THRESHOLD=.75,
            _detection_preview_url=lambda path: path, _runtime_ms=lambda value: value, _ms=lambda value: value))
        template = dict(template_id='template', final_confidence_threshold=.5,
                        text_anchor_weight=.6, image_anchor_weight=.4)
        fields = [dict(data_type='text'), dict(data_type='image')]
        verification = dict(passed=True, score=1, text_anchor_score=.8, image_anchor_score=.6)
        for layout_weight in [0, .5, 1]:
            candidate = service._candidate_from_anchor_strategy({**template, 'layout_weight': layout_weight},
                verification, fields, {1: 'page.png'}, 'standard', {})
            self.assertEqual(candidate['final_score'], .72)
            self.assertEqual(candidate['effective_matching_weights'], dict(layout=0, text_anchor=.6, image_anchor=.4))

    def test_only_two_public_modes_and_legacy_settings_fall_back_to_standard(self):
        self.assertEqual(services.VERIFICATION_STRATEGIES, {'standard', 'strict'})
        for value in ['text_only', 'text_image', 'image_only', 'layout_only']:
            self.assertEqual(services.normalize_verification_strategy(value), 'standard')
            with self.assertRaises(ValidationError):
                VerificationStrategyUpdate(verification_strategy=value)
        for value in ['standard', 'strict']:
            self.assertEqual(VerificationStrategyUpdate(verification_strategy=value).verification_strategy, value)


if __name__ == '__main__':
    unittest.main()
