from contextlib import contextmanager
from uuid import uuid4
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

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
