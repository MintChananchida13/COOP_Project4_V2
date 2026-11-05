"""Offline Table output-scan comparison. No network, model calls or result cache."""
import argparse
import copy
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'project_backend'))

import numpy as np
from app.model_runtime import table_recognition_v2_adapter as table


def reference_collect(value):
    if value is None:
        return []
    if isinstance(value, dict):
        result = [value]
        for child in value.values():
            result.extend(reference_collect(child))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for child in value:
            if child is None or isinstance(child, (str, int, float, bool, np.generic)):
                continue
            result.extend(reference_collect(child))
        return result
    converted = table._as_dict(value)
    return [converted] if converted else []


def synthetic_output():
    boxes, rec_boxes, texts = [], [], []
    for row in range(11):
        for col in range(3):
            x, y = col * 100, row * 40
            boxes.append([x, y, x + 100, y + 40])
            rec_boxes.append([x + 4, y + 4, x + 90, y + 30])
            texts.append(f'Row {row} Column {col}')
    return {'table_res_list': [{'cell_box_list': boxes, 'table_ocr_pred': {
        'rec_boxes': rec_boxes, 'rec_texts': texts, 'rec_scores': [.9] * len(texts)}}],
        'input_img': [[[0, 1, 2]] * 600] * 600}


def comparable(result):
    result = copy.deepcopy(result)
    debug = result.get('table_debug') or {}
    debug.pop('elapsed_seconds', None)
    debug.pop('post_processing_timing', None)
    return result


def run(raw, image, repeats):
    scans = {'before': reference_collect, 'after': table._collect_dicts}
    measurements = {name: [] for name in scans}
    reference_result = None
    for repeat in range(repeats):
        for name in (('before', 'after') if repeat % 2 == 0 else ('after', 'before')):
            parse_started = time.perf_counter()
            decoded = json.loads(raw)  # Same decoder and fresh input for both paths.
            parse_ms = (time.perf_counter() - parse_started) * 1000
            output = table._unwrap_remote_result(decoded)
            with patch.object(table, '_collect_dicts', side_effect=scans[name]):
                started = time.perf_counter()
                result = table._reconstruct_with_test_structure_rules(output, image, started)
                reconstruction_ms = (time.perf_counter() - started) * 1000
            current = comparable(result)
            if reference_result is None:
                reference_result = current
            elif current != reference_result:
                raise AssertionError('Response changed beyond measured timing fields')
            measurements[name].append({
                'json_parse_ms': parse_ms,
                'output_scan_ms': result['table_debug']['post_processing_timing']['output_scan_ms'],
                'post_processing_ms': reconstruction_ms,
                'parse_and_post_processing_ms': parse_ms + reconstruction_ms,
            })
    return {'scope': 'Offline JSON parse and table post-processing only; no HTTP or model inference',
            'source': 'Identical JSON response, alternating order, fresh parse per repetition',
            'repeats': repeats, 'response_bytes': len(raw.encode('utf-8')),
            'responses_equal_except_measured_timing': True, 'model_calls': 0,
            'median_ms': {name: {key: round(statistics.median(row[key] for row in rows), 2)
                                for key in rows[0]} for name, rows in measurements.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--response-json', type=Path, help='Optional saved, already-unwrapped Table JSON output')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'table_scan_performance.json')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--width', type=int, default=300)
    parser.add_argument('--height', type=int, default=440)
    args = parser.parse_args()
    if min(args.repeats, args.width, args.height) < 1:
        parser.error('Repeats and dimensions must be positive')
    raw = args.response_json.read_text(encoding='utf-8') if args.response_json else json.dumps(synthetic_output())
    result = run(raw, np.zeros((args.height, args.width, 3), dtype=np.uint8), args.repeats)
    result['fixture'] = str(args.response_json) if args.response_json else 'synthetic: 33 OCR tokens + 600x600 RGB numeric array'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
