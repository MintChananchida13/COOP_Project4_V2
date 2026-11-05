import unittest
from unittest.mock import patch

from app.business.services import VerificationService


class TextAnchorMatchingTests(unittest.TestCase):
    def setUp(self):
        self.service = VerificationService()

    def test_text_edit_operations_and_floor_boundary(self):
        cases = [
            ('abcdefgh', 'abcdefgh', 0, True),
            ('abcdefgh', 'abXdefgh', 1, True),  # substitution
            ('abcdefgh', 'abdefgh', 1, True),   # deletion
            ('abcdefgh', 'abcXdefgh', 1, True),  # insertion
            ('abcdefgh', 'abXYefgh', 2, True),
            ('abcdefgh', 'abXYZfgh', 3, False),
            ('abcdefgh', 'abefgh', 2, True),
            ('abcdefgh', 'afgh', 4, False),
            ('abcdefgh', 'abcXYdefgh', 2, True),
            ('abcdefgh', 'abcXYZdefgh', 3, False),
            ('abcdefghijkl', 'XbcdefghijYlZ', 3, True),  # substitution + insertion
            ('abcdefghijkl', 'XbdefghijklZ', 3, True),  # all three, exactly at allowance
            ('abcdefghijkl', 'XbdefghijYlZ', 4, False),  # all three operations
            ('abc', 'abX', 1, False),  # floor(3 * .25) == 0
            ('abcd', 'abcX', 1, True),
            ('abcdefg', 'abcdeXY', 2, False),  # floor(7 * .25) == 1
            ('12345678', '123456', 2, True),
            ('123456', '12345678', 2, False),  # allowance is based on expected
            ('ทดสอบระบบ', 'ทดสอบระบบ', 0, True),
            ('กขคงจฉชซ', 'กขคงจฉกข', 2, True),
            ('กขคงจฉชซ', 'กขคงจกขค', 3, False),
            ('กิ', 'ก', 1, False),  # do not discard Thai combining marks
        ]
        for expected, actual, distance, passed in cases:
            with self.subTest(expected=expected, actual=actual):
                left = self.service._normalize_text(expected)
                right = self.service._normalize_text(actual)
                self.assertEqual(self.service._levenshtein_distance(left, right), distance)
                result = self.service._score_match(expected, actual, .01)
                self.assertEqual(result['passed'], passed)
                for key in ('score', 'field_score', 'text_match_score', 'text_similarity_score'):
                    self.assertEqual(result[key], 1.0 if passed else 0.0)
                self.assertEqual(result['verification_threshold'], 1.0)
                self.assertEqual(result['failure_reason'], 'passed' if passed else 'below_threshold')

    def test_text_normalization_and_empty_values(self):
        result = self.service._score_match(' ＡB\u200b C\nD ', 'abcd', 0)
        self.assertTrue(result['passed'])
        self.assertEqual(result['score'], 1)
        for expected, actual in [('', ''), ('abcd', ''), ('', 'abcd'), (None, None), (' \n', '\u200b')]:
            with self.subTest(expected=expected, actual=actual):
                result = self.service._score_match(expected, actual, 1)
                self.assertFalse(result['passed'])
                self.assertEqual(result['score'], 0)

    def test_no_similarity_calculation_or_match_type_in_result(self):
        result = self.service._score_match('abcdefgh', 'abXYefgh', .01)
        self.assertTrue(result['passed'])
        self.assertNotIn('match_type', result)
        self.assertFalse(hasattr(self.service, '_similarity'))
        self.assertFalse(hasattr(self.service, '_normalize_for_similarity'))

    def test_old_api_payloads_are_accepted_but_legacy_settings_are_ignored(self):
        from app.api.schemas import TemplateFieldCreate, TemplateFieldUpdate
        for schema in (TemplateFieldCreate, TemplateFieldUpdate):
            self.assertNotIn('match_type', schema.model_json_schema()['properties'])
            self.assertNotIn('regex_pattern', schema.model_json_schema()['properties'])
        for kind in ('exact', 'contains', 'fuzzy', 'regex'):
            payload = TemplateFieldUpdate(expected_text='abcdefgh', match_type=kind, regex_pattern='.*')
            self.assertEqual(payload.model_dump(exclude_unset=True), {'expected_text': 'abcdefgh'})

    def test_legacy_anchor_types_all_use_edit_distance_without_extra_ocr(self):
        for kind in ('exact', 'contains', 'fuzzy', 'regex', None):
            for actual, passed in [('abXYefgh', True), ('xxabcdefghyy', False)]:
                field = {'id': 'anchor', 'field_name': 'name', 'display_label': 'name',
                         'data_type': 'text', 'page_number': 1, 'expected_text': 'abcdefgh',
                         'match_type': kind, 'regex_pattern': '.*',
                         'required_for_verification': True, 'verification_weight': 1,
                         'roi': {'x_ratio': 0, 'y_ratio': 0, 'width_ratio': 1, 'height_ratio': 1}}
                with self.subTest(kind=kind, actual=actual), \
                     patch('app.business.services.ocr_roi', side_effect=AssertionError('extra OCR')):
                    result = self.service.verify_template('template', {1: 'page.png'}, [field],
                        precomputed_text_ocr_cache={'anchor': {'text': actual, 'confidence': .01}},
                        include_crop_preview=False)
                checked = result['checked_fields'][0]
                self.assertEqual(checked['passed'], passed)
                self.assertEqual(checked['score'], float(passed))
                self.assertNotIn('match_type', checked)

    def test_update_preserves_archived_database_values(self):
        import sqlite3
        from app.api.schemas import TemplateFieldCreate
        from app.business.services import AdminTemplateService

        database = sqlite3.connect(':memory:')
        self.addCleanup(database.close)
        database.executescript('''
            CREATE TABLE template_pages (id TEXT, template_version_id TEXT);
            INSERT INTO template_pages VALUES ('page', 'template');
            CREATE TABLE verification_anchors (
                id TEXT, template_page_id TEXT, anchor_name TEXT, anchor_type TEXT,
                roi_x_ratio REAL, roi_y_ratio REAL, roi_width_ratio REAL, roi_height_ratio REAL,
                roi_points_json TEXT, required INTEGER, weight REAL, expected_text TEXT,
                match_type TEXT, regex_pattern TEXT, image_category_id TEXT,
                sort_order INTEGER, updated_at TEXT);
            INSERT INTO verification_anchors (id, template_page_id, expected_text, match_type, regex_pattern)
                VALUES ('anchor', 'page', 'old text', 'regex', '^old.*$');
        ''')
        payload = TemplateFieldCreate(template_page_id='page', page_number=1,
            field_name='name', display_label='name', data_type='text', expected_text='new text',
            use_for_verification=True,
            roi={'page_number': 1, 'x_ratio': 0, 'y_ratio': 0, 'width_ratio': 1, 'height_ratio': 1})
        with patch('app.business.services._resolve_image_category_id', return_value=None):
            AdminTemplateService()._update_verification_anchor_from_payload(
                database, 'template', 'anchor', payload)
        self.assertEqual(database.execute(
            'SELECT expected_text, match_type, regex_pattern FROM verification_anchors').fetchone(),
            ('new text', 'regex', '^old.*$'))


if __name__ == '__main__':
    unittest.main()
