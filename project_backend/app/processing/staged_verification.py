"""Stage scheduling independent of model transport and extraction processing."""
from __future__ import annotations

import json
import time
from app.processing.model_timing import summarize_model_calls


def merge_metrics(total: dict, value: dict) -> None:
    for key, item in value.items():
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            total[key] = total.get(key, 0) + item
        elif isinstance(item, list):
            total.setdefault(key, []).extend(item)
        elif isinstance(item, dict):
            merge_metrics(total.setdefault(key, {}), item)


def batch_key(template_id, anchor_id, page, index):
    return json.dumps([str(template_id), str(anchor_id or ''), page, index], ensure_ascii=False)


def run(templates, page_paths, batch, judge, progress=None):
    """One anchor per surviving template per stage. Completed templates stop scheduling."""
    started = time.perf_counter()
    states = {str(template['template_id']): {'fields': fields, 'checked': [], 'timing': {},
              'passed': False, 'status': 'no_verification_fields', 'failed_stage': None}
              for template, fields in templates}
    active = [(template, fields) for template, fields in templates if fields]
    stages, text_total, image_total = [], {}, {}
    while active:
        stage_number = len(stages) + 1
        stage_started = time.perf_counter()
        scheduled = []
        for template, fields in active:
            tid = str(template['template_id'])
            index = len(states[tid]['checked'])
            original = fields[index]
            page = int(original.get('page_number') or 1)
            key = batch_key(tid, original.get('id'), page, index)
            scheduled.append((template, original, {**original, 'id': key, 'page_number': page}, key))
        # Missing pages reject locally and never enter a model batch.
        requests = [(template, [field]) for template, _, field, _ in scheduled if field['page_number'] in page_paths]
        if progress:
            progress({'stage': 'anchor_stage', 'stage_number': stage_number, 'templates_remaining': len(active)})
        text, errors, text_debug, images, image_debug = batch(requests) if requests else ({}, {}, {}, {}, {})
        merge_metrics(text_total, text_debug)
        merge_metrics(image_total, image_debug)
        survivors, mapping, rejected, completed = [], [], [], []
        for template, original, field, key in scheduled:
            tid = str(template['template_id'])
            state = states[tid]
            if field['page_number'] not in page_paths:
                checked, anchor_timing = {'passed': False, 'error': 'query_page_missing', 'score': 0.0}, {}
            else:
                try:
                    checked, anchor_timing = judge(tid, field, text, errors, images)
                except Exception as error:
                    checked, anchor_timing = {'passed': False, 'score': 0, 'error': f'anchor_verification_error: {error}'}, {}
            passed = bool(checked.get('passed')) and not checked.get('error')
            checked = {**checked, 'passed': passed, 'template_id': tid, 'field_id': original.get('id'),
                       'required': True,
                       'anchor_id': original.get('id'), 'page_number': field['page_number'],
                       'anchor_type': original.get('data_type'), 'stage': stage_number, 'batch_key': key}
            state['checked'].append(checked)
            merge_metrics(state['timing'], anchor_timing)
            mapping.append({'template_id': tid, 'anchor_id': original.get('id'), 'page_number': field['page_number'],
                            'batch_key': key, 'passed': passed})
            if not passed:
                state.update(status='anchor_failed', failed_stage=stage_number)
                rejected.append(tid)
            elif len(state['checked']) == len(state['fields']):
                state.update(status='verified', passed=True)
                completed.append(tid)
            else:
                survivors.append((template, state['fields']))
        stages.append({'stage': stage_number, 'scheduled_anchor_count': len(scheduled),
                       'roi_crop_count': int(text_debug.get('roi_crop_count') or 0) + int(image_debug.get('image_roi_count') or 0),
                       'model_call_count': len(text_debug.get('model_calls') or []) + len(image_debug.get('model_calls') or []),
                       'elapsed_ms': (time.perf_counter() - stage_started) * 1000,
                       'rejected_template_ids': rejected, 'completed_template_ids': completed, 'mapping': mapping})
        active = survivors
    results = {}
    for tid, state in states.items():
        checked = state['checked']
        averages = {kind: sum(float(item.get('score') or 0) for item in checked if item.get('anchor_type') == kind)
                    / max(1, sum(item.get('anchor_type') == kind for item in checked)) for kind in ('text', 'image')}
        results[tid] = {'passed': state['passed'], 'score': float(state['passed']), 'status': state['status'],
                        'required_passed': state['passed'], 'checked_fields': checked,
                        'text_anchor_score': averages['text'], 'image_anchor_score': averages['image'],
                        'failed_stage': state['failed_stage'], 'timing': state['timing'],
                        'all_anchors_required': True, 'total_anchor_count': len(state['fields'])}
    inference_values = [value for value in (text_total.get('text_det_inference_ms'), text_total.get('text_rec_inference_ms'),
                       image_total.get('siglip_inference_ms')) if isinstance(value, (int, float))]
    recorded = summarize_model_calls([text_total, image_total])
    if recorded['model_call_count']:
        inference_values = list(recorded['inferences'].values())
    return results, {'stage_count': len(stages), 'stages': stages,
                     'roi_crop_count': sum(stage['roi_crop_count'] for stage in stages),
                     'model_call_count': sum(stage['model_call_count'] for stage in stages),
                     'model_runtime_ms': sum(inference_values) if inference_values and all(value is not None for value in inference_values) else None,
                     'model_timing_missing_reason': recorded.get('model_timing_missing_reason'),
                     'elapsed_ms': (time.perf_counter() - started) * 1000}, text_total, image_total


def candidate_order(candidate):
    return (not bool(candidate.get('final_passed')), -int(candidate.get('verification_anchor_count') or 0),
            str(candidate.get('template_id') or ''))
