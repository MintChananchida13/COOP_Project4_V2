"""Render A4 regression PDFs and local ID-card scans; never commit generated pixels.

Run from the repository root:
  python project_frontend/tests/pdf_content_preservation_fixtures.py --output benchmark/output/pdf_content_fixtures.json
  node project_frontend/tests/pdfContentPreservation.test.cjs --fixtures benchmark/output/pdf_content_fixtures.json
"""
import argparse
import base64
import json
from pathlib import Path
import zlib

import cv2
import numpy as np
import pymupdf as fitz


def render(doc, name, expected_crop):
    # Match the frontend PDF.js scale and detector's longest-side limit.
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False, colorspace=fitz.csRGB)
    image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    scale = min(1, 1600 / max(pix.width, pix.height))
    if scale < 1:
        image = cv2.resize(image, (round(pix.width * scale), round(pix.height * scale)), interpolation=cv2.INTER_AREA)
    # The upload converter encodes JPEG at quality 0.95 before the worker sees it.
    ok, encoded = cv2.imencode('.jpg', cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError('JPEG fixture encoding failed')
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    rgba = cv2.cvtColor(image, cv2.COLOR_BGR2RGBA)
    return dict(name=name, expectedCrop=expected_crop, width=rgba.shape[1], height=rgba.shape[0],
                pixels=base64.b64encode(zlib.compress(rgba.tobytes())).decode('ascii'))


def fixtures():
    result = []
    for kind in ('text', 'table', 'image', 'card'):
        with fitz.open() as doc:
            page = doc.new_page(width=595, height=842)
            page.draw_rect(fitz.Rect(120, 300, 480, 525), color=(.15, .15, .15), width=2)
            page.insert_text((210, 360), 'ID CARD 123456', fontsize=12)
            if kind == 'text':
                page.insert_text((40, 100), 'IMPORTANT DOCUMENT CONTENT', fontsize=12)
                page.insert_text((40, 680), 'Retain this footer too', fontsize=10)
            if kind == 'table':
                for y in (80, 100, 120):
                    page.draw_line((40, y), (110, y), width=.8)
                for x in (40, 75, 110):
                    page.draw_line((x, 80), (x, 120), width=.8)
            if kind == 'image':
                photo = np.full((90, 70, 3), (150, 120, 90), np.uint8)
                cv2.circle(photo, (35, 25), 15, (200, 190, 180), -1)
                ok, encoded = cv2.imencode('.png', photo)
                if not ok:
                    raise RuntimeError('Image fixture encoding failed')
                page.insert_image(fitz.Rect(40, 80, 110, 170), stream=encoded.tobytes())
            result.append(render(doc, 'generated-a4-' + kind, kind == 'card'))

    root = Path(__file__).resolve().parents[2]
    # Existing project scans, including faint borders. No OCR or model calls.
    prefix = '\u0e2a\u0e33\u0e40\u0e19\u0e32\u0e1a\u0e31\u0e15\u0e23\u0e1b\u0e23\u0e30\u0e0a\u0e32\u0e0a\u0e19'
    for path in sorted((root / 'benchmark/input').glob('*.pdf')):
        if path.name.startswith(prefix):
            with fitz.open(path) as doc:
                if len(doc) != 1:
                    raise AssertionError('Card fixture must have a single page')
                result.append(render(doc, path.name, True))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    cases = fixtures()
    data = json.dumps(cases, ensure_ascii=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding='utf-8')
        print(f'Generated {len(cases)} PDF fixtures')
    else:
        print(data)
