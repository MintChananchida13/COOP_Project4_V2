import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api.routes import detect_template_dev_route


class FakeRequest:
    async def body(self) -> bytes:
        return b"image-bytes"

    @property
    def headers(self) -> dict:
        return {"content-type": "image/png"}


class RoutesErrorMappingTest(unittest.IsolatedAsyncioTestCase):
    async def test_detect_template_dev_maps_postgres_operational_error_to_503(self) -> None:
        import psycopg2

        with patch(
            "app.routes.detect_template_dev",
            side_effect=psycopg2.OperationalError("server closed the connection unexpectedly"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await detect_template_dev_route(FakeRequest())

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail["status"], "database_unavailable")


if __name__ == "__main__":
    unittest.main()
