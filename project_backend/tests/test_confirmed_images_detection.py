import asyncio
import unittest
from unittest.mock import patch
from fastapi import BackgroundTasks, Response, HTTPException
from app.api import routes
from app.processing import detection_service as detection


def multipart(*files):
    return b"".join(b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="page.jpg"\r\nContent-Type: image/jpeg\r\n\r\n' + f + b'\r\n' for f in files) + b'--boundary--\r\n'

class Request:
    headers = {"content-type":"multipart/form-data; boundary=boundary"}
    def __init__(self, body, params): self._body=body; self.query_params=params
    async def body(self): return self._body

class ConfirmedPagesTests(unittest.TestCase):
    def test_admin_confirmed_pages_skip_backend_boundary_and_keep_page_order(self):
        request = Request(multipart(b'first', b'second'), {"confirmedImages": "1", "context": "admin"})
        tasks = BackgroundTasks()
        with patch.object(routes, "_ensure_processing_available_for_request"):
            response = asyncio.run(routes.detect_template_dev_route(request, tasks, Response()))
        job_id = response["data"]["job_id"]
        try:
            task = tasks.tasks[0]
            self.assertEqual(task.args[1], [b'first', b'second'])
            self.assertTrue(task.args[3])  # Admin diagnostics remain enabled.
            self.assertTrue(task.args[-1])  # Prepared pages bypass backend normalization.
            with patch.object(routes, "detect_template_dev", return_value={"matched": False}) as detect:
                routes._run_detect_dev_job(*task.args, **task.kwargs)
            self.assertTrue(detect.call_args.kwargs["skip_query_normalization"])
            self.assertFalse(detect.call_args.kwargs["user_context"])
        finally:
            routes.detect_dev_jobs.pop(job_id, None)

    def test_admin_worker_keeps_boundary_detection_enabled(self):
        key = "test-admin-boundary"
        routes.detect_dev_jobs[key] = {}
        try:
            with patch.object(routes, "detect_template_dev", return_value={"matched": False}) as call:
                routes._run_detect_dev_job(key, b"image", admin_context=True)
            self.assertFalse(call.call_args.kwargs["skip_query_normalization"])
            self.assertFalse(call.call_args.kwargs["user_context"])
        finally:
            routes.detect_dev_jobs.pop(key, None)

    def test_admin_pdf_one_page_checks_boundary_but_multiple_pages_do_not(self):
        from pathlib import Path
        with patch.object(Path, "mkdir"), patch.object(detection, "_storage_path", return_value=Path("test")), \
                patch.object(detection, "_image_dimensions", return_value=(800, 1100)), \
                patch.object(detection, "_copy_debug_image", return_value=None), \
                patch.object(detection, "_save_debug_crop", return_value=None), \
                patch.object(detection.normalization_service, "detect_pdf_subdocument_boundary", return_value={"passed": False, "reason": "none"}) as boundary:
            detection._normalize_query_pages("q", [Path("page.png")], source_type="pdf")
            self.assertEqual(boundary.call_count, 1)
            boundary.reset_mock()
            detection._normalize_query_pages("q", [Path("p1.png"), Path("p2.png")], source_type="pdf")
            boundary.assert_not_called()

    def test_admin_image_checks_boundary_but_confirmed_user_image_does_not(self):
        from pathlib import Path
        info = {"normalized_image_path": "normal.png", "normalization_status": "skipped", "crop_applied": False}
        with patch.object(Path, "mkdir"), patch.object(detection, "_storage_path", return_value=Path("test")), \
                patch.object(detection, "_image_dimensions", return_value=(800, 600)), \
                patch.object(detection.normalization_service, "normalize_document", return_value=info) as normalize:
            detection._normalize_query_pages("q", [Path("page.png")], source_type="image")
            normalize.assert_called_once()
            normalize.reset_mock()
            pages = detection._normalize_query_pages("q", [Path("page.png")], source_type="image", skip_normalization=True)
            normalize.assert_not_called()
            self.assertEqual(pages[0]["matching_path"], "page.png")

    def test_user_search_rejects_unconfirmed_upload(self):
        request = Request(multipart(b"page"), {})
        with patch.object(routes, "_ensure_processing_available_for_request"):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(routes.detect_template_dev_route(request, BackgroundTasks(), Response()))
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("confirmed image pages", error.exception.detail)

    def test_maintenance_blocks_before_reading_image_and_creating_job(self):
        request = Request(multipart(b"page"), {"confirmedImages": "1"})
        tasks = BackgroundTasks()
        with patch.object(routes.global_settings, "get_system_maintenance", return_value={"active": True, "message": "maintenance"}):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(routes.detect_template_dev_route(request, tasks, Response()))
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(error.exception.detail["error"]["code"], "SYSTEM_MAINTENANCE")
        self.assertEqual(len(tasks.tasks), 0)

    def test_waiting_for_maintenance_does_not_block_event_loop(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        def gate(*args):
            entered.set()
            release.wait(timeout=2)
        async def run():
            with patch.object(routes, "_ensure_processing_available_for_request", side_effect=gate):
                task = asyncio.create_task(routes.detect_template_dev_route(
                    Request(multipart(b"page"), {"confirmedImages": "1"}), BackgroundTasks(), Response()))
                try:
                    await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=1)
                    self.assertFalse(task.done())
                    release.set()
                    result = await asyncio.wait_for(task, timeout=1)
                    routes.detect_dev_jobs.pop(result["data"]["job_id"], None)
                finally:
                    release.set()
        asyncio.run(run())

    def test_traced_request_exposes_initial_server_timing_without_changing_response(self):
        request = Request(multipart(b'page'), {"confirmedImages": "1", "benchmark_trace_id": "a" * 32})
        response = Response()
        tasks = BackgroundTasks()
        with patch.object(routes, "_ensure_processing_available_for_request"):
            result = asyncio.run(routes.detect_template_dev_route(request, tasks, response))
        self.assertEqual(result["status"], "success")
        self.assertEqual(response.status_code, 202)
        for name in ("maintenance", "request_body", "job_setup", "initial_total"):
            self.assertIn(name + ";dur=", response.headers["Server-Timing"])
        self.assertEqual(tasks.tasks[0].kwargs["benchmark_trace_id"], "a" * 32)
        routes.detect_dev_jobs.pop(result["data"]["job_id"], None)

    def test_confirmed_pages_remain_separate_and_in_order(self):
        data=multipart(b'page-one',b'page-two')
        with patch.object(routes,"_images_to_pdf_bytes") as convert:
            pages=routes._extract_multipart_file(Request.headers["content-type"],data,confirmed_images=True)
        self.assertEqual(pages,[b'page-one',b'page-two'])
        convert.assert_not_called()

    def test_legacy_multiple_pages_keep_previous_pdf_transport(self):
        with patch.object(routes,"_images_to_pdf_bytes",return_value=b'%PDF-example') as convert:
            result=routes._extract_multipart_file(Request.headers["content-type"],multipart(b'first',b'second'))
        self.assertEqual(result,b'%PDF-example')
        convert.assert_called_once_with([b'first',b'second'])

    def test_pdf_not_accepted_as_confirmed_image(self):
        with self.assertRaises(HTTPException):
            routes._extract_multipart_file(Request.headers["content-type"],multipart(b'%PDF-file'),confirmed_images=True)

    def test_page_ending_in_dashes_is_not_truncated(self):
        pages = routes._extract_multipart_file(Request.headers["content-type"], multipart(b'image-ending--'), confirmed_images=True)
        self.assertEqual(pages, [b'image-ending--'])

    def test_user_request_passes_original_page_bytes_to_background_job(self):
        request=Request(multipart(b'first',b'second'),{"confirmedImages":"1"})
        tasks=BackgroundTasks()
        with patch.object(routes,"_ensure_processing_available_for_request"):
            response=asyncio.run(routes.detect_template_dev_route(request,tasks,Response()))
        task=tasks.tasks[0]
        self.assertEqual(task.args[1],[b'first',b'second'])
        self.assertTrue(task.args[-1])
        routes.detect_dev_jobs.pop(response["data"]["job_id"],None)

    def test_http_upload_keeps_confirmed_page_pixels_and_order(self):
        import io
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from PIL import Image
        app = FastAPI()
        app.include_router(routes.router)
        files = []
        expected = []
        for index, color in enumerate(('red', 'blue')):
            image = Image.new('RGB', (5 + index, 3), color)
            output = io.BytesIO()
            image.save(output, format='PNG')
            content = output.getvalue()
            expected.append(content)
            files.append(('file', (f'page-{index}.png', content, 'image/png')))
        with patch.object(routes, '_ensure_processing_available_for_request'), patch.object(routes, '_run_detect_dev_job') as job:
            with TestClient(app) as client:
                response = client.post('/api/templates/detect-dev?confirmedImages=1', files=files)
        self.assertEqual(response.status_code, 202)
        self.assertIs(response.json()['data']['confirmed_images'], True)
        self.assertEqual(job.call_args.args[1], expected)
        self.assertTrue(job.call_args.args[-1])
        routes.detect_dev_jobs.pop(response.json()['data']['job_id'], None)

    def test_confirmed_worker_sets_skip_normalization(self):
        key='test-confirmed-pages'
        routes.detect_dev_jobs[key]={}
        try:
            with patch.object(routes,"detect_template_dev",return_value={"matched":False}) as call:
                routes._run_detect_dev_job(key,[b'image'],confirmed_images=True)
            self.assertTrue(call.call_args.kwargs["skip_query_normalization"])
        finally: routes.detect_dev_jobs.pop(key,None)

    def test_preparation_saves_each_page_without_reencoding_pdf(self):
        with patch.object(detection,"_save_query_image",side_effect=lambda query,data,index,**kwargs: (index,data)), patch.object(detection,"_convert_pdf_to_page_images") as convert:
            pages=detection._prepare_query_pages('test',[b'one',b'two'])
        self.assertEqual(pages,[(1,b'one'),(2,b'two')])
        convert.assert_not_called()

    def test_confirmed_image_matches_display_orientation_and_white_transparency(self):
        import io
        from pathlib import Path
        from PIL import Image
        image = Image.new('RGB', (2, 3), 'red')
        exif = Image.Exif()
        exif[274] = 6
        source = io.BytesIO()
        image.save(source, format='JPEG', exif=exif)
        saved = []
        with patch.object(detection, '_storage_path', return_value=Path('ignored')), patch.object(Path, 'mkdir'), patch.object(Image.Image, 'save', autospec=True, side_effect=lambda im, *args, **kwargs: saved.append(im.copy())):
            detection._save_query_image('test', source.getvalue(), confirmed=True)
        self.assertEqual(saved[0].size, (3, 2))
        transparent = Image.new('RGBA', (2, 2), (0, 0, 0, 0))
        source = io.BytesIO()
        transparent.save(source, format='PNG')
        with patch.object(detection, '_storage_path', return_value=Path('ignored')), patch.object(Path, 'mkdir'), patch.object(Image.Image, 'save', autospec=True, side_effect=lambda im, *args, **kwargs: saved.append(im.copy())):
            detection._save_query_image('test', source.getvalue(), confirmed=True)
        self.assertEqual(saved[-1].getpixel((0, 0)), (255, 255, 255))
