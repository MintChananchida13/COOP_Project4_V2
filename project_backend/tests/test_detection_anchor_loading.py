import ast
import copy
import __future__
import json
import sqlite3
import sys
import threading
import time
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.processing.detection_requirements import verification_anchor_types


def functions(relative, names, namespace):
    path = ROOT / relative
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    definitions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace


def anchor(kind):
    return dict(id=kind, data_type=kind, expected_text='expected' if kind == 'text' else None,
                image_category='person' if kind == 'image' else None,
                required_for_verification=True, verification_weight=1,
                roi={'page_number': 1, 'x_ratio': .1, 'y_ratio': .2, 'width_ratio': .3, 'height_ratio': .4})


def row(kind):
    value = dict(template_id='template', template_name='Card', document_type='card', category='id',
                 template_status='active', version_name='v1', version_number=1, detection_mode='all_pages',
                 main_page_number=1, final_confidence_threshold=.7, layout_weight=.5,
                 text_anchor_weight=.3, image_anchor_weight=.2, page_count=1, field_count=1,
                 anchor_id=kind, template_page_id='page', page_number=1, field_name=kind,
                 display_label=kind, data_type=kind, expected_text='expected' if kind == 'text' else None,
                 image_category='person' if kind == 'image' else None,
                 match_type='contains', required_for_verification=True, verification_weight=1,
                 roi_x_ratio=.1, roi_y_ratio=.2, roi_width_ratio=.3, roi_height_ratio=.4,
                 roi_points_json=None)
    return value


class AnchorLoadingTests(unittest.TestCase):
    def test_fallback_sql_executes_for_text_image_and_combined_modes(self):
        database = sqlite3.connect(':memory:')
        self.addCleanup(database.close)
        database.row_factory = sqlite3.Row
        database.executescript('''
            CREATE TABLE template_groups(id TEXT, name TEXT, document_type TEXT, category TEXT);
            CREATE TABLE template_versions(id TEXT, template_group_id TEXT, status TEXT, version_name TEXT,
                version_number INTEGER, detection_mode TEXT, main_page_number INTEGER,
                final_confidence_threshold REAL, layout_weight REAL, text_anchor_weight REAL,
                image_anchor_weight REAL, updated_at TEXT);
            CREATE TABLE template_pages(id TEXT, template_version_id TEXT, page_number INTEGER);
            CREATE TABLE extraction_fields(id TEXT, template_page_id TEXT);
            CREATE TABLE image_verification_categories(id TEXT, value TEXT);
            CREATE TABLE verification_anchors(id TEXT, template_page_id TEXT, anchor_type TEXT,
                image_category_id TEXT, anchor_name TEXT, expected_text TEXT, match_type TEXT,
                required INTEGER, weight REAL, roi_x_ratio REAL, roi_y_ratio REAL,
                roi_width_ratio REAL, roi_height_ratio REAL, roi_points_json TEXT,
                sort_order INTEGER, created_at TEXT);
            INSERT INTO template_groups VALUES ('group', 'Card', 'card', 'id');
            INSERT INTO template_versions VALUES ('template', 'group', 'active', 'v1', 1,
                'all_pages', 1, .7, .5, .3, .2, '2026-01-01');
            INSERT INTO template_versions VALUES ('empty', 'group', 'active', 'v1', 1,
                'all_pages', 1, .7, .5, .3, .2, '2026-01-01');
            INSERT INTO template_pages VALUES ('page', 'template', 1);
            INSERT INTO template_pages VALUES ('empty-page', 'empty', 1);
            INSERT INTO image_verification_categories VALUES ('category', 'person');
            INSERT INTO verification_anchors VALUES ('text', 'page', 'text', NULL, 'Name',
                'expected', 'contains', 1, 1, .1, .2, .3, .4, NULL, 1, '2026-01-01');
            INSERT INTO verification_anchors VALUES ('image', 'page', 'image', 'category', 'Photo',
                NULL, NULL, 1, 1, .1, .2, .3, .4, NULL, 2, '2026-01-01');
        ''')
        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute_timed(self, query, params):
                return database.execute(query, params), {}
        namespace = {'time': time, 'json': json, '_connect': Connection,
                     '_ms': lambda value: value * 1000,
                     'published_template_cache': SimpleNamespace(get_verification_anchor_templates=lambda kinds: None)}
        functions('app/processing/detection_service.py', {'_load_anchor_strategy_templates'}, namespace)
        for kinds in [{'text'}, {'image'}, {'text', 'image'}]:
            templates, _ = namespace['_load_anchor_strategy_templates'](anchor_types=kinds)
            mapped = {item['template_id']: item for item in templates}
            self.assertEqual({item['data_type'] for item in mapped['template']['verification_anchors']}, kinds)
            self.assertEqual(mapped['empty']['verification_anchors'], [])
            if 'image' in kinds:
                image = next(item for item in mapped['template']['verification_anchors'] if item['data_type'] == 'image')
                self.assertEqual(image['image_category'], 'person')

        from unittest.mock import Mock
        stale_cache = Mock(return_value=[])
        namespace['published_template_cache'].get_verification_anchor_templates = stale_cache
        templates, debug = namespace['_load_anchor_strategy_templates'](anchor_types={'text','image'}, force_refresh=True)
        self.assertEqual({template['template_id'] for template in templates}, {'template', 'empty'})
        self.assertEqual(debug['source'], 'database')
        stale_cache.assert_not_called()

    def test_partial_refresh_does_not_make_the_full_template_inventory_ready(self):
        from unittest.mock import patch
        from app.processing import published_template_cache as cache
        entry = SimpleNamespace(template_id='active', metadata={'status':'active'}, field_count=0,
                                verification_fields=[])
        for ready, degraded, refreshed in [(False, False, {}), (False, False, {'active':entry}),
                                            (True, True, {'active':entry})]:
            with self.subTest(ready=ready, degraded=degraded, entries=bool(refreshed)), \
                    patch.object(cache, '_READY', ready), patch.object(cache, '_DEGRADED', degraded), \
                    patch.object(cache, '_ENTRIES', {}), patch.object(cache, '_load_entries', return_value=refreshed):
                cache.refresh('active' if refreshed else 'draft')
                self.assertEqual(cache._READY, ready)
                self.assertEqual(cache._DEGRADED, degraded)
                self.assertIsNone(cache.get_verification_anchor_templates({'text','image'}))

    def test_policy_for_both_supported_modes(self):
        for mode in ('standard', 'strict'):
            self.assertEqual(verification_anchor_types(mode), {'text', 'image'})

    def loader(self, cached=None):
        calls = []
        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute_timed(self, query, params):
                calls.append((query, params))
                rows = [row(kind) for kind in params]
                # Preserve templates without matching anchors, as LEFT JOIN did.
                empty = row(None)
                empty['template_id'] = 'no-anchors'
                rows.append(empty)
                return SimpleNamespace(fetchall=lambda: rows), {}
        namespace = {'time': time, 'json': json, '_connect': Connection,
                     '_ms': lambda value: value * 1000,
                     'published_template_cache': SimpleNamespace(get_verification_anchor_templates=lambda kinds: cached)}
        functions('app/processing/detection_service.py', {'_load_anchor_strategy_templates', '_load_anchor_strategy_templates'}, namespace)
        return namespace['_load_anchor_strategy_templates'], calls

    def test_database_fallback_filters_all_mode_types_and_keeps_metadata(self):
        for mode in ['standard', 'strict', 'text_only', 'text_image', 'image_only', 'layout_only']:
            with self.subTest(mode=mode):
                load, calls = self.loader()
                kinds = set(verification_anchor_types(mode))
                templates, debug = load(kinds)
                if not kinds:
                    self.assertEqual(templates, [])
                    self.assertEqual(calls, [])
                    continue
                self.assertEqual(len(calls), 1)
                query, params = calls[0]
                self.assertEqual(set(params), kinds)
                self.assertIn('va.anchor_type IN (', query)
                self.assertNotIn('layout_signature', query)
                self.assertEqual('JOIN image_verification_categories' in query, 'image' in kinds)
                actual = templates[0]['verification_anchors']
                self.assertEqual({item['data_type'] for item in actual}, kinds)
                self.assertEqual(templates[0]['final_confidence_threshold'], .7)
                self.assertEqual(templates[1]['verification_anchors'], [])
                for field in actual:
                    self.assertEqual(field['roi'], anchor(field['data_type'])['roi'])
                    self.assertTrue(field['required_for_verification'])
                    self.assertEqual(field['image_category'], anchor(field['data_type'])['image_category'])

    def test_ready_cache_filters_before_copy_and_distinguishes_none_from_empty(self):
        entries = {'template': SimpleNamespace(template_id='template', metadata={}, field_count=1,
                                                verification_fields=[anchor('text'), anchor('image')])}
        namespace = {'_LOCK': threading.RLock(), '_ENTRIES': entries, '_READY': True,
                     '_DEGRADED': False, 'copy': copy}
        functions('app/processing/published_template_cache.py', {'get_verification_fields', 'get_verification_anchor_templates'}, namespace)
        for kinds in [None, set(), {'text'}, {'image'}, {'text', 'image'}]:
            expected = {'text', 'image'} if kinds is None else kinds
            fields = namespace['get_verification_fields']('template', kinds)
            self.assertEqual({item['data_type'] for item in fields}, expected)
            result = namespace['get_verification_anchor_templates'](kinds)
            self.assertEqual({item['data_type'] for item in result[0]['verification_anchors']}, expected)
            if fields:
                fields[0]['roi']['x_ratio'] = 99
                self.assertEqual(entries['template'].verification_fields[0]['roi']['x_ratio'], .1)

    def test_request_cache_is_separate_for_each_anchor_type(self):
        calls = []
        def db(template_id, anchor_types=None):
            calls.append(anchor_types)
            return [anchor(kind) for kind in sorted(anchor_types)], {}
        namespace = {'published_template_cache': SimpleNamespace(get_verification_fields=lambda *args, **kwargs: None),
                     'verification_service': SimpleNamespace(load_verification_fields_with_db_timing=db)}
        functions('app/processing/detection_service.py', {'_fetch_verification_fields_cached'}, namespace)
        cache = SimpleNamespace(verification_fields={}, stats=defaultdict(int))
        get = namespace['_fetch_verification_fields_cached']
        for kinds in [{'text'}, {'image'}, {'text', 'image'}, {'text'}]:
            fields, _, _ = get('template', cache, anchor_types=kinds)
            self.assertEqual({field['data_type'] for field in fields}, kinds)
        self.assertEqual(len(calls), 3)
        self.assertEqual(get('template', cache, anchor_types=set())[0], [])
        self.assertEqual(len(calls), 3)

    def test_verification_service_filters_query_but_default_still_loads_both(self):
        queries = []
        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def execute_timed(self, query, params):
                queries.append((query, params))
                return SimpleNamespace(fetchone=lambda: {'id': 'template'}, fetchall=lambda: []), {}
        namespace = {'time': time, '_connect': Connection, '_template_field_row_to_api': lambda value: value}
        functions('app/business/services.py', {'load_verification_fields_with_db_timing'}, namespace)
        method = namespace['load_verification_fields_with_db_timing']
        for kinds in [None, set(), {'text'}, {'image'}, {'text', 'image'}]:
            queries.clear()
            method(None, 'template', anchor_types=kinds)
            query, params = queries[-1]
            self.assertEqual(params, ('template', *(sorted(kinds) if kinds is not None else [])))
            self.assertEqual('JOIN image_verification_categories' in query, kinds is None or 'image' in kinds)
            if kinds == set():
                self.assertIn('AND FALSE', query)




if __name__ == '__main__':
    unittest.main()
