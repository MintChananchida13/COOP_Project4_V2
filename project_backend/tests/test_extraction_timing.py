import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import unittest
from unittest.mock import Mock

import numpy as np

from app.processing import extraction_timing


class ExtractionTimingTests(unittest.TestCase):
    def runtime(self, call_id):
        @extraction_timing.instrument_runtime
        def runtime(*, timing=None):
            now = time.perf_counter()
            timing.update(call_id=call_id, model_kind='text_recognition',
                model_call_start_monotonic=now, model_call_end_monotonic=now + .001,
                http_request_ms=1, gateway_timing={'inference_ms': .5})
            return 'unchanged'
        return runtime

    def test_measurements_preserve_results_and_capture_one_actual_call(self):
        runtime = self.runtime('one')
        @extraction_timing.measure_processing
        def process():
            return {'success': True, 'text': extraction_timing.call('stage', runtime)}
        result = process()
        self.assertEqual((result['success'], result['text']), (True, 'unchanged'))
        timing = result['extraction_timing']
        self.assertEqual(timing['model_call_count'], 1)
        self.assertEqual(timing['inferences']['text_recognition'], .5)
        self.assertEqual(timing['stages']['stage']['count'], 1)
        self.assertGreaterEqual(timing['processing_ms'], 0)
        self.assertIsNone(extraction_timing._capture.get())

    def test_concurrent_requests_do_not_mix_calls(self):
        @extraction_timing.measure_processing
        def process(call_id):
            self.runtime(call_id)()
            return {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(process, ['one', 'two']))
        self.assertEqual([r['extraction_timing']['model_calls'][0]['call_id'] for r in results], ['one', 'two'])

    def test_errors_propagate_and_release_capture(self):
        @extraction_timing.measure_processing
        def process():
            raise ValueError('failure')
        with self.assertRaisesRegex(ValueError, 'failure'):
            process()
        self.assertIsNone(extraction_timing._capture.get())

    def test_missing_gateway_inference_is_not_zero(self):
        @extraction_timing.instrument_runtime
        def runtime(*, timing=None):
            timing.update(call_id='missing', model_kind='text_detection')
        @extraction_timing.measure_processing
        def process():
            runtime()
            return {}
        timing = process()['extraction_timing']
        self.assertIsNone(timing['inferences']['text_detection'])
        self.assertIsNotNone(timing['model_timing_missing_reason'])

    def test_selected_roi_pipeline_preserves_mapping_and_batch_calls(self):
        # Compile the real function without importing startup/database side effects.
        from typing import Any, Dict, List
        from types import SimpleNamespace
        source = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8'))
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'process_document_payload')
        image = np.ones((20, 20, 3), dtype=np.uint8)
        def recognize(items, source, timing):
            self.assertEqual(source, 'fixed_text')
            timing.update(text_detection_batch_size=len(items), text_recognition_batch_size=3)
            return {key: {'text': 'OCR-' + key, 'confidence': .9} for key, _ in items}
        batch = Mock(side_effect=recognize)
        flex = Mock(return_value={'text': 'flex'})
        engine = Mock(return_value={'text': 'table/image'})
        save = Mock(return_value='crop.png')
        namespace = dict(Dict=Dict, List=List, Any=Any, DocumentPayload=object,
            extraction_timing=extraction_timing, np=np, uuid=__import__('uuid'),
            decode_base64_image=lambda _: (None, image),
            crop_opencv_polygon_region=lambda *args: None,
            crop_opencv_region=lambda *args: image, _save_cropped_roi=save,
            recognize_text_crops_with_detection=batch, process_flexible_text_roi=flex,
            process_roi_with_engine=engine, PaddleThaiOcrUnavailableError=RuntimeError,
            normalize_ocr_text=lambda text: text or '', without_image_payloads=lambda result: result)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'main.py', 'exec'), namespace)
        def roi(index, kind='text', mode='fix'):
            return SimpleNamespace(roiId=index, fieldName=str(index), type=kind, roiMode=mode,
                expectedContent='text', extractionMethod='paddle_thai_ocr', points=None,
                x=0, y=0, width=10, height=10)
        result = namespace['process_document_payload'](SimpleNamespace(image='image', context='user',
            rois=[roi(1), roi(2), roi(3, mode='flexible'), roi(4, 'table'), roi(5, 'image')]))
        self.assertEqual([r['roiId'] for r in result['extracted_data']], [1, 2, 3, 4, 5])
        self.assertEqual([r['text'] for r in result['extracted_data']],
                         ['OCR-0', 'OCR-1', 'flex', 'table/image', 'table/image'])
        self.assertEqual((batch.call_count, flex.call_count, engine.call_count, save.call_count), (1, 1, 2, 5))
        timing = result['extraction_timing']
        self.assertEqual(timing['fixed_text_counts']['text_recognition_batch_size'], 3)
        self.assertEqual(timing['stages']['crop_save']['count'], 5)
        self.assertIn('flexible_text_roi', timing['stages'])
        self.assertIn('table_roi', timing['stages'])


if __name__ == '__main__':
    unittest.main()
