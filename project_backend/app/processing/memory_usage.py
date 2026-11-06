"""Bound heavy OCR work and log process-only memory metadata, never document data."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
logger.propagate = False
_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ocr-memory-worker')
_worker_state = threading.local()
_trace = ContextVar('ocr_memory_trace', default=None)
_windows_api = None


def enabled():
    return os.getenv('OCR_MEMORY_DIAGNOSTICS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}


def snapshot():
    """RSS and lifetime peak RSS, not Python heap-only measurements."""
    global _windows_api
    rss = peak = None
    try:
        if sys.platform.startswith('linux'):
            with open('/proc/self/status', encoding='ascii') as handle:
                for line in handle:
                    key, _, value = line.partition(':')
                    if key in ('VmRSS', 'VmHWM'):
                        measured = int(value.split()[0]) * 1024
                        if key == 'VmRSS':
                            rss = measured
                        else:
                            peak = measured
        elif sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes

            if _windows_api is None:
                class Counters(ctypes.Structure):
                    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                        ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                        ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                psapi = ctypes.WinDLL('psapi', use_last_error=True)
                kernel.GetCurrentProcess.restype = wintypes.HANDLE
                psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
                _windows_api = kernel, psapi, Counters
            kernel, psapi, Counters = _windows_api
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                rss, peak = counters.WorkingSetSize, counters.PeakWorkingSetSize
    except (OSError, ValueError, IndexError):
        pass
    result = {'rss_bytes': rss, 'process_peak_rss_bytes': peak}
    if sys.platform.startswith('linux'):
        for name, field in (('memory.current', 'cgroup_current_bytes'),
                            ('memory.peak', 'cgroup_peak_bytes'), ('memory.max', 'cgroup_limit_bytes')):
            try:
                value = Path('/sys/fs/cgroup', name).read_text(encoding='ascii').strip()
                result[field] = int(value) if value != 'max' else None
            except (OSError, ValueError):
                result[field] = None
    return result


def checkpoint(stage, **counts):
    if enabled():
        metadata = {key: value for key, value in counts.items()
                    if isinstance(value, (int, float, bool)) or value is None}
        logger.info('OCR_MEMORY %s', json.dumps({'pid': os.getpid(), 'stage': stage, **snapshot(), **metadata}))


class Trace:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.active = {}
        self.overall = None
        self.stages = {}
        self.baseline = snapshot()
        self.sample()

    def sample(self):
        value = snapshot()['rss_bytes']
        if value is None:
            return
        with self.lock:
            self.overall = max(self.overall or 0, value)
            for name in self.active:
                self.stages[name] = max(self.stages.get(name, 0), value)

    def poll(self):
        while not self.stop.wait(.1):
            self.sample()


@contextmanager
def trace(name):
    if not enabled() or _trace.get() is not None:
        yield
        return
    state = Trace()
    token = _trace.set(state)
    sampler = threading.Thread(target=state.poll, name='ocr-memory-sampler', daemon=True)
    sampler.start()
    try:
        checkpoint(name + '_start')
        yield
    finally:
        state.stop.set()
        sampler.join()
        state.sample()
        logger.info('OCR_MEMORY %s', json.dumps({'pid': os.getpid(), 'stage': name + '_end',
            'baseline_rss_bytes': state.baseline['rss_bytes'], 'sampled_peak_rss_bytes': state.overall,
            'stage_sampled_peak_rss_bytes': state.stages, **snapshot()}))
        _trace.reset(token)


@contextmanager
def span(name):
    state = _trace.get()
    if state is None:
        yield
        return
    with state.lock:
        state.active[name] = state.active.get(name, 0) + 1
    state.sample()
    checkpoint(name + '_start')
    try:
        yield
    finally:
        state.sample()
        checkpoint(name + '_end')
        with state.lock:
            state.active[name] -= 1
            if not state.active[name]:
                del state.active[name]


def call(stage, function, *args, **kwargs):
    with span(stage):
        return function(*args, **kwargs)


def enqueue(function, *args, **kwargs):
    """Queue only the job identifier after it has been persisted to the database."""
    started = time.perf_counter()
    context = copy_context()
    def execute():
        _worker_state.active = True
        try:
            if enabled():
                logger.info('OCR_MEMORY gate pid=%s wait_ms=%.2f', os.getpid(),
                            (time.perf_counter() - started) * 1000)
            with trace(function.__name__):
                return function(*args, **kwargs)
        finally:
            _worker_state.active = False
    return _worker.submit(context.run, execute)


def serialized(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        # Reuse one heavy-work thread per process, before loading job JSON or
        # decoding images. Nested calls execute directly instead of deadlocking
        # on their own worker. Only scheduling changes; no results are cached.
        if getattr(_worker_state, 'active', False):
            return function(*args, **kwargs)
        return enqueue(function, *args, **kwargs).result()
    return wrapped


def shutdown():
    _worker.shutdown(wait=True)
