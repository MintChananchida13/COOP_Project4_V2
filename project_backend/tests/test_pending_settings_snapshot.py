import sqlite3
import unittest
from unittest.mock import patch

from app.business import services


class PendingSettingsSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)')
        self.service = services.GlobalSettingsService()

    def tearDown(self):
        self.conn.close()

    def test_empty_or_invalid_pending_template_uses_one_query_and_does_not_apply(self):
        self.conn.execute('INSERT INTO app_settings (key, value) VALUES (?, ?)',
                          (services.PENDING_TEMPLATE_CHANGES_SETTING_KEY, '[]'))
        queries = []
        self.conn.set_trace_callback(queries.append)
        with patch.object(services, '_connect', return_value=self.conn):
            self.assertFalse(self.service._apply_pending_settings_if_due({'enforcementEnabled': False}, force=True))
        self.assertEqual(len([q for q in queries if q.lstrip().upper().startswith('SELECT')]), 1)
        self.assertIsNotNone(self.conn.execute('SELECT value FROM app_settings WHERE key = ?',
                                              (services.PENDING_TEMPLATE_CHANGES_SETTING_KEY,)).fetchone())

    def test_pending_strategy_is_applied_and_removed(self):
        self.conn.execute('INSERT INTO app_settings (key, value) VALUES (?, ?)',
                          (services.PENDING_VERIFICATION_STRATEGY_SETTING_KEY, 'text_only'))
        with patch.object(services, '_connect', return_value=self.conn), \
             patch.object(services, 'normalize_verification_strategy', return_value='text_only'), \
             patch.object(services, '_set_cached_verification_strategy') as cached:
            self.assertTrue(self.service._apply_pending_settings_if_due({'enforcementEnabled': False}, force=True))
        self.assertEqual(self.conn.execute('SELECT value FROM app_settings WHERE key = ?',
                         (services.VERIFICATION_STRATEGY_SETTING_KEY,)).fetchone()['value'], 'text_only')
        self.assertIsNone(self.conn.execute('SELECT value FROM app_settings WHERE key = ?',
                          (services.PENDING_VERIFICATION_STRATEGY_SETTING_KEY,)).fetchone())
        cached.assert_called_once_with('text_only')

    def test_not_due_does_not_read_pending_settings(self):
        with patch.object(self.service, '_pending_settings_due', return_value=False), \
             patch.object(services, '_connect') as connect:
            self.assertFalse(self.service._apply_pending_settings_if_due({'enforcementEnabled': True}))
        connect.assert_not_called()

    def test_template_snapshot_parsing_matches_individual_reads(self):
        raw = '{"template_statuses":{"t1":"active"},"template_updates":{"t1":{"name":"x"}},"template_field_ops":{"t1":[{"op":"x"}]},"template_page_ops":{"t1":[{"op":"y"}]}}'
        for name in ('statuses', 'updates', 'field_ops', 'page_ops'):
            method = getattr(self.service, '_pending_template_' + name)
            with patch.object(self.service, '_load_setting_value', return_value=raw):
                self.assertEqual(method(), method(raw, loaded=True))


if __name__ == '__main__':
    unittest.main()
