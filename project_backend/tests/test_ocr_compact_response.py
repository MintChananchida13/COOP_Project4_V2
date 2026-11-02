import unittest
from app.model_runtime.layout_analysis_service import _text_detection_items_from_response
from app.model_runtime.paddle_thai_ocr_adapter import _extract_text_confidence


class OcrCompactResponseTests(unittest.TestCase):
    def test_ocr_fields_are_identical_without_input_image(self):
        polygon = [[1, 2], [8, 2], [8, 6], [1, 6]]
        detection = {'res': {'dt_polys': [polygon], 'dt_scores': [0.9], 'input_img': [[[1, 2, 3]]]}}
        recognition = {'res': {'rec_text': 'hello', 'rec_score': 0.9, 'input_img': [[[1, 2, 3]]]}}
        compact_detection = {'res': {'dt_polys': [polygon], 'dt_scores': [0.9]}}
        compact_recognition = {'res': {'rec_text': 'hello', 'rec_score': 0.9}}
        self.assertEqual(_text_detection_items_from_response(detection), _text_detection_items_from_response(compact_detection))
        self.assertEqual(_extract_text_confidence(recognition), _extract_text_confidence(compact_recognition))


if __name__ == '__main__':
    unittest.main()
