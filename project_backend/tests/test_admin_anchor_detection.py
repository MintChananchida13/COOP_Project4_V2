import ast
import __future__
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'app/processing/detection_service.py'


def load(names, namespace):
    from app.processing import document_aspect, staged_verification
    namespace.setdefault('document_aspect', document_aspect)
    namespace.setdefault('staged_verification', staged_verification)
    if '_detect_template_anchor_strategy' in names:
        from test_staged_verification import fake_batch
        names = names | {'_run_staged_anchor_batches', '_staged_anchor_callbacks'}
        namespace.setdefault('_run_global_text_anchor_batch', Mock(side_effect=lambda templates, *args: fake_batch(templates)[:3]))
        if hasattr(namespace.get('verification_service'), '__dict__') and not hasattr(namespace['verification_service'], 'score_image_anchors_batch'):
            namespace['verification_service'].score_image_anchors_batch = Mock(side_effect=lambda fields, paths: ({field['id']: {} for field in fields}, {}))
        parallel = namespace.get('_run_parallel_anchor_batches')
        if isinstance(parallel, Mock) and parallel.side_effect is None:
            parallel.side_effect = fake_batch
    definitions = [node for node in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in definitions:
        node.decorator_list = []
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)
    return SimpleNamespace(**namespace)


class AdminAnchorDetectionTests(unittest.TestCase):
    def test_admin_dispatch_skips_layout_and_system_strategy(self):
        delegate = Mock(return_value={'matched': True})
        normalize = Mock(return_value=[{'page_index': 1, 'normalized_path': 'page.png'}])
        settings = Mock()
        namespace = dict(time=time, uuid4=lambda: SimpleNamespace(hex='query'),
                         DetectionRequestCache=Mock, _prepare_query_pages=Mock(return_value=['page.png']),
                         _normalize_query_pages=normalize, _detect_template_anchor_strategy=delegate,
                         _finish_detection_result=lambda value: value,
                         global_settings_service=settings, normalize_verification_strategy=lambda value: 'strict' if value == 'strict' else 'standard',
                         DETECTION_BATCH_MODE_GLOBAL='global', DETECTION_RETRIEVAL_LIMIT=5,
                         USER_DETECTION_RETRIEVAL_LIMIT=5, DETECTION_VERIFICATION_CANDIDATE_LIMIT=5)
        service = load({'detect_template_dev'}, namespace)
        result = service.detect_template_dev(b'image', include_template_id='draft', admin_context=True,
                                              cleanup_generated=False, verification_strategy_override='layout_only')
        self.assertTrue(result['matched'])
        self.assertTrue(normalize.call_args.kwargs['skip_normalization'])
        self.assertEqual(delegate.call_args.kwargs['selected_template_id'], 'draft')
        self.assertEqual(delegate.call_args.args[-2:], ('standard', 'global'))
        settings.assert_not_called()
        self.assertEqual(settings.mock_calls, [])

    def run_selected(self, fields, *, score=.9, required_passed=True, page_count=1, mode='all_pages', main_page=1,
                     strategy='standard', page_indexes=(1,), parallel=None):
        template = dict(id='draft', name='Draft', status='draft', page_count=page_count,
                        detection_mode=mode, main_page_number=main_page, final_confidence_threshold=.75)
        loader = Mock(side_effect=AssertionError('Published candidates must not be loaded'))
        verify = Mock(side_effect=lambda tid, paths, stage_fields, **kwargs: dict(
            passed=required_passed, score=score, required_passed=required_passed,
            checked_fields=[{**field, 'passed': required_passed, 'score': score} for field in stage_fields],
            text_anchor_score=score, image_anchor_score=score))
        no_anchors = Mock(return_value=dict(passed=False, score=0, checked_fields=[]))
        namespace = dict(time=time, verification_anchor_types=lambda strategy: {'text', 'image'},
                         normalize_verification_strategy=lambda value: value,
                         VERIFICATION_STRATEGY_STANDARD='standard', VERIFICATION_STRATEGY_STRICT='strict',
                         _run_parallel_anchor_batches=parallel if parallel is not None else Mock(return_value=({}, {}, {}, {}, {})),
                         _verify_sequential_anchor_template=verify,
                         _fetch_template_cached=Mock(return_value=(template, False, {})),
                         _fetch_verification_fields_cached=Mock(return_value=(fields, False, {})),
                         _load_anchor_strategy_templates=loader,
                         verification_service=SimpleNamespace(verify_template=verify, _verification_summary=no_anchors,
                                                               DEFAULT_VERIFICATION_THRESHOLD=.75),
                         DEFAULT_FINAL_CONFIDENCE_THRESHOLD=.75,
                         DETECTION_BATCH_MODE_GLOBAL='global', VERIFICATION_STRATEGY_IMAGE_ONLY='image_only',
                         _persist_selected_processing_pages=Mock(return_value=[]),
                         _detection_timing_debug=Mock(return_value={}), _detection_preview_url=lambda path: path,
                         _ms=lambda value: (value or 0)*1000, _runtime_ms=lambda value: value,
                         DETECTION_VERSION='test', PIPELINE_CONFIG=SimpleNamespace(to_debug_dict=lambda: {}))
        service = load({'_detect_template_anchor_strategy', '_candidate_from_anchor_strategy',
                        '_ordered_anchor_templates', '_anchor_strategy_template_fields'}, namespace)
        progress = Mock()
        result = service._detect_template_anchor_strategy('query', [
                    {'page_index': index, 'normalized_path': f'page{index}.png'} for index in page_indexes],
                    {}, None, time.perf_counter(), 'image', strategy, 'global',
                    selected_template_id='draft', progress_callback=progress)
        loader.assert_not_called()
        self.assertTrue(result['debug']['layout_retrieval_skipped'])
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['template_id'], 'draft')
        self.assertEqual(progress.call_args.args[0]['total_rank'], 1)
        return result, verify

    def test_selected_draft_matches_using_anchors_without_signatures(self):
        result, _ = self.run_selected([dict(id='text', data_type='text', expected_text='Hello', page_number=1)])
        self.assertTrue(result['matched'])
        self.assertEqual(result['best_candidate']['final_score'], 1)
        self.assertEqual(result['best_candidate']['layout_score'], 0)

    def test_missing_anchors_and_any_failed_anchor_cannot_pass(self):
        for fields, kwargs in [([], {}), ([dict(data_type='image')], {'required_passed': False})]:
            with self.subTest(fields=fields, kwargs=kwargs):
                result, _ = self.run_selected(fields, **kwargs)
                self.assertFalse(result['matched'])

    def test_all_pages_mismatch_cannot_pass(self):
        result, _ = self.run_selected([dict(data_type='image')], page_count=2)
        self.assertFalse(result['matched'])
        self.assertEqual(result['candidates'][0]['decision_reason'], 'page_count_mismatch')

    def test_main_page_filters_other_pages_and_allows_extra_pages(self):
        result, verify = self.run_selected([dict(data_type='image', page_number=1),
                                            dict(data_type='image', page_number=2)],
                                           page_count=2, mode='main_page')
        self.assertTrue(result['matched'])
        self.assertEqual([field['page_number'] for field in verify.call_args.args[2]], [1])

    def test_page_count_mismatch_calls_neither_batches_nor_verification(self):
        for strategy in ('standard', 'strict'):
            with self.subTest(strategy=strategy):
                parallel = Mock(return_value=({}, {}, {}, {}, {}))
                result, verify = self.run_selected([dict(data_type='text', expected_text='Hello', page_number=1)],
                    page_count=2, strategy=strategy, parallel=parallel)
                parallel.assert_not_called()
                verify.assert_not_called()
                self.assertFalse(result['matched'])
                self.assertEqual(result['candidates'][0]['evaluation_status'], 'skipped')
                self.assertEqual(result['candidates'][0]['decision_reason'], 'page_count_mismatch')
                self.assertEqual(result['debug']['anchor_strategy']['page_count_prefilter']['rejected_template_ids'], ['draft'])

    def test_missing_main_page_is_rejected_before_models(self):
        parallel = Mock(return_value=({}, {}, {}, {}, {}))
        result, verify = self.run_selected([dict(data_type='image', page_number=2)],
            page_count=2, mode='main_page', main_page=2, parallel=parallel)
        parallel.assert_not_called()
        verify.assert_not_called()
        self.assertFalse(result['matched'])

    def test_main_page_present_allows_extra_document_pages(self):
        parallel = Mock(return_value=({}, {}, {}, {}, {}))
        result, verify = self.run_selected([dict(data_type='image', page_number=2)],
            page_count=2, mode='main_page', main_page=2, page_indexes=(1, 2, 3), parallel=parallel)
        parallel.assert_called_once()
        verify.assert_called_once()
        self.assertTrue(result['matched'])

    def test_unknown_template_page_count_keeps_existing_verification(self):
        parallel = Mock(return_value=({}, {}, {}, {}, {}))
        result, verify = self.run_selected([dict(data_type='text', expected_text='Hello', page_number=1)],
            page_count=None, parallel=parallel)
        parallel.assert_called_once()
        verify.assert_called_once()
        self.assertTrue(result['matched'])


if __name__ == '__main__':
    unittest.main()
