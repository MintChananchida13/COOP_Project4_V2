import sqlite3
import unittest
from unittest.mock import patch

from app.business import services
from app.api.schemas import TemplatePageCreate, TemplatePageUpdate
from app.processing import document_aspect
from test_document_aspect import image_url


class TemplatePageGeometrySaveTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE template_versions(id TEXT PRIMARY KEY);
            INSERT INTO template_versions VALUES ('template');
            CREATE TABLE template_pages(id TEXT PRIMARY KEY, template_version_id TEXT, page_number INTEGER,
                page_name TEXT, sample_image_url TEXT, normalized_image_url TEXT, layout_signature_json TEXT,
                image_width INTEGER, image_height INTEGER, image_aspect_ratio REAL,
                image_geometry_checked BOOLEAN DEFAULT FALSE, created_at TEXT, updated_at TEXT);
        ''')
        self.admin = services.AdminTemplateService()
        for target, kwargs in [('_connect', {'return_value': self.conn}),
                               ('_refresh_published_template_cache', {})]:
            patcher = patch.object(services, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        for method, value in [('get_template', {}), ('_defer_active_template_changes', False)]:
            patcher = patch.object(self.admin, method, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_create_and_replace_image_persist_geometry_but_name_edit_does_not_read_image(self):
        self.admin.create_template_page('template', TemplatePageCreate(page_number=1, sample_image_url=image_url(158, 100)))
        row = dict(self.conn.execute('SELECT * FROM template_pages').fetchone())
        self.assertEqual((row['image_width'], row['image_height'], row['image_aspect_ratio']), (158, 100, 1.58))
        with patch.object(document_aspect, 'image_geometry', side_effect=AssertionError('Name edits do not change image geometry')):
            self.admin.update_template_page('template', row['id'], TemplatePageUpdate(page_name='Renamed'))
        self.admin.update_template_page('template', row['id'], TemplatePageUpdate(normalized_image_url=image_url(210, 297)))
        updated = dict(self.conn.execute('SELECT * FROM template_pages').fetchone())
        self.assertEqual((updated['image_width'], updated['image_height']), (210, 297))
        self.assertAlmostEqual(updated['image_aspect_ratio'], 210 / 297)

    def test_deferred_image_update_refreshes_geometry_when_applied(self):
        self.admin.create_template_page('template', TemplatePageCreate(page_number=1, sample_image_url=image_url(158, 100)))
        page_id = self.conn.execute('SELECT id FROM template_pages').fetchone()['id']
        self.admin._apply_pending_template_page_ops(self.conn, {'template': [{
            'action': 'update_page', 'page_id': page_id,
            'patch': {'normalized_image_url': image_url(100, 158)},
        }]})
        stored = document_aspect.load_template_page_aspects(self.conn, ['template'])
        self.assertAlmostEqual(stored['template'][1], 100 / 158)
