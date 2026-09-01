import json
import importlib.util
import unittest
from unittest.mock import patch

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None

if FASTAPI_AVAILABLE:
    from main import (
        DocumentPayload,
        create_ocr_job,
        get_ocr_job,
        run_ocr_job,
        update_ocr_job_status,
    )


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    jobs = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def execute(self, sql, params=()):
        normalized = " ".join(sql.lower().split())
        if normalized.startswith("insert into ocr_jobs"):
            job_id, requested_by, request_json = params
            self.jobs[job_id] = {
                "id": job_id,
                "requested_by": requested_by,
                "status": "queued",
                "request_json": request_json,
                "result_json": None,
                "error_message": None,
                "requested_at": "now",
                "started_at": None,
                "completed_at": None,
            }
            return FakeCursor()

        if normalized.startswith("select id, requested_by, status") or normalized.startswith("select id, status"):
            return FakeCursor(self.jobs.get(params[0]))

        if normalized.startswith("select request_json"):
            job = self.jobs.get(params[0])
            return FakeCursor({"request_json": job["request_json"]} if job else None)

        if "set status = 'processing'" in normalized:
            self.jobs[params[0]].update({"status": "processing", "started_at": "now", "error_message": None})
            return FakeCursor()

        if "set status = 'completed'" in normalized:
            result_json, job_id = params
            self.jobs[job_id].update({"status": "completed", "completed_at": "now", "result_json": result_json, "error_message": None})
            return FakeCursor()

        if "set status = 'failed'" in normalized:
            error_message, job_id = params
            self.jobs[job_id].update({"status": "failed", "completed_at": "now", "error_message": error_message})
            return FakeCursor()

        raise AssertionError(f"Unexpected SQL: {sql}")


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is not installed in this Python environment")
class OcrJobTest(unittest.TestCase):
    def setUp(self):
        FakeConnection.jobs = {}
        self.db_patch = patch("main.db_connect", return_value=FakeConnection())
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()

    def payload(self):
        return DocumentPayload(image="data:image/png;base64,AA==", rois=[], async_mode=True)

    def test_create_job(self):
        job_id = create_ocr_job(self.payload())

        self.assertTrue(job_id.startswith("ocr_"))
        self.assertEqual(FakeConnection.jobs[job_id]["status"], "queued")
        self.assertFalse(json.loads(FakeConnection.jobs[job_id]["request_json"])["async_mode"])

    def test_poll_status(self):
        job_id = create_ocr_job(self.payload())

        job = get_ocr_job(job_id)

        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "queued")

    def test_complete_job(self):
        job_id = create_ocr_job(self.payload())
        result = {"success": True, "extracted_data": [{"fieldName": "name", "text": "Alice"}]}

        update_ocr_job_status(job_id, "completed", result=result)
        job = get_ocr_job(job_id)

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"], result)

    def test_failed_job(self):
        job_id = create_ocr_job(self.payload())

        update_ocr_job_status(job_id, "failed", error_message="boom")
        job = get_ocr_job(job_id)

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_message"], "boom")

    def test_job_not_found(self):
        self.assertIsNone(get_ocr_job("ocr_missing"))

    def test_run_job_marks_completed(self):
        job_id = create_ocr_job(self.payload())
        expected = {"success": True, "extracted_data": []}

        with patch("main.process_document_payload", return_value=expected):
            run_ocr_job(job_id)

        self.assertEqual(get_ocr_job(job_id)["status"], "completed")
        self.assertEqual(get_ocr_job(job_id)["result"], expected)

    def test_run_job_marks_failed(self):
        job_id = create_ocr_job(self.payload())

        with patch("main.process_document_payload", side_effect=RuntimeError("failed hard")):
            run_ocr_job(job_id)

        self.assertEqual(get_ocr_job(job_id)["status"], "failed")
        self.assertEqual(get_ocr_job(job_id)["error_message"], "failed hard")


if __name__ == "__main__":
    unittest.main()
