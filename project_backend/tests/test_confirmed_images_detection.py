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
