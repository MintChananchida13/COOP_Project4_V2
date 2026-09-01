import unittest

from fastapi import HTTPException

from app.services import _resolve_image_category_id


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=()):
        normalized = " ".join(sql.lower().split())
        self.executed.append((normalized, params))
        if normalized.startswith("create table"):
            return FakeCursor()
        if normalized.startswith("insert into image_verification_categories"):
            return FakeCursor()
        if "select id from image_verification_categories" in normalized:
            value = params[0]
            if value in {"portrait", "ivc_portrait"}:
                return FakeCursor({"id": "ivc_portrait"})
            return FakeCursor(None)
        raise AssertionError(f"Unexpected SQL: {sql}")

    def commit(self):
        return None


class SchemaV2MappingTest(unittest.TestCase):
    def test_resolves_image_category_code_to_fk_id(self):
        conn = FakeConnection()

        self.assertEqual(_resolve_image_category_id(conn, "portrait"), "ivc_portrait")

    def test_accepts_existing_image_category_id(self):
        conn = FakeConnection()

        self.assertEqual(_resolve_image_category_id(conn, "ivc_portrait"), "ivc_portrait")

    def test_missing_image_category_returns_clear_validation_error(self):
        conn = FakeConnection()

        with self.assertRaises(HTTPException) as context:
            _resolve_image_category_id(conn, "missing")

        self.assertEqual(context.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
