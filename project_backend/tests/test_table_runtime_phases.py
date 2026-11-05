import json
import unittest
from unittest.mock import MagicMock, patch

from app.core import model_runtime_client as client
from app.processing.model_timing import summarize_model_calls


class TableRuntimePhasesTests(unittest.TestCase):
    def test_table_diagnostic_metadata_never_serializes_raw_output(self):
        output = {'raw': {'pixels': [[[0, 0, 0]]] * 10}, 'model': 'table'}
        with patch.object(client, '_json_size_bytes', side_effect=AssertionError('raw serialized')):
            fields = client._field_size_summary(output, measure_bytes=False)
        self.assertTrue(all(field['bytes'] is None for field in fields))
        self.assertEqual(fields[0]['field_count'], 1)
        self.assertEqual(output['raw']['pixels'][0], [[0, 0, 0]])

    def test_timing_separates_http_parse_and_diagnostics_with_one_call(self):
        clock = [100.0]
        def advance(seconds):
            clock[0] += seconds
        response = MagicMock()
        payload = {'success': True, 'result': {'table': 'unchanged'},
                   'timing': {'queue_wait_ms': 0, 'inference_ms': 10}}
        raw = json.dumps(payload).encode()
        def read():
            advance(3)
            return raw
        def open_request(*args, **kwargs):
            advance(2)
            return response
        response.__enter__.return_value.read.side_effect = read
        actual_loads = json.loads
        def parse(raw):
            advance(1)
            return actual_loads(raw)
        def summarize(*args, **kwargs):
            self.assertFalse(kwargs['measure_bytes'])
            advance(5)
            return []
        timing = {}
        with patch.object(client, 'runtime_url', return_value='http://localhost/table'), \
             patch.object(client.urllib.request, 'urlopen', side_effect=open_request) as request, \
             patch.object(client.time, 'perf_counter', side_effect=lambda: clock[0]), \
             patch.object(client.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(client.json, 'loads', side_effect=parse), \
             patch.object(client, '_field_size_summary', side_effect=summarize):
            result = client._post_predict(client.ModelRuntimeKind.TABLE, {}, timing=timing)
        self.assertEqual(result, {'table': 'unchanged'})
        request.assert_called_once()
        self.assertEqual(timing['http_request_ms'], 5000)
        self.assertEqual(timing['response_read_ms'], 3000)  # subset of HTTP, not additional
        self.assertEqual(timing['json_parse_ms'], 1000)
        self.assertEqual(timing['response_diagnostics_ms'], 10000)
        self.assertEqual(timing['total_runtime_client_ms'], 16000)
        summary = summarize_model_calls([timing, timing])
        self.assertEqual(summary['model_call_count'], 1)
        self.assertEqual(summary['inferences']['table'], 10)
        self.assertEqual(summary['model_calls'][0]['queue_wait_ms'], 0)
        self.assertEqual(summary['model_calls'][0]['json_parse_ms'], 1000)

    def test_non_table_field_size_measurements_remain_exact(self):
        self.assertEqual(client._field_size_summary({'text': 'hello'})[0]['bytes'], 7)


if __name__ == '__main__':
    unittest.main()
