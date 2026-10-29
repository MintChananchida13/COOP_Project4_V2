import ast
import base64
import copy
import __future__
import json
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.processing.detection_work_policy import detection_work_scope, include_detection_diagnostics, compact_detection_candidates
from app.api.detection_response import user_detection_response


def load_functions(names, namespace):
    path = ROOT / 'app/processing/detection_service.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in definitions:
        node.decorator_list = []
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace


def expand_candidates(result):
    pool = result['candidate_pool']
    def resolve(value):
        return pool[value['candidate_ref']] if isinstance(value, dict) and 'candidate_ref' in value else value
    return {**result, 'best_candidate': resolve(result['best_candidate']),
            'candidates': [resolve(item) for item in result['candidates']],
            'pages': [{**page, **({'best_candidate': resolve(page['best_candidate'])} if 'best_candidate' in page else {}),
                       'candidates': [resolve(item) for item in page['candidates']]}
                      for page in result['pages']]}


class UserDetectionWorkTests(unittest.TestCase):
    def test_finalizer_preserves_admin_and_negotiates_user_candidate_format(self):
        encode = Mock(return_value='data:image/png;base64,final')
        namespace = {'include_detection_diagnostics': include_detection_diagnostics,
                     'compact_detection_candidates': compact_detection_candidates,
                     '_image_to_data_url': encode, 'Path': Path}
        load_functions({'_finish_detection_result'}, namespace)
        candidate = {'template_id': 'template', 'final_passed': True, 'final_score': .9,
                     'extraction_image_path': 'processed.png', 'template_rois': [{'id': 'roi'}]}
        source = {'matched': True, 'best_candidate': candidate, 'candidates': [candidate],
                  'pages': [{'candidates': [candidate], 'original_image_path': 'original.png'}]}
        @detection_work_scope
        def finish(*, user_context=False, compact_candidates=False):
            return namespace['_finish_detection_result'](source)
        self.assertIs(finish(), source)
        encode.assert_not_called()
        legacy = finish(user_context=True)
        self.assertNotIn('candidate_pool', legacy)
        pooled = finish(user_context=True, compact_candidates=True)
        restored = expand_candidates(pooled)
        restored.pop('candidate_pool')
        self.assertEqual(restored, legacy)
        self.assertEqual(encode.call_count, 2)
        self.assertTrue(include_detection_diagnostics())
        self.assertFalse(compact_detection_candidates())

    def test_diagnostic_preview_helpers_do_not_touch_files_or_images_for_user(self):
        path = ROOT / 'app/business/services.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        names = {'_image_path_to_data_url', '_pil_image_to_data_url'}
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {'include_detection_diagnostics': include_detection_diagnostics,
                     'Path': Mock(side_effect=AssertionError('unexpected diagnostic file read'))}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec',
                     flags=__future__.annotations.compiler_flag), namespace)
        image = Mock()
        @detection_work_scope
        def inspect(*, user_context=False):
            self.assertIsNone(namespace['_image_path_to_data_url']('unused.png'))
            self.assertIsNone(namespace['_pil_image_to_data_url'](image))
        inspect(user_context=True)
        image.save.assert_not_called()
        namespace['Path'].assert_not_called()

    def test_policy_is_thread_local_and_restored_after_failure(self):
        barrier = threading.Barrier(2)
        @detection_work_scope
        def inspect(*, user_context=False):
            barrier.wait(timeout=3)
            return include_detection_diagnostics()
        with ThreadPoolExecutor(max_workers=2) as executor:
            user = executor.submit(inspect, user_context=True)
            admin = executor.submit(inspect)
            self.assertFalse(user.result())
            self.assertTrue(admin.result())
        @detection_work_scope
        def fail(*, user_context=False):
            raise RuntimeError('failed')
        with self.assertRaises(RuntimeError):
            fail(user_context=True)
        self.assertTrue(include_detection_diagnostics())

    def test_user_skips_preview_reads_but_required_final_image_can_encode(self):
        reads = Mock(return_value=b'pixels')
        namespace = {'include_detection_diagnostics': include_detection_diagnostics,
                     'base64': base64}
        load_functions({'_image_to_data_url', '_detection_preview_url'}, namespace)
        path = SimpleNamespace(read_bytes=reads)
        @detection_work_scope
        def inspect(*, user_context=False):
            self.assertIsNone(namespace['_detection_preview_url']('unused.png'))
            self.assertIsNone(namespace['_image_to_data_url'](path))
            reads.assert_not_called()
            return namespace['_image_to_data_url'](path, required=True)
        self.assertTrue(inspect(user_context=True).startswith('data:image/png;base64,'))
        self.assertEqual(reads.call_count, 1)
        namespace['_image_to_data_url'](path)
        self.assertEqual(reads.call_count, 2)

    def test_only_unused_extraction_test_is_skipped_and_admin_ocr_is_retained(self):
        ocr = Mock(return_value={'field': {'text': 'Name', 'confidence': .9}})
        namespace = {'include_detection_diagnostics': include_detection_diagnostics, 'time': time,
                     'ocr_rois': ocr, 'OcrUnavailableError': RuntimeError, '_timing_ms_map': lambda value: value,
                     '_detection_debug_url': lambda value: None}
        load_functions({'_run_extraction_test'}, namespace)
        fields = [{'id': 'field', 'page_number': 1, 'field_name': 'Name', 'data_type': 'text'}]
        projected = [{'field_id': 'field', 'template_roi': {'x_ratio': .1, 'y_ratio': .1,
                      'width_ratio': .3, 'height_ratio': .2}}]
        @detection_work_scope
        def inspect(*, user_context=False):
            return namespace['_run_extraction_test']('template', fields, projected, 'image.png', 1, 'template_canvas')
        self.assertEqual(inspect(user_context=True)['status'], 'skipped')
        ocr.assert_not_called()
        self.assertEqual(inspect()['tested_count'], 1)
        self.assertEqual(ocr.call_count, 1)

    def test_compaction_roundtrip_preserves_scores_variants_roi_and_log_metadata(self):
        candidate = {'template_id': 'template', 'final_passed': True, 'final_score': .9,
                     'query_page_index': 1, 'roi_coordinate_space': 'processing',
                     'template_rois': [{'id': 'roi', 'x_ratio': .1}],
                     'metadata': {'version_name': 'v1'}, 'verification': {'score': .8, 'passed': True},
                     'processing_pages': [{'processingImageReference': 'page_1.png'}]}
        page_variant = {**candidate, 'final_score': .85, 'query_page_index': 2}
        source = {'matched': True, 'best_candidate': candidate, 'candidates': [candidate],
                  'pages': [{'page_index': 1, 'best_candidate': candidate, 'candidates': [copy.deepcopy(candidate)]},
                            {'page_index': 2, 'best_candidate': page_variant, 'candidates': [page_variant]}]}
        snapshot = copy.deepcopy(source)
        original = user_detection_response(source)
        compact = user_detection_response(source, compact_candidates=True)
        expanded = expand_candidates(compact)
        expanded.pop('candidate_pool')
        self.assertEqual(expanded, original)
        self.assertEqual(source, snapshot)
        self.assertEqual(len(compact['candidate_pool']), 2)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(original)))

    def test_lazy_preview_only_for_selected_changed_passing_pages(self):
        candidate = {'template_id': 'template', 'final_passed': True, 'extraction_image_path': 'processed.png'}
        source = {'matched': True, 'best_candidate': candidate, 'pages': [
            {'original_image_path': 'original.png', 'candidates': [candidate]},
            {'original_image_path': 'processed.png', 'candidates': [candidate]},
            {'original_image_path': 'original.png', 'candidates': [{**candidate, 'final_passed': False}]},
        ]}
        encode = Mock(return_value='data:image/png;base64,final')
        result = user_detection_response(source, preview_loader=encode)
        encode.assert_called_once_with('processed.png')
        self.assertIn('processing_image_preview_url', result['pages'][0])
        self.assertNotIn('processing_image_preview_url', result['pages'][1])
        self.assertNotIn('processing_image_preview_url', result['pages'][2])
        encode.reset_mock()
        user_detection_response({**source, 'matched': False}, preview_loader=encode)
        encode.assert_not_called()

    def test_finalization_occurs_before_cleanup_for_all_six_modes(self):
        for mode in ['standard', 'strict', 'layout_only', 'text_only', 'text_image', 'image_only']:
            with self.subTest(mode=mode):
                events = []
                candidate = {'template_id': 'template', 'final_passed': True, 'final_score': .9, 'retrieval_score': .8}
                page = {'page_index': 1, 'best_candidate': candidate, 'candidates': [candidate]}
                result = {'matched': True, 'best_candidate': candidate, 'candidates': [candidate], 'pages': [page]}
                def finish(value):
                    self.assertNotIn('cleanup', events)
                    events.append('finish')
                    return value
                namespace = dict(time=time, uuid4=lambda: SimpleNamespace(hex='query'),
                    DetectionRequestCache=lambda: None, _prepare_query_pages=lambda *args, **kwargs: [Path('original.png')],
                    _normalize_query_pages=lambda *args, **kwargs: [{'page_index': 1, 'normalized_path': 'processed.png'}],
                    DETECTION_RETRIEVAL_LIMIT=3, USER_DETECTION_RETRIEVAL_LIMIT=3, DETECTION_VERIFICATION_CANDIDATE_LIMIT=3,
                    DETECTION_BATCH_MODE_GLOBAL='global', normalize_verification_strategy=lambda value: value,
                    normalize_detection_batch_mode=lambda value: value, _detect_template_text_only=lambda *args: result,
                    _detect_template_anchor_strategy=lambda *args: result,
                    _fetch_template_cached=lambda *args: (None, False, {}), _detect_page=lambda *args, **kwargs: page,
                    _is_confirmed_main_page_candidate=lambda value: False, _aggregate_candidates=lambda value: [candidate],
                    _persist_selected_processing_pages=lambda *args: [], _detection_engine=lambda value: mode,
                    DETECTION_VERSION='version', DETECTION_THRESHOLD=.5, SAVE_DEBUG_ARTIFACTS=False,
                    PIPELINE_CONFIG=SimpleNamespace(to_debug_dict=lambda: {}), _detection_timing_debug=lambda *args, **kwargs: {},
                    _finish_detection_result=finish, _cleanup_query_artifacts=lambda value: events.append('cleanup'))
                for constant in ['TEXT_ONLY', 'TEXT_IMAGE', 'IMAGE_ONLY']:
                    namespace['VERIFICATION_STRATEGY_' + constant] = constant.lower()
                load_functions({'detect_template_dev'}, namespace)
                returned = namespace['detect_template_dev'](b'image', verification_strategy_override=mode)
                self.assertTrue(returned['matched'])
                self.assertEqual(events, ['finish', 'cleanup'])


if __name__ == '__main__':
    unittest.main()
