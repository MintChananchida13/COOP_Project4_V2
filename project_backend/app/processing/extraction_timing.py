"""Request-local OCR measurements; never stores image or recognized text payloads."""
from contextvars import ContextVar
from functools import wraps
import logging
import time

from app.processing.model_timing import summarize_model_calls

_capture = ContextVar('extraction_timing', default=None)
logger = logging.getLogger(__name__)


def call(stage, function, *args, **kwargs):
    state = _capture.get()
    if state is None:
        return function(*args, **kwargs)
    if stage == 'fixed_text_batch':
        kwargs.setdefault('timing', {})
    started = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        item = state['stages'].setdefault(stage, {'elapsed_ms': 0.0, 'count': 0})
        item['elapsed_ms'] += (time.perf_counter() - started) * 1000
        item['count'] += 1
        if stage == 'fixed_text_batch':
            measurements = kwargs.get('timing') or {}
            state['ocr_counts'] = {key: measurements.get(key) for key in (
                'text_detection_batch_size', 'text_detection_batch_chunks',
                'text_recognition_batch_size', 'text_recognition_fallback_size',
            )}


def instrument_runtime(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        state = _capture.get()
        if state is None:
            return function(*args, **kwargs)
        collector = kwargs.get('timing')
        if not isinstance(collector, dict):
            collector = {}
            kwargs['timing'] = collector
        try:
            return function(*args, **kwargs)
        finally:
            state['model_calls'].append(dict(collector))
    return wrapped


def measure_processing(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        state = {'stages': {}, 'model_calls': [], 'ocr_counts': {}}
        token = _capture.set(state)
        started = time.perf_counter()
        try:
            result = function(*args, **kwargs)
            elapsed = (time.perf_counter() - started) * 1000
            summary = summarize_model_calls(state['model_calls'])
            result['extraction_timing'] = {
                'processing_ms': round(elapsed, 2),
                'fixed_text_counts': state['ocr_counts'],
                'stages': {key: {**value, 'elapsed_ms': round(value['elapsed_ms'], 2)}
                           for key, value in state['stages'].items()},
                **summary,
            }
            logger.info('OCR extraction timing: %s', result['extraction_timing'])
            return result
        finally:
            _capture.reset(token)
    return wrapped
