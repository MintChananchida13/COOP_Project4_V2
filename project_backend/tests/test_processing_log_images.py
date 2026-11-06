import base64
import io
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from app.api import routes
from app.business import services
from app.core import db


class ProcessingLogImagesTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / '.tmp_pycache'
        self.root = base / f'log_images_{uuid4().hex}'
        self.root.mkdir(parents=True)
        def cleanup():
            # Only this test's generated directory may be recursively removed.
            assert self.root.resolve().parent == base.resolve()
            assert self.root.name.startswith('log_images_')
            shutil.rmtree(self.root)
        self.addCleanup(cleanup)
        self.conn = sqlite3.connect(':memory:', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys = ON')
        self.addCleanup(self.conn.close)
        for table in ('processing_logs', 'processing_log_images'):
            schema = next(sql for sql in db._POSTGRES_SCHEMA if f'CREATE TABLE IF NOT EXISTS {table} (' in sql)
            self.conn.executescript(schema.replace('::jsonb', ''))
        # SQLite is an isolated fixture, never a Production storage change.
        p = patch.object(services, '_connect', return_value=self.conn)
        p.start(); self.addCleanup(p.stop)
        for name, value in [('_storage_root', self.root / 'storage'),
                            ('_detection_query_storage_root', self.root / 'queries')]:
            p = patch.object(services, name, return_value=value)
            p.start(); self.addCleanup(p.stop)
        self.service = services.ProcessingLogService()
        p = patch.object(routes, 'processing_logs', self.service)
        p.start(); self.addCleanup(p.stop)
        app = FastAPI()
        app.include_router(routes.router)
        self.client = TestClient(app)
        image = io.BytesIO()
        Image.new('RGB', (12, 8), 'white').save(image, format='PNG')
        self.png = image.getvalue()
        self.payload = {'processing_run_id': 'run_test', 'page_count': 1,
            'page_files': [{'pageNumber': 1, 'dataUrl': 'data:image/png;base64,' + base64.b64encode(self.png).decode()}],
            'ocr_original': [{'fieldId': 'f1', 'value': 'original'}],
            'ground_truth': [{'fieldId': 'f1', 'value': 'edited'}], 'roi_snapshot': [{'roiId': 'f1'}]}

    def record(self, log_id):
        return dict(self.conn.execute('SELECT * FROM processing_logs WHERE id = ?', (log_id,)).fetchone())

    def test_inline_image_is_in_database_and_preview_is_identical_without_local_files(self):
        saved = self.service.upsert(self.payload)
        image = self.conn.execute('SELECT * FROM processing_log_images WHERE log_id = ?', (saved['id'],)).fetchone()
        self.assertEqual(base64.b64decode(image['image_base64']), self.png)
        self.assertEqual(image['mime_type'], 'image/png')
        self.assertFalse(services._safe_processing_log_dir(saved['id']).exists())
        response = self.client.get(saved['source_pages'][0]['previewUrl'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.png)
        self.assertEqual(response.headers['content-type'], 'image/png')
        self.assertEqual(int(response.headers['content-length']), len(self.png))
        record = self.record(saved['id'])
        for key in ('ocr_original', 'ground_truth', 'roi_snapshot'):
            self.assertEqual(json.loads(record[key + '_json']), self.payload[key])

    def test_query_images_preserve_source_processing_and_page_mapping_after_files_disappear(self):
        query = self.root / 'queries' / 'query_test'
        query.mkdir(parents=True)
        (query / 'page_1.png').write_bytes(self.png)
        (query / 'page_2.png').write_bytes(b'page-two')
        (query / 'processing_page_1.png').write_bytes(b'aligned-pixels')
        saved = self.service.upsert({**self.payload, 'page_count': 2, 'page_files': [], 'template_detection': {
            'queryId': 'query_test', 'processingPages': [{'pageNumber': 1, 'processingImageReference': 'processing_page_1.png'}]}})
        # Delete just the generated input fixtures to model an ephemeral filesystem.
        for path in query.iterdir():
            path.unlink()
        first, second = saved['source_pages']
        self.assertEqual(first['roiCoordinateSpace'], 'processing')
        self.assertEqual(self.client.get(first['sourcePreviewUrl']).content, self.png)
        self.assertEqual(self.client.get(first['processingPreviewUrl']).content, b'aligned-pixels')
        self.assertEqual(self.client.get(second['sourcePreviewUrl']).content, b'page-two')
        self.assertEqual(self.client.get(f"/admin/processing-logs/{saved['id']}/pages/2?kind=processing").status_code, 404)

    def test_list_and_detail_do_not_select_or_return_base64(self):
        saved = self.service.upsert(self.payload)
        queries = []
        self.conn.set_trace_callback(queries.append)
        listing = self.service.list()
        detail = self.service.get(saved['id'])
        self.conn.set_trace_callback(None)
        self.assertFalse(any('processing_log_images' in sql for sql in queries))
        self.assertNotIn('image_base64', json.dumps([listing, detail]))
        self.assertNotIn(base64.b64encode(self.png).decode(), json.dumps([listing, detail]))

    def test_resave_keeps_original_ocr_and_database_image_with_no_new_page_files(self):
        saved = self.service.upsert(self.payload)
        again = self.service.upsert({**self.payload, 'page_files': [], 'source_pages': [],
            'ocr_original': [{'fieldId': 'f1', 'value': 'changed'}],
            'ground_truth': [{'fieldId': 'f1', 'value': 'new edit'}], 'metadata': {'snapshotReason': 'back_to_roi'}})
        self.assertEqual(saved['id'], again['id'])
        self.assertEqual(saved['source_pages'], again['source_pages'])
        self.assertEqual(json.loads(self.record(saved['id'])['ocr_original_json']), self.payload['ocr_original'])
        self.assertEqual(self.client.get(again['source_pages'][0]['previewUrl']).content, self.png)
        count = self.conn.execute('SELECT count(*) FROM processing_log_images').fetchone()[0]
        self.assertEqual(count, 1)

    def test_failed_image_save_rolls_back_snapshot_and_retains_previous_image(self):
        saved = self.service.upsert(self.payload)
        before = self.record(saved['id'])
        with patch.object(self.service, '_persist_detection_query_pages', side_effect=RuntimeError('DB image write failed')):
            with self.assertRaises(RuntimeError):
                self.service.upsert({**self.payload, 'document_name': 'changed', 'metadata': {'snapshotReason': 'ocr_completed'}})
        self.assertEqual(self.record(saved['id']), before)
        self.assertEqual(self.client.get(saved['source_pages'][0]['previewUrl']).content, self.png)

    def test_failed_metadata_update_rolls_back_image_and_snapshot_together(self):
        saved = self.service.upsert(self.payload)
        before = self.record(saved['id'])
        query = self.root / 'queries' / 'query_changed'
        query.mkdir(parents=True)
        (query / 'page_1.png').write_bytes(b'replacement-pixels')
        with patch.object(self.service, '_log_image_metadata', side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):
                self.service.upsert({**self.payload, 'template_detection': {'queryId': 'query_changed'}})
        self.assertEqual(self.record(saved['id']), before)
        self.assertEqual(self.client.get(saved['source_pages'][0]['previewUrl']).content, self.png)

    def test_delete_cascades_images_but_preserves_other_logs(self):
        first = self.service.upsert(self.payload)
        second = self.service.upsert({**self.payload, 'processing_run_id': 'run_other'})
        self.assertTrue(self.service.delete(first['id'])['deleted'])
        rows = self.conn.execute('SELECT log_id FROM processing_log_images').fetchall()
        self.assertEqual([row['log_id'] for row in rows], [second['id']])
        self.assertEqual(self.client.get(second['source_pages'][0]['previewUrl']).content, self.png)

    def legacy(self, *, missing=False):
        log_id = 'proc_log_legacy'
        path = services._safe_processing_log_dir(log_id) / 'source_pages' / 'page_1.png'
        if not missing:
            path.parent.mkdir(parents=True)
            path.write_bytes(self.png)
        page = {'pageNumber': 1, 'sourceReference': services._relative_storage_reference(path),
                'previewUrl': f'/admin/processing-logs/{log_id}/pages/1?kind=source', 'imageReferenceStatus': 'persisted'}
        self.conn.execute('INSERT INTO processing_logs (id,processing_run_id,source_pages_json) VALUES (?,?,?)',
                          (log_id, 'run_legacy', json.dumps([page])))
        self.conn.commit()
        return page, path

    def test_legacy_local_image_moves_to_database_on_preview(self):
        page, path = self.legacy()
        self.assertEqual(self.client.get(page['previewUrl']).content, self.png)
        self.assertTrue(path.exists(), 'Migration must preserve the old original file')
        path.unlink()
        self.assertEqual(self.client.get(page['previewUrl']).content, self.png)

    def test_missing_legacy_image_stays_404_until_page_files_are_resubmitted(self):
        page, _ = self.legacy(missing=True)
        self.assertEqual(self.client.get(page['previewUrl']).status_code, 404)
        self.service.upsert({**self.payload, 'processing_run_id': 'run_legacy', 'source_pages': [page]})
        self.assertEqual(self.client.get(page['previewUrl']).content, self.png)

    def test_legacy_path_cannot_reference_another_log(self):
        page, _ = self.legacy()
        page['sourceReference'] = 'processing_logs/other/source_pages/page_1.png'
        self.conn.execute('UPDATE processing_logs SET source_pages_json = ? WHERE id = ?',
                          (json.dumps([page]), 'proc_log_legacy'))
        self.conn.commit()
        self.assertEqual(self.client.get(page['previewUrl']).status_code, 400)

    def test_bulk_legacy_migration_is_dry_by_default_and_keeps_local_original(self):
        page, path = self.legacy()
        result = self.service.migrate_log_images('proc_log_legacy')
        self.assertEqual(result['available'], 1)
        self.assertEqual(result['migrated'], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM processing_log_images').fetchone()[0], 0)
        result = self.service.migrate_log_images('proc_log_legacy', dry_run=False)
        self.assertEqual(result['migrated'], 1)
        self.assertTrue(path.exists())
        self.assertEqual(self.service.migrate_log_images('proc_log_legacy', dry_run=False)['migrated'], 0)
        path.unlink()
        self.assertEqual(self.client.get(page['previewUrl']).content, self.png)

    def test_bulk_migration_reports_missing_images_without_inventing_data(self):
        self.legacy(missing=True)
        result = self.service.migrate_log_images('proc_log_legacy', dry_run=False)
        self.assertEqual(result['missing'], [{'page': 1, 'kind': 'source'}])
        self.assertEqual(result['migrated'], 0)


if __name__ == '__main__':
    unittest.main()
