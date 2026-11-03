import base64
import io
import json
import sqlite3
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from PIL import Image
from app.business import services
from app.model_runtime import layout_analysis_service


class AnchorPublishTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE template_groups(id TEXT, name TEXT, document_type TEXT, category TEXT, description TEXT);
            CREATE TABLE template_versions(id TEXT, template_group_id TEXT, status TEXT, version_name TEXT,
                version_number INTEGER, created_from_version_id TEXT, detection_mode TEXT, main_page_number INTEGER,
                similarity_threshold REAL, final_confidence_threshold REAL, layout_weight REAL,
                text_anchor_weight REAL, image_anchor_weight REAL, created_at TEXT, updated_at TEXT, published_at TEXT);
            CREATE TABLE template_pages(id TEXT, template_version_id TEXT, page_number INTEGER,
                normalized_image_url TEXT, sample_image_url TEXT);
            CREATE TABLE image_verification_categories(id TEXT, value TEXT, enabled BOOLEAN);
            CREATE TABLE verification_anchors(id TEXT, template_page_id TEXT, anchor_type TEXT, anchor_name TEXT,
                expected_text TEXT, image_category_id TEXT, roi_x_ratio REAL, roi_y_ratio REAL,
                roi_width_ratio REAL, roi_height_ratio REAL, sort_order INTEGER);
            CREATE TABLE publish_jobs(id TEXT, template_version_id TEXT, status TEXT, step TEXT, requested_at TEXT,
                metadata_json TEXT, started_at TEXT, completed_at TEXT, error_message TEXT);
            INSERT INTO template_groups VALUES('group', 'Card', 'card', 'id', '');
            INSERT INTO template_versions VALUES('template', 'group', 'draft', 'v1', 1, NULL, 'all_pages', 1,
                .5, .75, 0, .5, .5, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL);
            INSERT INTO image_verification_categories VALUES('category', 'photo', TRUE);
            INSERT INTO verification_anchors VALUES('text', 'page', 'text', 'Name', 'Hello', NULL, .1, .1, .3, .2, 1);
            INSERT INTO verification_anchors VALUES('image', 'page', 'image', 'Photo', NULL, 'category', .5, .1, .3, .4, 2);
        ''')
        image_bytes = io.BytesIO()
        Image.new('RGB', (100,100), 'white').save(image_bytes, format='PNG')
        image = 'data:image/png;base64,' + base64.b64encode(image_bytes.getvalue()).decode()
        self.db.execute('INSERT INTO template_pages VALUES(?, ?, ?, ?, ?)', ('page', 'template', 1, image, image))
        self.db.commit()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(services, '_connect', return_value=self.db))
        self.stack.enter_context(patch.object(services.GlobalSettingsService, '_should_defer_admin_setting_updates', return_value=False))
        self.stack.enter_context(patch.object(services.GlobalSettingsService, '_pending_template_statuses', return_value={}))
        self.refresh = self.stack.enter_context(patch.object(services, '_refresh_published_template_cache'))
        self.layout = self.stack.enter_context(patch.object(layout_analysis_service, 'analyze_layout_signature', side_effect=AssertionError('Layout must never run')))
        self.admin = services.AdminTemplateService()
        self.stack.enter_context(patch.object(self.admin, 'get_template', return_value={'id':'template', 'status':'draft'}))
        self.publisher = services.TemplatePublishService()

    def readiness(self):
        return self.admin.run_prepublish_simulation('template')

    def test_simulation_and_confirm_publish_work_without_layout_columns_or_model(self):
        result = self.readiness()
        self.assertTrue(result['passed'])
        self.assertEqual(result['text_anchor_count'], 1)
        self.assertEqual(result['image_anchor_count'], 1)
        published = self.admin.confirm_publish_template('template')
        self.assertEqual(published['status'], 'published')
        self.assertEqual(published['job']['status'], 'completed')
        metadata = json.loads(published['job']['metadata_json'])
        self.assertEqual(metadata['engine'], 'text_image_anchors')
        self.assertNotIn('layout_signature_pages', metadata)
        self.layout.assert_not_called()
        self.refresh.assert_called_once_with('template', list_changed=True)

    def test_invalid_anchors_block_simulation_and_direct_confirmation_before_status_change(self):
        for query in ["UPDATE verification_anchors SET expected_text='' WHERE id='text'",
                      "UPDATE verification_anchors SET roi_width_ratio=2 WHERE id='text'",
                      "UPDATE image_verification_categories SET enabled=FALSE"]:
            with self.subTest(query=query):
                self.db.execute("UPDATE verification_anchors SET expected_text='Hello', roi_width_ratio=.3 WHERE id='text'")
                self.db.execute("UPDATE image_verification_categories SET enabled=TRUE")
                self.db.execute(query)
                self.assertFalse(self.readiness()['passed'])
                with self.assertRaises(HTTPException) as error:
                    self.admin.confirm_publish_template('template')
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(self.db.execute('SELECT status FROM template_versions').fetchone()['status'], 'draft')


    def test_missing_images_and_missing_anchors_cannot_pass(self):
        self.db.execute("UPDATE template_pages SET normalized_image_url=NULL, sample_image_url=NULL")
        self.assertFalse(self.readiness()['passed'])
        self.db.execute('DELETE FROM verification_anchors')
        self.assertIn('text_or_image_anchor_required', self.readiness()['issues'])

    def queue_job(self):
        self.db.execute("UPDATE template_versions SET status='validated'")
        self.db.commit()
        return self.publisher.create_publish_job('template')['job']['id']

    def test_run_revalidates_stored_anchors_and_marks_invalid_job_failed(self):
        job_id = self.queue_job()
        self.db.execute("UPDATE verification_anchors SET expected_text=NULL WHERE id='text'")
        self.db.commit()
        result = self.publisher.run_job_dev(job_id)
        self.assertEqual(result['job']['status'], 'failed')
        self.assertEqual(result['template']['status'], 'validated')
        self.refresh.assert_not_called()

    def test_complete_path_uses_same_readiness_gate(self):
        job_id = self.queue_job()
        result = self.publisher.complete_job_dev(job_id)
        self.assertEqual(result['job']['status'], 'completed')
        self.assertEqual(result['template']['status'], 'active')
        self.layout.assert_not_called()

    def test_complete_path_rejects_invalid_configuration(self):
        job_id = self.queue_job()
        self.db.execute('DELETE FROM verification_anchors')
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.publisher.complete_job_dev(job_id)
        self.assertNotEqual(self.db.execute('SELECT status FROM template_versions').fetchone()['status'], 'active')

    def test_maintenance_defers_activation(self):
        self.stack.enter_context(patch.object(services.GlobalSettingsService, '_should_defer_admin_setting_updates', return_value=True))
        pending = self.stack.enter_context(patch.object(services.GlobalSettingsService, 'save_pending_template_status'))
        remove = self.stack.enter_context(patch.object(services, '_remove_published_template_cache'))
        result = self.publisher.run_job_dev(self.queue_job())
        self.assertEqual(result['job']['status'], 'completed')
        self.assertEqual(result['template']['status'], 'validated')
        pending.assert_called_once_with('template', 'active')
        remove.assert_called_once_with('template')
        self.refresh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
