import importlib.util
import sqlite3
import unittest
from unittest.mock import patch

from app.model_runtime.siglip_v23 import SINGLE


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "FastAPI is required for category service tests")
class SiglipCategoryMigrationTests(unittest.TestCase):
    def test_replaces_retired_categories_and_prompts_without_remapping_anchors(self):
        from app.business import image_verification_category_service as service

        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with patch.object(service, "_IMAGE_VERIFICATION_CATEGORIES_READY", False):
                service.ensure_image_verification_categories_table(connection)
            connection.execute("CREATE TABLE anchor (category_id TEXT REFERENCES image_verification_categories(id) ON DELETE SET NULL)")
            for name in ("official_stamp", "government_emblem", "thailand_symbol"):
                connection.execute("INSERT INTO image_verification_categories (id,value,label,prompt) VALUES (?,?,?,?)", (name, name, name, "Old prompt"))
                connection.execute("INSERT INTO anchor VALUES (?)", (name,))
            connection.execute("UPDATE image_verification_categories SET prompt = 'Old prompt' WHERE value = 'company_logo'")
            with patch.object(service, "_IMAGE_VERIFICATION_CATEGORIES_READY", False):
                service.ensure_image_verification_categories_table(connection)
            found = dict(connection.execute("SELECT value,prompt FROM image_verification_categories").fetchall())
            self.assertEqual(found, SINGLE)
            self.assertEqual(connection.execute("SELECT category_id FROM anchor").fetchall(), [(None,), (None,), (None,)])
            with patch.object(service, "_IMAGE_VERIFICATION_CATEGORIES_READY", False):
                service.ensure_image_verification_categories_table(connection)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM image_verification_categories").fetchone()[0], 8)
        finally:
            connection.close()

    def test_cannot_add_negative_or_override_single_prompt(self):
        from fastapi import HTTPException
        from app.business import image_verification_category_service as service

        for name in ("printed_text", "official_stamp", "new_category"):
            with self.assertRaises(HTTPException) as caught:
                service.ImageVerificationCategoryService().create({"value": name, "label": name, "prompt": "Anything"})
            self.assertEqual(caught.exception.status_code, 422)
