import copy
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from app.model_runtime import table_recognition_v2_adapter as table


def reference_collect(value):
    """Original scanner, retained only as a regression/benchmark oracle."""
    if value is None:
        return []
    if isinstance(value, dict):
        nested = [value]
        for item in value.values():
            nested.extend(reference_collect(item))
        return nested
    if isinstance(value, (list, tuple)):
        rows = []
        for item in value:
            if item is None or isinstance(item, (str, int, float, bool, np.generic)):
                continue
            rows.extend(reference_collect(item))
        return rows
    item = table._as_dict(value)
    return [item] if item else []


class TableOutputScanTests(unittest.TestCase):
    def assert_same_inventory(self, value):
        expected = reference_collect(value)
        actual = table._collect_dicts(value)
        self.assertEqual([id(node) for node in actual], [id(node) for node in expected])

    def test_order_identity_shared_nodes_wrappers_and_scalar_arrays(self):
        shared = {'text': 'unchanged'}
        value = {'pixels': [[[0, 1, 2]] * 100] * 10,
            'tables': [{'child': shared}, shared, (), np.int64(3)],
            'wrappers': [SimpleNamespace(json={'wrapped': 1}), SimpleNamespace(res={'wrapped': 2})],
            'empty': {}, 'array': np.zeros((2, 2))}
        self.assert_same_inventory(value)

    def test_mixed_list_does_not_skip_dictionary_after_numbers(self):
        self.assert_same_inventory([0, 1, 2, {'table': [{'cell': 1}]}, 'text', None])

    def test_random_json_trees_match_original_scanner(self):
        rng = random.Random(20261105)
        def build(depth):
            if depth == 0:
                return rng.choice([None, True, False, 0, 1.25, 'ไทย English'])
            kind = rng.randrange(3)
            if kind == 0:
                return {str(index): build(depth - 1) for index in range(rng.randrange(5))}
            if kind == 1:
                return [build(depth - 1) for _ in range(rng.randrange(5))]
            return build(0)
        for _ in range(100):
            self.assert_same_inventory(build(5))

    def test_entire_reconstruction_response_is_identical_with_frozen_clock(self):
        from tests.test_table_postprocessing import TablePostprocessingTests
        output = TablePostprocessingTests().output()
        # Representative large numeric output alongside actual OCR metadata.
        output['input_img'] = [[[0, 1, 2]] * 40] * 30
        original = copy.deepcopy(output)
        image = np.zeros((80, 200, 3), dtype=np.uint8)
        with patch.object(table.time, 'perf_counter', return_value=100):
            with patch.object(table, '_collect_dicts', side_effect=reference_collect):
                before = table._reconstruct_with_test_structure_rules(output, image, 100)
            after = table._reconstruct_with_test_structure_rules(output, image, 100)
        self.assertEqual(after, before)  # Includes debug, source audit, cells, text, HTML and rows.
        self.assertEqual(output, original)

    def test_fallback_response_is_also_identical(self):
        output = {'html': '<table><tr><td>9ข้อความ a</td><td>123</td></tr></table>',
                  'pixels': [[[0, 1, 2]] * 20] * 10}
        with patch.object(table.time, 'perf_counter', return_value=100):
            with patch.object(table, '_collect_dicts', side_effect=reference_collect):
                before = table._reconstruct_with_test_structure_rules(output, np.zeros((2, 2, 3)), 100)
            after = table._reconstruct_with_test_structure_rules(output, np.zeros((2, 2, 3)), 100)
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
