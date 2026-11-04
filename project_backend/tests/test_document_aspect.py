import base64
import io
import sqlite3
import unittest
from unittest.mock import patch

from PIL import Image
from app.processing import document_aspect as aspect


def image_url(width, height):
    output = io.BytesIO()
    Image.new('RGB', (width, height)).save(output, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')


class DocumentAspectTests(unittest.TestCase):
    def test_header_reader_preserves_actual_image_orientation(self):
        self.assertAlmostEqual(aspect.image_aspect(image_url(158, 100)), 1.58)
        self.assertAlmostEqual(aspect.image_aspect(image_url(100, 158)), 100 / 158)

    def test_opposite_orientations_are_rejected_even_near_square(self):
        for reference, query in [(1.58, 1 / 1.58), (1 / 1.58, 1.58), (1.01, .99)]:
            result = aspect.check_template({'page_aspect_ratios': {1: reference}}, {1: query}, .08)
            self.assertFalse(result['passed'])
            self.assertFalse(result['comparisons'][0]['orientation_matches'])

    def test_missing_unreadable_and_remote_images_remain_unknown(self):
        for source in (None, '', 'missing-image.png', 'data:image/png;base64,broken', 'https://example.test/card.png'):
            self.assertIsNone(aspect.image_aspect(source))
        self.assertTrue(aspect.check_template({'page_aspect_ratios': {1: 1.586}}, {1: None}, .08)['passed'])
        self.assertTrue(aspect.check_template({}, {1: 1.586}, .08)['passed'])

    def test_card_a4_and_tolerance_boundary(self):
        template = {'page_aspect_ratios': {1: 1.586}}
        self.assertFalse(aspect.check_template(template, {1: 297 / 210}, .08)['passed'])
        self.assertTrue(aspect.check_template(template, {1: 1.586 * 1.08}, .08)['passed'])
        self.assertFalse(aspect.check_template(template, {1: 1.586 * 1.081}, .08)['passed'])

    def test_main_page_ignores_other_page_shapes_but_all_pages_checks_each(self):
        template = {'page_aspect_ratios': {1: 1.586, 2: 297 / 210}, 'detection_mode': 'main_page', 'main_page_number': 2}
        query = {1: 1, 2: 297 / 210, 3: 2}
        self.assertTrue(aspect.check_template(template, query, .08)['passed'])
        template['detection_mode'] = 'all_pages'
        self.assertFalse(aspect.check_template(template, query, .08)['passed'])

    def test_bulk_loading_uses_effective_image_and_keeps_unknown_pages(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            self.create_pages_table(conn)
            conn.executemany('INSERT INTO template_pages (id, template_version_id, page_number, normalized_image_url, sample_image_url) VALUES (?, ?, ?, ?, ?)', [
                ('p1', 'card', 1, image_url(158, 100), image_url(210, 297)),
                ('p2', 'a4', 1, None, image_url(210, 297)),
                ('p3', 'unknown', 1, 'https://example.test/normalized.png', image_url(158, 100)),
            ])
            self.assertEqual(aspect.backfill_page_geometry(conn), {'processed': 3, 'stored': 2, 'unknown': 1})
            with patch.object(aspect, 'image_geometry', side_effect=AssertionError('Search must only read stored numbers')):
                result = aspect.load_template_page_aspects(conn, ['card', 'a4', 'unknown'])
                self.assertEqual(aspect.backfill_page_geometry(conn)['processed'], 0)
        self.assertAlmostEqual(result['card'][1], 1.58)
        self.assertAlmostEqual(result['a4'][1], 210 / 297)
        self.assertNotIn('unknown', result)

    @staticmethod
    def create_pages_table(conn):
        conn.execute('''CREATE TABLE template_pages(id TEXT PRIMARY KEY, template_version_id TEXT, page_number INTEGER,
            normalized_image_url TEXT, sample_image_url TEXT, image_width INTEGER, image_height INTEGER,
            image_aspect_ratio REAL, image_geometry_checked BOOLEAN NOT NULL DEFAULT FALSE)''')

    def test_replacing_image_updates_persisted_size_and_orientation(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            self.create_pages_table(conn)
            conn.execute('INSERT INTO template_pages (id, template_version_id, page_number, sample_image_url) VALUES (?, ?, ?, ?)',
                         ('page', 'card', 1, image_url(158, 100)))
            aspect.persist_page_geometry(conn, 'page', 'card')
            row = dict(conn.execute('SELECT * FROM template_pages').fetchone())
            self.assertEqual((row['image_width'], row['image_height'], row['image_aspect_ratio']), (158, 100, 1.58))
            conn.execute('UPDATE template_pages SET normalized_image_url = ? WHERE id = ?', (image_url(100, 158), 'page'))
            aspect.persist_page_geometry(conn, 'page', 'card')
            self.assertAlmostEqual(aspect.load_template_page_aspects(conn, ['card'])['card'][1], 100 / 158)
            with patch.object(aspect, 'image_geometry') as read:
                self.assertFalse(aspect.persist_page_geometry(conn, 'page', 'other-template'))
                read.assert_not_called()

    def test_backfill_is_scoped_to_requested_template(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            self.create_pages_table(conn)
            conn.executemany('INSERT INTO template_pages (id, template_version_id, page_number, sample_image_url) VALUES (?, ?, ?, ?)',
                             [('a', 'first', 1, image_url(158, 100)), ('b', 'second', 1, image_url(210, 297))])
            self.assertEqual(aspect.backfill_page_geometry(conn, 'first')['processed'], 1)
            self.assertEqual(aspect.load_template_page_aspects(conn, ['second']), {})
            self.assertEqual(aspect.backfill_page_geometry(conn)['processed'], 1)

    def test_tolerance_can_be_configured_and_invalid_values_use_default(self):
        for raw, expected in [('0.05', .05), ('0', 0), ('nan', .08), ('invalid', .08), ('-1', .08)]:
            with patch.dict('os.environ', {'TEMPLATE_ASPECT_RATIO_TOLERANCE': raw}):
                self.assertEqual(aspect.tolerance(), expected)
