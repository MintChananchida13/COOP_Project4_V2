"""Compare full anchor batching with staged execution under the SAME new decision rule.

Default: deterministic simulated transport. --live --manifest uses real backend
OCR/SigLIP/Gateway adapters and configured categories. No global settings change.
The measured end-to-end interval is backend verification, not browser end-to-end.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'project_backend'))
from app.processing import staged_verification as engine


def compare(templates, page_paths, callbacks, full):
    batch, judge = callbacks()
    started = time.perf_counter()
    if full:
        requests = []
        for template, fields in templates:
            aliased = [{**field, 'id': engine.batch_key(template['template_id'], field.get('id'),
                        int(field.get('page_number') or 1), index)} for index, field in enumerate(fields)]
            requests.append((template, aliased))
        outputs = batch(requests)
        first = True
        def cached(_requests):
            nonlocal first
            if first:
                first = False
                return outputs
            return outputs[0], outputs[1], {}, outputs[3], {}
        batch = cached
    results, metrics, *_ = engine.run(templates, page_paths, batch, judge)
    metrics['verification_end_to_end_ms'] = (time.perf_counter() - started) * 1000
    metrics['error_anchor_count'] = sum(bool(item.get('error')) for result in results.values() for item in result['checked_fields'])
    if full:
        metrics['stage_count'] = 1 if any(fields for _, fields in templates) else 0
    passed = [dict(template_id=tid, final_passed=value['passed'], verification_anchor_count=value['total_anchor_count'])
              for tid, value in results.items() if value['passed']]
    winner = min(passed, key=engine.candidate_order)['template_id'] if passed else None
    return {key: metrics[key] for key in ('verification_end_to_end_ms', 'model_runtime_ms', 'roi_crop_count',
            'model_call_count', 'stage_count', 'error_anchor_count')}, winner


def simulated_case(all_pass=False):
    templates = []
    for index in range(24):
        fields = [{'id': f'anchor-{step}', 'data_type': 'image' if step % 2 else 'text', 'page_number': step % 2 + 1,
                   'fixture_pass': all_pass or index == 0 or step != 0} for step in range(4)]
        templates.append(({'template_id': f'template-{index:02}'}, fields))
    def callbacks():
        def batch(requests):
            fields = [field for _, fields in requests for field in fields]
            text = [field for field in fields if field['data_type'] == 'text']
            images = [field for field in fields if field['data_type'] == 'image']
            # Explicit simulated request overhead and per-ROI cost, not model measurements.
            model_calls = (2 if text else 0) + (1 if images else 0)
            started = time.perf_counter()
            time.sleep(.0005 * model_calls + .001 * len(fields))
            elapsed = (time.perf_counter() - started) * 1000
            values = {field['id']: {'passed': field['fixture_pass'], 'score': float(field['fixture_pass'])} for field in fields}
            return ({field['id']: values[field['id']] for field in text}, {},
                    {'roi_crop_count': len(text), 'text_det_inference_ms': elapsed,
                     'model_calls': [{}] * (2 if text else 0)},
                    {field['id']: values[field['id']] for field in images},
                    {'image_roi_count': len(images), 'model_calls': [{}] * (1 if images else 0)})
        def judge(tid, field, text, errors, images):
            return (images if field['data_type'] == 'image' else text)[field['id']], {}
        return batch, judge
    return templates, {1: 'simulated-page-1', 2: 'simulated-page-2'}, callbacks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, default=ROOT / 'benchmark/output/staged_batch.json')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    if args.live:
        if not args.manifest:
            parser.error('--live requires --manifest with final page paths and eligible template anchors')
        from app.core.local_env import load_local_env
        load_local_env()
        from app.processing import detection_service as detection
        data = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
        templates = [(item, item['verification_anchors']) for item in data['templates']]
        page_paths = {int(key): str((args.manifest.parent / value).resolve()) for key, value in data['pages'].items()}
        def callbacks():
            batch, judge = detection._staged_anchor_callbacks(page_paths, {}, data.get('strategy', 'standard'))
            if not data.get('ocr_paths'):
                return batch, judge
            # Explicit fixture paths avoid reading/changing the production settings database.
            from unittest.mock import patch
            from app.core import model_runtime_client
            def configured_batch(requests):
                with patch.object(model_runtime_client, '_active_ocr_path',
                                  side_effect=lambda kind, mode: data['ocr_paths'][kind][mode]):
                    outputs = batch(requests)
                    if outputs[1]:
                        raise RuntimeError(f"Live text batch failed: {next(iter(outputs[1].values()))}")
                    return outputs
            return configured_batch, judge
        cases = [(args.manifest.stem, (templates, page_paths, callbacks))]
    else:
        cases = [('early_rejection', simulated_case()), ('all_anchors_pass', simulated_case(True))]
    report = {'transport': 'live_gateway' if args.live else 'simulated',
              'scope': 'backend verification only; excludes browser, upload, prefilter, extraction',
              'decision_rule': 'all anchors pass, most anchors, template_id ascending', 'cases': []}
    for name, (templates, paths, callbacks) in cases:
        measurements = {'full_batch': [], 'staged_batch': []}
        winners = []
        # Alternate order to reduce systematic warm-up bias; no cache reset is claimed.
        for repeat in range(args.repeats):
            current = {}
            for full in ([True, False] if repeat % 2 == 0 else [False, True]):
                metrics, winner = compare(templates, paths, callbacks, full)
                if args.live and metrics['error_anchor_count']:
                    raise RuntimeError('Live verification returned anchor errors; this run is not a valid performance comparison.')
                label = 'full_batch' if full else 'staged_batch'
                measurements[label].append(metrics)
                current[label] = winner
            if current['full_batch'] != current['staged_batch']:
                raise RuntimeError(f'Matching disagreement: {current}')
            winners.append(current['staged_batch'])
        medians = {label: {key: statistics.median(row[key] for row in rows) if all(row[key] is not None for row in rows) else None
                           for key in rows[0]} for label, rows in measurements.items()}
        report['cases'].append({'name': name, 'winner_by_repeat': winners, 'median': medians, 'runs': measurements})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({**report, 'cases': [{key: value for key, value in case.items() if key != 'runs'} for case in report['cases']]}, indent=2))


if __name__ == '__main__':
    main()
