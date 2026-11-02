"""Compare real single-image and batched Paddle Text Detection on ROI images."""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import manager, predict, resolve_ocr
from app.main import paddle_detection_batch


def detection_values(item):
    raw = item.get('res', item)
    if not isinstance(raw, dict) or 'dt_polys' not in raw:
        raise RuntimeError('Detection output lacks dt_polys; cannot validate equivalence')
    return raw['dt_polys'], raw.get('dt_scores')


def equal_scores(first, second):
    if first is None or second is None:
        return first is None and second is None
    try:
        a, b = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
        return a.shape == b.shape and bool(np.allclose(a, b, rtol=1e-5, atol=1e-4))
    except (TypeError, ValueError):
        return False


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', nargs='+', help='Paths to ROI image files (input order is retained)')
    parser.add_argument('--version', default='v6')
    parser.add_argument('--model-id', default='default')
    parser.add_argument('--batch-size', type=int, default=4)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if args.batch_size < 2 or args.repeat < 1:
        parser.error('batch size must be at least 2 and repeat must be positive')
    images = []
    for path in args.images:
        image = cv2.imread(path)
        if image is None:
            parser.error(f'Unable to read image: {path}')
        images.append(image)
    if len(images) < 2:
        parser.error('at least two ROI images are needed to test batching')
    entry = resolve_ocr('det', args.version, args.model_id)
    model, load_ms = manager.get_ocr('det', entry)
    predict('det', images[0], model)
    singles_ms, batches_ms, comparisons = [], [], []
    for iteration in range(args.repeat):
        start = time.perf_counter()
        singles = [predict('det', image, model)[0] for image in images]
        singles_ms.append(round((time.perf_counter() - start) * 1000, 2))
        start = time.perf_counter()
        batches = paddle_detection_batch(model, images, batch_size=args.batch_size)['results']
        batches_ms.append(round((time.perf_counter() - start) * 1000, 2))
        for index, (single, batch) in enumerate(zip(singles, batches)):
            single_polys, single_scores = detection_values(single)
            batch_polys, batch_scores = detection_values(batch)
            comparisons.append({'iteration': iteration + 1, 'image_index': index + 1,
                                'same_polygons': single_polys == batch_polys,
                                'scores_close': equal_scores(single_scores, batch_scores),
                                'single_box_count': len(single_polys), 'batch_box_count': len(batch_polys)})
    same = all(item['same_polygons'] and item['scores_close'] for item in comparisons)
    print(json.dumps({'model': entry['model_name'], 'model_id': args.model_id, 'model_load_ms': load_ms,
                      'image_count': len(images), 'batch_size': args.batch_size,
                      'single_ms': singles_ms, 'batch_ms': batches_ms,
                      'single_median_ms': round(statistics.median(singles_ms), 2),
                      'batch_median_ms': round(statistics.median(batches_ms), 2),
                      'all_equivalent': same, 'comparisons': comparisons}, indent=2))
    return 0 if same else 2


if __name__ == '__main__':
    raise SystemExit(run())
