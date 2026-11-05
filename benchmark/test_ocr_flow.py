from contextlib import contextmanager
from uuid import uuid4
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch

import ocr_flow


@contextmanager
def report_path():
    directory = Path(__file__).parent / 'output'
    directory.mkdir(exist_ok=True)
    stem = 'test_ocr_' + uuid4().hex
    try:
        yield directory / (stem + '.xlsx')
    finally:
        for path in directory.glob(stem + '*.xlsx'):
            path.unlink()


class OcrFlowTests(unittest.TestCase):
    def test_matched_template_keeps_existing_path(self):
        with patch.object(ocr_flow, 'prepare_auto_roi') as auto, \
             patch.object(ocr_flow, 'run_ocr', return_value={'status': 'PASS', 'ocr_ui_wall_ms': 10}) as ocr:
            row = ocr_flow.run_after_detection(Mock(), {'status': 'PASS', 'matched': True}, 1000)
        auto.assert_not_called()
        ocr.assert_called_once()
        self.assertEqual(row['roi_source'], 'template')

    def test_unmatched_template_prepares_auto_roi_then_reads_ocr(self):
        page = Mock()
        steps = []
        def auto(*args):
            steps.append('auto')
            return {'auto_roi_region_count': 4, 'auto_roi_page_count': 2, 'auto_roi_setup_ms': 100}
        def ocr(*args):
            steps.append('ocr')
            return {'status': 'PASS', 'ocr_ui_wall_ms': 20}
        with patch.object(ocr_flow, 'prepare_auto_roi', side_effect=auto), \
             patch.object(ocr_flow, 'run_ocr', side_effect=ocr):
            row = ocr_flow.run_after_detection(page, {'status': 'PASS', 'matched': False}, 1000)
        self.assertEqual(steps, ['auto', 'ocr'])
        self.assertEqual(row['roi_source'], 'auto_roi')
        self.assertEqual(row['ocr_ui_wall_ms'], 20)
        self.assertEqual(row['auto_roi_setup_ms'], 100)

    def test_no_regions_or_failed_auto_roi_never_starts_ocr(self):
        with patch.object(ocr_flow, 'run_ocr') as ocr:
            with patch.object(ocr_flow, 'prepare_auto_roi', return_value={'auto_roi_region_count': 0}):
                empty = ocr_flow.run_after_detection(Mock(), {'status': 'PASS', 'matched': False}, 1000)
            with patch.object(ocr_flow, 'prepare_auto_roi', side_effect=RuntimeError('unavailable')):
                failed = ocr_flow.run_after_detection(Mock(), {'status': 'PASS', 'matched': False}, 1000)
            ocr.assert_not_called()
        self.assertEqual(empty['status'], 'SKIP_NO_ROI')
        self.assertEqual(failed['status'], 'AUTO_ROI_FAILED')

    def test_detection_failure_never_clicks_auto_roi(self):
        with patch.object(ocr_flow, 'prepare_auto_roi') as auto, patch.object(ocr_flow, 'run_ocr') as ocr:
            result = ocr_flow.run_after_detection(Mock(), {'status': 'FAIL', 'error': 'closed'}, 1000)
        self.assertEqual(result['status'], 'SETUP_FAILED')
        auto.assert_not_called()
        ocr.assert_not_called()

    def test_auto_roi_clicks_once_for_multiple_pages_and_waits_for_workspace(self):
        page = Mock()
        response = Mock(ok=True)
        response.json.return_value = {'success': True, 'pages': [
            {'page_index': 0, 'regions': [{}, {}]}, {'page_index': 1, 'regions': [{}]}]}
        pending = MagicMock()
        pending.__enter__.return_value.value = response
        page.expect_response.return_value = pending
        result = ocr_flow.prepare_auto_roi(page, 1000)
        page.get_by_role.assert_called_once_with('button', name=ocr_flow.AUTO_ROI_BUTTON, exact=True)
        page.get_by_role.return_value.click.assert_called_once()
        page.get_by_text.return_value.wait_for.assert_called_once_with(timeout=1000)
        self.assertEqual(result['auto_roi_page_count'], 2)
        self.assertEqual(result['auto_roi_region_count'], 3)

    def job(self, job_id='ocr-one'):
        return {'job_id': job_id, 'status': 'completed', 'page_number': 1, 'result_count': 2,
                'roi_count': 2, 'job_create_ms': 0,
                'timing': {'processing_ms': 12, 'model_call_count': 1,
                    'fixed_text_counts': {'text_recognition_batch_size': 3},
                    'inferences': {'text_recognition': None},
                    'stages': {'fixed_text_batch': {'elapsed_ms': 10, 'count': 1}},
                    'model_calls': [{'call_id': job_id, 'kind': 'text_recognition',
                                     'http_request_ms': 8, 'inference_ms': None}]}}

    def test_page_matching_uses_job_id_and_actual_page_number(self):
        record = ocr_flow.page_record(self.job(), [
            {'job_id': 'other', 'pageNumber': 1, 'frontend_total_ms': 999},
            {'job_id': 'ocr-one', 'pageNumber': 3, 'frontend_total_ms': 20, 'poll_wait_ms': 0}])
        self.assertEqual(record['page_number'], 3)
        self.assertEqual(record['frontend_total_ms'], 20)
        self.assertEqual(record['job_create_ms'], 0)
        self.assertEqual(record['poll_wait_ms'], 0)
        self.assertIsNone(record['text_recognition_inference_ms'])
        self.assertEqual(record['capture_missing_reasons'], [])
        self.assertEqual(record['text_recognition_batch_size'], 3)

    def test_missing_timings_are_explicit(self):
        record = ocr_flow.page_record({'job_id': 'old', 'status': 'completed'}, [])
        self.assertEqual(record['capture_missing_reasons'],
                         ['backend_extraction_timing_missing', 'frontend_ocr_timing_missing'])
        self.assertIsNone(record['processing_ms'])

    def test_separate_workbook_contains_only_ocr_data_and_handles_multiple_pages(self):
        from openpyxl import load_workbook
        pages = [ocr_flow.page_record(self.job(job_id), []) for job_id in ('one', 'two')]
        rows = [{'file': '=unsafe.jpg', 'run_number': 1, 'status': 'PASS', '_pages': pages}]
        with report_path() as target:
            path = ocr_flow.save_report(target, rows)
            workbook = load_workbook(path)
            self.addCleanup(workbook.close)
            self.assertEqual(workbook.sheetnames,
                ['OCR Runs', 'OCR Pages', 'OCR Stages', 'OCR Model Calls', 'Metric Notes'])
            self.assertEqual(workbook['OCR Pages'].max_row, 3)
            self.assertEqual(workbook['OCR Model Calls'].max_row, 3)
            self.assertEqual(workbook['OCR Stages'].max_row, 3)
            self.assertEqual(workbook['OCR Runs']['A2'].value, "'=unsafe.jpg")
            self.assertNotIn('template_search_ms', [cell.value for cell in workbook['OCR Runs'][1]])
            self.assertNotIn('text', [cell.value for cell in workbook['OCR Pages'][1]])
            workbook.close()

    def test_locked_report_uses_new_path(self):
        with report_path() as path:
            original_replace = ocr_flow.os.replace
            def replace(source, destination):
                if destination == path:
                    raise PermissionError('locked')
                return original_replace(source, destination)
            with patch.object(ocr_flow.os, 'replace', side_effect=replace):
                saved = ocr_flow.save_report(path, [])
            self.assertNotEqual(saved, path)
            self.assertTrue(saved.exists())

    def page(self, snapshot):
        page = Mock()
        page.evaluate.side_effect = [None, None, snapshot]
        page.get_by_role.return_value.is_visible.return_value = True
        return page

    def test_runner_reports_multi_page_ocr_without_search_metrics(self):
        page = self.page({'token': 'token', 'pending': 0, 'posts': 2, 'errors': [],
            'jobs': {'one': self.job('one'), 'two': self.job('two')},
            'client': [{'job_id': name, 'pageNumber': index} for index, name in enumerate(('one', 'two'), 1)]})
        with patch.object(ocr_flow, 'uuid4', return_value=Mock(hex='token')):
            row = ocr_flow.run_ocr(page, 1000)
        self.assertEqual(row['status'], 'PASS')
        self.assertEqual(row['page_job_count'], 2)
        self.assertEqual(row['result_count'], 4)
        self.assertNotIn('template_search_ms', row)

    def test_partial_page_failure_is_not_reported_as_pass(self):
        bad = {'job_id': 'bad', 'status': 'failed', 'error': 'model unavailable'}
        page = self.page({'token': 'token', 'pending': 0, 'posts': 2, 'errors': [],
                          'jobs': {'one': self.job('one'), 'bad': bad}, 'client': []})
        with patch.object(ocr_flow, 'uuid4', return_value=Mock(hex='token')):
            row = ocr_flow.run_ocr(page, 1000)
        self.assertEqual(row['status'], 'FAIL')
        self.assertEqual(len(row['_pages']), 2)

    def test_invalid_durations_are_not_coerced_to_zero(self):
        for value in (None, True, -1, float('nan'), float('inf'), '5'):
            self.assertIsNone(ocr_flow.duration(value))
        self.assertEqual(ocr_flow.duration(0), 0)


if __name__ == '__main__':
    unittest.main()
