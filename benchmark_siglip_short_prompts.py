"""SigLIP benchmark: 13 short prompts, one CSV per CPU/GPU.

Install: pip install requests python-dotenv
Examples:
 python benchmark_siglip_short_prompts.py --device cpu --dry-run
 python benchmark_siglip_short_prompts.py --device cpu --probe
 python benchmark_siglip_short_prompts.py --device cpu
 python benchmark_siglip_short_prompts.py --device gpu
"""
from __future__ import annotations
import argparse
import csv
import json
import math
import mimetypes
import os
import time
from pathlib import Path

import requests
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

PROMPTS = {
    'emblem': 'A company logo or official institutional emblem, featuring text, symbols, or decorative designs.',
    'thai_garuda': 'A Garuda figure with a human body, bird head, and wings.',
    'portrait': 'An ID-style portrait photograph showing a persons face and upper body, possibly with numbers or measurement lines in the background.',
    'signature': 'A handwritten signature.',
    'barcode': 'A barcode with parallel vertical black bars.',
    'qr_code': 'A square QR code with a black and white matrix pattern.',
    'text': 'Printed or handwritten text, letters, dates, and numbers.',
    'table': 'A table with rows and columns.',
    'blank': 'A blank document area.',
    'background': 'A document background or watermark.',
    'illustration': 'A drawing, diagram, or illustrative photograph.',
    'bar_chart': 'A statistical bar chart.',
}
POSITIVE = set(list(PROMPTS)[:6])
NEGATIVE = set(PROMPTS) - POSITIVE
# Existing image directories: company logos now count as emblem.
# document_stamp has been removed; the old 'ตราประทับ' folder is intentionally skipped.
FOLDERS = {
    'emblem': 'emblem',
    'ตราครุฑ': 'thai_garuda',
    'โลโก้': 'emblem',
    'รูปบุคคล': 'portrait',
    'signature': 'signature',
    'barcode': 'barcode',
    'qr_code': 'qr_code',
    'neg': 'neg',
}
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}
FIELDS = [
    'category', 'filename', 'repeat', 'backend_total_ms',
    'model_duration_ms', 'difference_ms', 'predicted_category', 'top3'
]


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def get_images(root: Path):
    images = []
    for folder, truth in FOLDERS.items():
        directory = root / folder
        if not directory.is_dir():
            print(f'WARNING: folder not found: {directory}')
            continue
        paths = sorted(p for p in directory.rglob('*') if p.is_file() and p.suffix.lower() in EXTENSIONS)
        print(f'{folder} -> {truth}: {len(paths)} image(s)')
        for path in paths:
            images.append((folder, path, truth))
    return images


def send_request(session, url, path, timeout):
    with path.open('rb') as stream:
        return session.post(
            url,
            data={'labels': json.dumps(list(PROMPTS.values()), ensure_ascii=False)},
            files={'image': (path.name, stream, mimetypes.guess_type(path.name)[0] or 'application/octet-stream')},
            timeout=timeout,
        )


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f'Invalid numeric score: {value!r}')
    return float(value)


def extract_logits(payload):
    if not isinstance(payload, dict):
        raise ValueError('Expected JSON object')
    candidates = [payload]
    for key in ('data', 'result', 'results', 'raw_output', 'predictions', 'raw_predictions'):
        item = payload.get(key)
        if isinstance(item, dict):
            candidates.append(item)
        elif isinstance(item, list):
            candidates.extend(x for x in item if isinstance(x, dict))
    for obj in candidates:
        for key in ('logits_per_image', 'logits', 'scores'):
            values = obj.get(key)
            if isinstance(values, list) and len(values) == 1 and isinstance(values[0], list):
                values = values[0]
            if isinstance(values, list) and len(values) == len(PROMPTS):
                return [numeric(v) for v in values]
    for obj in candidates:
        labels = obj.get('labels')
        if isinstance(labels, list) and len(labels) == len(PROMPTS):
            mapping = {
                x.get('image_category'): x.get('raw_logit')
                for x in labels if isinstance(x, dict)
            }
            if set(mapping) == set(PROMPTS):
                return [numeric(mapping[k]) for k in PROMPTS]
    raise ValueError(f'No complete logits vector of {len(PROMPTS)} values found')


def extract_duration(payload):
    meta = payload.get('meta') if isinstance(payload, dict) else None
    if not isinstance(meta, dict) or meta.get('duration_ms') is None:
        return None
    duration = numeric(meta['duration_ms'])
    if duration < 0:
        raise ValueError('Negative model duration')
    return duration


def request_one(session, url, path, timeout):
    started = time.perf_counter()
    response = send_request(session, url, path, timeout)
    elapsed = (time.perf_counter() - started) * 1000
    if not response.ok:
        raise RuntimeError(f'HTTP {response.status_code}: {response.text[:800]}')
    payload = response.json()
    logits = extract_logits(payload)
    names = list(PROMPTS)
    ranked = sorted(range(len(logits)), key=lambda i: logits[i], reverse=True)
    top3 = [{'category': names[i], 'logit': round(logits[i], 6)} for i in ranked[:3]]
    duration = extract_duration(payload)
    return {
        'backend_total_ms': round(elapsed, 3),
        'model_duration_ms': round(duration, 3) if duration is not None else '',
        'difference_ms': round(elapsed - duration, 3) if duration is not None else '',
        'predicted_category': names[ranked[0]],
        'top3': json.dumps(top3, ensure_ascii=False),
    }


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', choices=['cpu', 'gpu'], required=True)
    parser.add_argument('--folder', type=str, default=None)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent / 'siglip')
    parser.add_argument('--output', type=Path, default=Path('siglip_benchmark_results_short_prompts'))
    parser.add_argument('--url', default=env('SIGLIP_BENCHMARK_URL', 'https://websolution.cdgs.co.th/paddle/api/v1/image-classifications'))
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--warmup', type=int, default=1)
    parser.add_argument('--delay', type=float, default=5)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.repeats < 1 or args.warmup < 0 or args.delay < 0:
        parser.error('repeats must be >=1; warmup and delay must be >=0')

    images = get_images(args.root)
    if args.folder:
        images = [item for item in images if item[0] == args.folder]
    if not images:
        raise SystemExit('No images found. Check --root.')
    print(f'Categories: {len(PROMPTS)}; images: {len(images)}; measured calls: {len(images) * args.repeats}')
    if len(images) != 65:
        print('NOTE: original 70-image dataset minus 5 document_stamp images = 65 images (if each folder still has 5).')
    if args.dry_run:
        return

    api_key = env('MODEL_GATEWAY_API_KEY', '')
    if not api_key:
        raise SystemExit('Set MODEL_GATEWAY_API_KEY in .env or environment')
    auth_header = env('MODEL_GATEWAY_AUTH_HEADER', 'x-api-key')
    session = requests.Session()
    session.headers[auth_header] = ('Bearer ' + api_key) if auth_header.lower() == 'authorization' else api_key
    if args.probe:
        folder, path, _ = images[0]
        result = request_one(session, args.url, path, args.timeout)
        print(f'Probe OK: {folder}/{path.name} -> {result["predicted_category"]}; top3={result["top3"]}')
        return

    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / f'siglip_benchmark_{args.device.upper()}.csv'
    rows = []
    total = len(images) * args.repeats
    failures = 0
    for folder, path, truth in images:
        for repeat in range(1, args.repeats + 1):
            record = {key: '' for key in FIELDS}
            record.update(category=folder, filename=path.name, repeat=repeat)
            try:
                record.update(request_one(session, args.url, path, args.timeout))
                status = 'OK'
            except Exception as exc:
                failures += 1
                status = f'ERROR {type(exc).__name__}: {exc}'
            rows.append(record)
            write_csv(output, rows)
            print(f'[{len(rows)}/{total}] {folder}/{path.name} #{repeat}: {status} '
                  f'{record["predicted_category"]} {record["backend_total_ms"]} ms', flush=True)
            if args.delay and len(rows) < total:
                time.sleep(args.delay)
    print(f'Finished: {len(rows)} rows, {failures} failed; CSV: {output.resolve()}')
    print('NOTE: --device names the result file only; actual CPU/GPU is determined by the server.')
    print('NOTE: Negative images are evaluated as a group, not exact negative subclass.')

if __name__ == '__main__':
    main()
