import copy
import ast
import asyncio
import gc
from contextvars import ContextVar
import inspect
import json
import os
import threading
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, mock_open, patch

from app.core import model_runtime_client as client
from app.processing import memory_usage


class OcrMemoryTests(unittest.TestCase):
    def test_async_endpoint_queues_only_job_id_and_preserves_response(self):
        from fastapi import BackgroundTasks, HTTPException
        from typing import Dict, Any
        import time
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8'))
        node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == 'process_document')
        node.decorator_list = []
        enqueue = Mock()
        run = Mock()
        namespace = dict(DocumentPayload=object, BackgroundTasks=BackgroundTasks, Dict=Dict, Any=Any,
            HTTPException=HTTPException, time=time, logger=Mock(), _ensure_processing_available_for_context=Mock(),
            create_ocr_job=Mock(return_value='job-id'), run_ocr_job=run,
            memory_usage=SimpleNamespace(enqueue=enqueue), process_document_payload=Mock(),
            PaddleThaiOcrUnavailableError=RuntimeError, LayoutAnalysisUnavailableError=ValueError)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), namespace)
        tasks = BackgroundTasks()
        response = asyncio.run(namespace['process_document'](SimpleNamespace(async_mode=True, context='user'), tasks))
        self.assertEqual(set(response), {'success', 'job_id', 'status', 'job_create_ms'})
        self.assertEqual(response['job_id'], 'job-id')
        self.assertEqual(response['status'], 'queued')
        self.assertEqual(tasks.tasks[0].args, (run, 'job-id'))
        asyncio.run(tasks())
        enqueue.assert_called_once_with(run, 'job-id')
        run.assert_not_called()

    def test_linux_rss_and_peak_are_os_measurements_and_unknown_limit_stays_null(self):
        def read(path, **kwargs):
            return {'memory.current': '1000', 'memory.peak': '2000', 'memory.max': 'max'}[path.name]
        with patch.object(memory_usage.sys, 'platform', 'linux'), \
             patch('builtins.open', mock_open(read_data='VmRSS: 4 kB\nVmHWM: 8 kB\n')), \
             patch.object(Path, 'read_text', read):
            snapshot = memory_usage.snapshot()
        self.assertEqual(snapshot['rss_bytes'], 4096)
        self.assertEqual(snapshot['process_peak_rss_bytes'], 8192)
        self.assertEqual(snapshot['cgroup_peak_bytes'], 2000)
        self.assertIsNone(snapshot['cgroup_limit_bytes'])

    def test_enqueue_returns_before_job_finishes_and_nested_guard_does_not_deadlock(self):
        entered = threading.Event()
        release = threading.Event()
        @memory_usage.serialized
        def job(job_id):
            entered.set()
            release.wait(3)
            return job_id
        with patch.dict(os.environ, {'OCR_MEMORY_DIAGNOSTICS': 'false'}):
            future = memory_usage.enqueue(job, 'job-id-only')
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(future.done())
            finally:
                release.set()
            self.assertEqual(future.result(timeout=3), 'job-id-only')

    def test_worker_preserves_request_context_without_leaking_to_next_call(self):
        value = ContextVar('request_context', default=None)
        @memory_usage.serialized
        def read():
            return value.get()
        with patch.dict(os.environ, {'OCR_MEMORY_DIAGNOSTICS': 'false'}):
            token = value.set('request-one')
            try:
                self.assertEqual(read(), 'request-one')
            finally:
                value.reset(token)
            self.assertIsNone(read())

    def test_scanner_releases_raw_tree_without_waiting_for_cyclic_gc(self):
        from app.model_runtime import table_recognition_v2_adapter as table
        class Tracked(dict):
            pass
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            value = Tracked({'pixels': [[[0, 1, 2]]] * 1000})
            reference = weakref.ref(value)
            inventory = table._collect_dicts(value)
            self.assertIs(inventory[0], value)
            del value, inventory
            self.assertIsNone(reference())
        finally:
            if was_enabled:
                gc.enable()

    def test_json_parse_does_not_keep_request_or_response_bytes(self):
        original_loads = json.loads
        request_payload = {'image': 'same-input'}
        original_payload = copy.deepcopy(request_payload)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"success":true,"result":{"rows":[["same"]]}}'
        seen = {}
        def parse(raw):
            frame = inspect.currentframe()
            try:
                frame = frame.f_back
                while frame and frame.f_code.co_name != '_post_predict':
                    frame = frame.f_back
                seen.update({key: key in frame.f_locals for key in ('body', 'request', 'payload', 'raw_bytes')})
            finally:
                del frame
            return original_loads(raw)
        with patch.object(client, 'runtime_url', return_value='http://localhost/table'), \
             patch.object(client.urllib.request, 'urlopen', return_value=response), \
             patch.object(client.json, 'loads', side_effect=parse), \
             patch.dict(os.environ, {'OCR_MEMORY_DIAGNOSTICS': 'false'}):
            result = client._post_predict(client.ModelRuntimeKind.TABLE, request_payload, timing={})
        self.assertEqual(result, {'rows': [['same']]})
        self.assertEqual(request_payload, original_payload)
        self.assertTrue(seen)
        self.assertFalse(any(seen.values()))

    def test_serialization_is_reentrant_and_exception_safe(self):
        @memory_usage.serialized
        def inner():
            return 'same'
        @memory_usage.serialized
        def outer(fail=False):
            if fail:
                raise ValueError('test')
            return inner()
        with patch.dict(os.environ, {'OCR_MEMORY_DIAGNOSTICS': 'false'}):
            with self.assertRaises(ValueError):
                outer(True)
            self.assertEqual(outer(), 'same')

    def test_second_job_waits_before_entering_heavy_work(self):
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        second_started = threading.Event()
        @memory_usage.serialized
        def work(first):
            if first:
                first_entered.set()
                release_first.wait(3)
            else:
                second_entered.set()
        def second():
            second_started.set()
            work(False)
        with patch.dict(os.environ, {'OCR_MEMORY_DIAGNOSTICS': 'false'}):
            a = threading.Thread(target=work, args=(True,))
            b = threading.Thread(target=second)
            try:
                a.start()
                self.assertTrue(first_entered.wait(2))
                b.start()
                self.assertTrue(second_started.wait(2))
                self.assertFalse(second_entered.wait(.05))
            finally:
                release_first.set()
                a.join(3)
                if b.ident is not None:
                    b.join(3)
            self.assertTrue(second_entered.is_set())

    def test_diagnostics_contain_only_memory_metadata_and_do_not_change_results(self):
        with patch.dict(os.environ, {'OCR_MEMORY_DIAGNOSTICS': 'true'}), \
             patch.object(memory_usage, 'snapshot', return_value={'rss_bytes': 100, 'process_peak_rss_bytes': 200}), \
             self.assertLogs(memory_usage.logger, level='INFO') as logs:
            @memory_usage.serialized
            def process():
                return memory_usage.call('parse', lambda: {'text': 'PRIVATE_DOCUMENT_CONTENT'})
            self.assertEqual(process(), {'text': 'PRIVATE_DOCUMENT_CONTENT'})
            memory_usage.checkpoint('counts', text='PRIVATE_DOCUMENT_CONTENT', rows=3)
        self.assertNotIn('PRIVATE_DOCUMENT_CONTENT', '\n'.join(logs.output))
        self.assertIn('sampled_peak_rss_bytes', '\n'.join(logs.output))
        self.assertIsNone(memory_usage._trace.get())

    def test_failed_stage_releases_sampler_context(self):
        with patch.dict(os.environ, {'OCR_MEMORY_DIAGNOSTICS': 'true'}):
            with self.assertRaises(ValueError), memory_usage.trace('failure'), memory_usage.span('parse'):
                raise ValueError('same error')
        self.assertIsNone(memory_usage._trace.get())


if __name__ == '__main__':
    unittest.main()
