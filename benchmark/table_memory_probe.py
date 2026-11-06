"""Fresh-process RSS comparison: real image + synthetic Table response, no model calls."""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'project_backend'))


def prepare(path):
    boxes, rec, texts = [], [], []
    # Merged header followed by two columns, without using image rule detection.
    boxes.append([0, 0, 200, 40])
    rec.append([5, 5, 190, 30])
    texts.append('Merged header')
    for row in range(1, 12):
        for col in range(2):
            x, y = col * 100, row * 40
            boxes.append([x, y, x + 100, y + 40])
            rec.append([x + 5, y + 5, x + 90, y + 30])
            texts.append(f'Row {row} column {col}')
    output = {'success': True, 'result': {'table_res_list': [{
        'cell_box_list': boxes, 'table_ocr_pred': {'rec_boxes': rec, 'rec_texts': texts,
                                               'rec_scores': [.9] * len(texts)}}],
        'synthetic_numeric_data': [[[0, 1, 2]] * 800] * 600}}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        json.dump(output, handle)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fingerprint(result):
    # Timings naturally differ; retain all OCR/table values, cells and audit.
    result.pop('extraction_timing', None)
    for item in result['extracted_data']:
        debug = item.get('table_debug') or {}
        for key in ('post_processing_timing', 'elapsed_seconds', 'runtime_client', 'table_wait_ms', 'table_runtime_ms'):
            debug.pop(key, None)
    return hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--mode', choices=('before', 'after'), default='after')
    parser.add_argument('--image', type=Path, default=ROOT / 'input/THAI_TABLE_081413.png')
    parser.add_argument('--fixture', type=Path, default=ROOT / 'output/table_memory_synthetic.json')
    parser.add_argument('--baseline-dir', type=Path, default=ROOT / 'output/memory_before')
    parser.add_argument('--run-id', type=int, default=0)
    parser.add_argument('--jobs', type=int, default=1)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    if args.prepare:
        prepare(args.fixture)
        print('Prepared synthetic response; no document data or Gateway output saved.')
        return
    os.environ['OCR_MEMORY_DIAGNOSTICS'] = 'false'
    # Import the real application without executing FastAPI startup/DB warmup.
    import main as application
    import base64
    from app.core import model_runtime_client
    from app.model_runtime import table_recognition_v2_adapter
    from app.processing.memory_usage import snapshot
    from PIL import Image
    # Load both implementations in both modes, so import/AST overhead is matched.
    legacy_client = load(args.baseline_dir / 'app/core/model_runtime_client.py', 'memory_baseline_client')
    legacy_adapter = load(args.baseline_dir / 'app/model_runtime/table_recognition_v2_adapter.py', 'memory_baseline_table')
    tree = ast.parse((args.baseline_dir / 'main.py').read_text(encoding='utf-8'))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'process_document_payload')
    legacy_namespace = dict(application.__dict__)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'memory_baseline_main', 'exec'), legacy_namespace)
    legacy_function = legacy_namespace['process_document_payload']
    del tree, node
    import gc
    gc.collect()  # Benchmark setup only, never forced in the processing pipeline.
    client = legacy_client if args.mode == 'before' else model_runtime_client
    adapter = legacy_adapter if args.mode == 'before' else table_recognition_v2_adapter
    function = legacy_function if args.mode == 'before' else application.process_document_payload
    namespace = legacy_namespace if args.mode == 'before' else function.__wrapped__.__wrapped__.__globals__
    # Both modes use exactly one mocked HTTP response from disk; no Gateway call.
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return args.fixture.read_bytes()
    baseline = snapshot()
    with Image.open(args.image) as image:
        width, height = image.size
    data = 'data:image/png;base64,' + base64.b64encode(args.image.read_bytes()).decode('ascii')
    payload = application.DocumentPayload(image=data, rois=[application.ROIModel(
        fieldName='table', roiId=1, type='table', extractionMethod='table_recognition_v2',
        x=0, y=0, width=width, height=height)])
    with patch.object(client, 'runtime_url', return_value='http://localhost/mock-table'), \
         patch.object(client.urllib.request, 'urlopen', return_value=Response()) as request, \
         patch.object(adapter, 'remote_recognize_table_raw', side_effect=client.remote_recognize_table_raw), \
         patch.object(adapter, '_use_remote_runtime', return_value=True), \
         patch.dict(namespace, {'process_roi_with_engine': lambda crop, roi: adapter.recognize_table_v2_local(crop),
                                '_save_cropped_roi': lambda *args: ''}):
        if args.jobs == 1:
            results = [function(payload)]
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                results = list(pool.map(function, [payload] * args.jobs))
    measured = snapshot()
    request_hashes = [hashlib.sha256(call.args[0].data).hexdigest() for call in request.call_args_list]
    hashes = [fingerprint(result) for result in results]
    assert len(set(hashes)) == 1
    report = {'mode': args.mode, 'platform': sys.platform, 'image': args.image.name,
        'image_dimensions': [width, height], 'framework_import_baseline': baseline,
        'after_processing': measured, 'response_fingerprint': hashes[0], 'jobs': args.jobs,
        'mocked_http_calls': request.call_count, 'actual_model_calls': 0,
        'gateway_request_fingerprints': request_hashes,
        'fixture_bytes': args.fixture.stat().st_size,
        'scope': 'Real image, synthetic response; startup caches/DB and real Gateway are not measured'}
    suffix = f'_{args.run_id}' if args.run_id else ''
    workload = f'_jobs{args.jobs}' if args.jobs != 1 else ''
    path = ROOT / 'output' / f'table_memory_{args.mode}{workload}{suffix}.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
