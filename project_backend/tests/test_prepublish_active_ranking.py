import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_admin_anchor_detection import load
from test_staged_verification import fake_batch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.business import services
from app.processing import detection_service
from app.processing import document_aspect


class PrepublishActiveRankingTests(unittest.TestCase):
    def detect(self, mode='standard', draft_score=.8, selected_active=False, mismatched_active=False,
               template_aspects=None, query_aspect=None):
        def template(template_id, status, score):
            return dict(template_id=template_id, template_status=status, page_count=1,
                        final_confidence_threshold=.75, verification_anchors=[
                            dict(id=template_id, data_type='text', expected_text=template_id, page_number=1, test_score=score)])
        pool = [template('active-a', 'active', .95), template('active-b', 'active', .85),
                template('disabled', 'nonactive', 1), template('other-draft', 'draft', 1),
                template('published', 'published', 1)]
        if mismatched_active:
            pool[0]['page_count'] = 2
        if selected_active:
            pool.append(template('selected', 'active', .1))
        selected = dict(id='selected', name='Selected', status='active' if selected_active else 'draft',
                        page_count=1, final_confidence_threshold=.75)
        fields = template('selected', selected['status'], draft_score)['verification_anchors']
        if draft_score >= .98:
            fields.append({**fields[0], 'id': 'selected-extra'})
        if template_aspects is not None:
            for item in pool:
                item['page_aspect_ratios'] = template_aspects.get(item['template_id'], {})
            selected['page_aspect_ratios'] = template_aspects.get('selected', {})
        def verification(template_id, paths, fields, **kwargs):
            score = fields[0]['test_score']
            return dict(passed=True, score=score, text_anchor_score=score, image_anchor_score=1,
                        checked_fields=[dict(passed=True, anchor_type='text', page_number=1)])
        verify = Mock(side_effect=verification)
        strict = Mock(side_effect=lambda templates, *args: fake_batch(templates)[:3])
        parallel = Mock(return_value=({}, {}, {}, {}, {}))
        pool_loader = Mock(return_value=(pool, {}))
        namespace = dict(time=time, normalize_verification_strategy=services.normalize_verification_strategy,
            verification_anchor_types=lambda strategy: {'text', 'image'},
            VERIFICATION_STRATEGY_STANDARD='standard', VERIFICATION_STRATEGY_STRICT='strict',
            DETECTION_BATCH_MODE_GLOBAL='global', DEFAULT_FINAL_CONFIDENCE_THRESHOLD=.75,
            _fetch_template_cached=Mock(return_value=(selected, False, {})),
            _fetch_verification_fields_cached=Mock(return_value=(fields, False, {})),
            _load_anchor_strategy_templates=pool_loader,
            _run_parallel_anchor_batches=parallel, _run_global_text_anchor_batch=strict,
            verification_service=SimpleNamespace(verify_template=verify, DEFAULT_VERIFICATION_THRESHOLD=.6),
            _persist_selected_processing_pages=Mock(return_value=[]), _detection_timing_debug=Mock(return_value={}),
            _detection_preview_url=lambda path: path, _runtime_ms=lambda value: value, _ms=lambda value: value,
            DETECTION_VERSION='test', PIPELINE_CONFIG=SimpleNamespace(to_debug_dict=lambda: {}))
        service = load({'_detect_template_anchor_strategy', '_candidate_from_anchor_strategy',
                        '_ordered_anchor_templates', '_anchor_strategy_template_fields'}, namespace)
        with patch.object(document_aspect, 'image_aspect', return_value=query_aspect):
            result = service._detect_template_anchor_strategy('detq_test', [{'page_index': 1, 'normalized_path': 'page.png'}],
                {}, None, time.perf_counter(), 'image', mode, 'global', selected_template_id='selected',
                compare_active_templates=True)
        pool_loader.assert_called_once_with({"text", "image"}, None, force_refresh=True)
        return result, verify, strict, parallel

    def test_standard_compares_active_pool_and_draft_without_inactive_or_other_drafts(self):
        result, verify, strict, parallel = self.detect()
        self.assertEqual([candidate['template_id'] for candidate in result['candidates']], ['active-a', 'active-b', 'selected'])
        self.assertEqual(result['best_candidate']['template_id'], 'active-a')
        self.assertEqual(verify.call_count, 3)
        strict.assert_not_called()
        parallel.assert_called_once()
        self.assertTrue(result['debug']['layout_retrieval_skipped'])

    def test_strict_evaluates_same_pool_and_can_select_draft(self):
        result, verify, strict, parallel = self.detect('strict', .98)
        self.assertEqual(result['best_candidate']['template_id'], 'selected')
        self.assertEqual(strict.call_count, 2)
        self.assertEqual(verify.call_count, 4)
        parallel.assert_not_called()

    def test_mixed_pool_only_sends_eligible_templates_to_models(self):
        for mode in ('standard', 'strict'):
            with self.subTest(mode=mode):
                result, verify, strict, parallel = self.detect(mode, mismatched_active=True)
                self.assertEqual(result['best_candidate']['template_id'], 'active-b')
                evaluated = verify
                self.assertEqual({call.args[0] for call in evaluated.call_args_list}, {'active-b', 'selected'})
                if mode == 'standard':
                    batched = parallel.call_args.args[0]
                    self.assertEqual({template['template_id'] for template, _ in batched}, {'active-b', 'selected'})
                rejected = next(candidate for candidate in result['candidates'] if candidate['template_id'] == 'active-a')
                self.assertEqual(rejected['decision_reason'], 'page_count_mismatch')
                self.assertFalse(rejected['final_passed'])

    def test_aspect_filter_excludes_a4_from_card_models_in_both_modes(self):
        for mode in ('standard', 'strict'):
            with self.subTest(mode=mode):
                result, verify, strict, parallel = self.detect(mode, query_aspect=1.586,
                    template_aspects={'active-a': {1: 297 / 210}, 'active-b': {1: 1.586}, 'selected': {1: 1.6}})
                self.assertEqual(result['best_candidate']['template_id'], 'active-b')
                evaluated = verify
                self.assertEqual({call.args[0] for call in evaluated.call_args_list}, {'active-b', 'selected'})
                if mode == 'standard':
                    self.assertEqual({item['template_id'] for item, _ in parallel.call_args.args[0]}, {'active-b', 'selected'})
                rejected = next(item for item in result['candidates'] if item['template_id'] == 'active-a')
                self.assertEqual(rejected['decision_reason'], 'aspect_ratio_mismatch')
                self.assertEqual(rejected['evaluation_status'], 'skipped')

    def test_all_aspects_mismatched_skip_all_models(self):
        aspects = {name: {1: 297 / 210} for name in ('active-a', 'active-b', 'selected')}
        for mode in ('standard', 'strict'):
            result, verify, strict, parallel = self.detect(mode, query_aspect=1.586, template_aspects=aspects)
            self.assertFalse(result['matched'])
            verify.assert_not_called()
            strict.assert_not_called()
            parallel.assert_not_called()

    def test_selected_active_template_is_not_duplicated_and_uses_current_fields(self):
        result, *_ = self.detect(draft_score=.98, selected_active=True)
        self.assertEqual([candidate['template_id'] for candidate in result['candidates']].count('selected'), 1)
        self.assertEqual(result['best_candidate']['template_id'], 'selected')
        self.assertEqual(result['best_candidate']['final_score'], 1)

    def test_prepublish_reports_draft_rank_and_requires_draft_to_beat_active_templates(self):
        admin = services.AdminTemplateService()
        for score, expected_passed, expected_rank in [(.8, False, 3), (.98, True, 1)]:
            with self.subTest(score=score):
                detection, *_ = self.detect(draft_score=score)
                with patch.object(admin, 'get_template', return_value={'id':'selected'}), \
                     patch.object(admin, '_enrich_prepublish_candidate_verification_details', side_effect=lambda candidates, result:candidates), \
                     patch.object(detection_service, 'detect_template_dev', return_value=detection) as detect, \
                     patch.object(services.shutil, 'rmtree'):
                    result = admin.run_prepublish_detection_test('selected', b'image')
                self.assertTrue(detect.call_args.kwargs['compare_active_templates'])
                self.assertEqual(result['passed'], expected_passed)
                self.assertEqual(result['warning'], not expected_passed)
                self.assertEqual(result['draft_template_rank'], expected_rank)
                self.assertEqual(result['separation_result']['closest_published_template'], 'active-a')
                self.assertEqual([candidate['rank'] for candidate in result['candidates']], [1,2,3])
                self.assertEqual(next(candidate for candidate in result['candidates'] if candidate['is_current_draft'])['template_id'], 'selected')

    def test_inference_error_does_not_delete_query_storage_root(self):
        admin = services.AdminTemplateService()
        with patch.object(admin, 'get_template', return_value={'id':'selected'}), \
             patch.object(detection_service, 'detect_template_dev', side_effect=RuntimeError('inference failed')), \
             patch.object(services.shutil, 'rmtree') as cleanup:
            with self.assertRaises(RuntimeError):
                admin.run_prepublish_detection_test('selected', b'image')
        cleanup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
