import io
import unittest

from PIL import Image

from app.routes import _extract_multipart_file


def _png_bytes(color: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (20, 30), color).save(output, format="PNG")
    return output.getvalue()


class DetectionMultipartPagesTest(unittest.TestCase):
    def test_multipart_multiple_image_pages_are_combined_as_pdf(self) -> None:
        boundary = "----test-boundary"
        parts = []
        for index, data in enumerate([_png_bytes("white"), _png_bytes("black")], start=1):
            parts.append(
                b"--" + boundary.encode("utf-8") + b"\r\n"
                + f'Content-Disposition: form-data; name="file"; filename="page-{index}.png"\r\n'.encode("utf-8")
                + b"Content-Type: image/png\r\n\r\n"
                + data
                + b"\r\n"
            )
        body = b"".join(parts) + b"--" + boundary.encode("utf-8") + b"--\r\n"

        result = _extract_multipart_file(f"multipart/form-data; boundary={boundary}", body)

        self.assertTrue(result.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
