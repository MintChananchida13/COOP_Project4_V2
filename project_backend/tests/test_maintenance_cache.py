import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.business import services


class MaintenanceCacheTests(unittest.TestCase):
    def setUp(self):
        services._invalidate_maintenance_cache()
        self.environment = patch.dict('os.environ', {'MAINTENANCE_SINGLE_PROCESS_CACHE': 'true'})
        self.environment.start()
        self.service = services.GlobalSettingsService()
        self.inactive = {'enforcementEnabled': False, 'active': False, 'scheduledStartAt': None,
                         'expectedEndAt': None, 'message': ''}

    def tearDown(self):
        self.environment.stop()
        services._invalidate_maintenance_cache()

    def test_repeated_requests_read_only_once(self):
        with patch.object(self.service, '_system_maintenance_from_storage', return_value=self.inactive) as read, \
             patch.object(self.service, '_apply_pending_settings_if_due') as apply:
            self.service.ensure_processing_available()
            self.service.ensure_processing_available()
        self.assertEqual(read.call_count, 1)
        self.assertEqual(apply.call_count, 1)

    def test_scheduled_boundary_rechecks_pending_and_blocks(self):
        state = {'due': False}
        with patch.object(self.service, '_system_maintenance_from_storage', side_effect=lambda **_: {**self.inactive, 'active': state['due']}) as read, \
             patch.object(self.service, '_apply_pending_settings_if_due') as apply, \
             patch.object(self.service, '_pending_settings_due', side_effect=lambda _: state['due']), \
             patch.object(self.service, '_maintenance_state', side_effect=lambda value: {**value, 'active': state['due']}):
            self.service.ensure_processing_available()
            state['due'] = True
            with self.assertRaises(HTTPException) as error:
                self.service.ensure_processing_available()
            self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(read.call_count, 2)
            self.assertEqual(apply.call_count, 2)

    def test_schedule_end_allows_requests_without_database_read(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        maintenance = {"enforcementEnabled": True, "scheduledStartAt": (now - timedelta(seconds=10)).isoformat(),
                       "expectedEndAt": (now + timedelta(seconds=10)).isoformat(), "active": True, "message": "down"}
        with patch.object(self.service, '_system_maintenance_from_storage', return_value=maintenance) as read, \
             patch.object(self.service, '_apply_pending_settings_if_due'):
            with self.assertRaises(HTTPException):
                self.service.ensure_processing_available()
            class Later(datetime):
                @classmethod
                def now(cls, tz=None):
                    return now + timedelta(seconds=20)
            with patch.object(services, 'datetime', Later):
                self.service.ensure_processing_available()
            self.assertEqual(read.call_count, 1)

    def test_setting_write_invalidates_cached_maintenance(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)')
        try:
            active = {**self.inactive, 'active': True, 'message': 'maintenance'}
            with patch.object(self.service, '_system_maintenance_from_storage', side_effect=[self.inactive, active]), \
                 patch.object(self.service, '_apply_pending_settings_if_due'), patch.object(services, '_connect', return_value=conn):
                self.service.ensure_processing_available()
                self.service._save_setting(services.SYSTEM_MAINTENANCE_SETTING_KEY, '{}')
                with self.assertRaises(HTTPException):
                    self.service.ensure_processing_available()
        finally:
            conn.close()

    def test_pending_save_invalidates_cached_state_after_commit(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)')
        try:
            with patch.object(self.service, '_system_maintenance_from_storage', return_value=self.inactive) as read, \
                 patch.object(self.service, '_apply_pending_settings_if_due'), patch.object(services, '_connect', return_value=conn):
                self.service.ensure_processing_available()
                self.service._save_setting(services.PENDING_VERIFICATION_STRATEGY_SETTING_KEY, 'text_only')
                self.service.ensure_processing_available()
                self.assertEqual(read.call_count, 2)
        finally:
            conn.close()

    def test_failed_apply_not_cached_and_retried(self):
        with patch.object(self.service, '_system_maintenance_from_storage', return_value=self.inactive) as read, \
             patch.object(self.service, '_apply_pending_settings_if_due', side_effect=[RuntimeError('test'), False]) as apply:
            self.service.ensure_processing_available()
            self.service.ensure_processing_available()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(apply.call_count, 2)

    def test_disabled_cache_uses_existing_database_path_each_time(self):
        with patch.dict('os.environ', {'MAINTENANCE_SINGLE_PROCESS_CACHE': 'false'}), \
             patch.object(self.service, 'get_system_maintenance', return_value=self.inactive) as read:
            self.service.ensure_processing_available()
            self.service.ensure_processing_available()
        self.assertEqual(read.call_count, 2)


if __name__ == '__main__':
    unittest.main()
