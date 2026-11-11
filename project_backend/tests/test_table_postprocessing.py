import copy
import time
import unittest
from unittest.mock import patch

import numpy as np

from app.model_runtime import table_recognition_v2_adapter as table
from app.processing import extraction_timing


class TablePostprocessingTests(unittest.TestCase):
    def output(self):
        boxes = [[0, 0, 100, 40], [100, 0, 200, 40], [0, 40, 100, 80], [100, 40, 200, 80]]
        return {'table_res_list': [{'cell_box_list': boxes,
            'table_ocr_pred': {'rec_boxes': [[4, 4, 90, 30], [104, 4, 190, 30],
                [4, 44, 90, 70], [104, 44, 190, 70]],
                'rec_texts': ['Name', 'Value', 'Example', '123'], 'rec_scores': [.9] * 4}}],
                'input_img': [[[0, 0, 0], [255, 255, 255]]]}

    def test_dictionary_traversal_preserves_order_and_objects(self):
        child = {'value': 2}
        root = {'pixels': [[0, 1, 2], [3, 4, 5]], 'table': [{'nested': child}], 'none': None}
        self.assertEqual(table._collect_dicts(root), [root, root['table'][0], child])
        self.assertIs(table._collect_dicts(root)[-1], child)

    def test_candidates_can_reuse_scan_without_traversing_again(self):
        output = self.output()
        collected = table._collect_dicts(output)
        expected = table._table_result_candidates(output)
        with patch.object(table, '_collect_dicts', side_effect=AssertionError('duplicate scan')):
            self.assertEqual(table._table_result_candidates(output, collected), expected)

    def test_reconstruction_scans_once_and_preserves_table_structure(self):
        output = self.output()
        original = copy.deepcopy(output)
        image = np.zeros((80, 200, 3), dtype=np.uint8)
        # Count only top-level scans (the walker recursively calls itself).
        scan = table._collect_dicts
        top_calls = []
        def counted(value):
            if value is output:
                top_calls.append(value)
            return scan(value)
        with patch.object(table, '_collect_dicts', side_effect=counted):
            result = table._reconstruct_with_test_structure_rules(output, image, time.perf_counter())
        self.assertEqual(len(top_calls), 1)
        self.assertEqual(result['table_rows'], [['Name', 'Value'], ['Example', '123']])
        self.assertEqual(result['table_structured']['rows'], result['table_rows'])
        self.assertEqual(result['table_selected_method'], 'embedded_table_rules')
        self.assertNotIn('raw_results', result)
        self.assertEqual(output, original)
        for value in result['table_debug']['post_processing_timing'].values():
            self.assertGreaterEqual(value, 0)

    def test_remote_html_fallback_still_works(self):
        output = {'html': '<table><tr><td>Example</td><td>123</td></tr></table>'}
        result = table._reconstruct_with_test_structure_rules(output, np.zeros((2, 2, 3), dtype=np.uint8), time.perf_counter())
        self.assertEqual(result['table_rows'], [['Example', '123']])
        self.assertEqual(result['table_selected_method'], 'remote_default_parser')

    def test_table_phases_accumulate_in_extraction_report(self):
        @extraction_timing.measure_processing
        def process():
            for _ in range(2):
                extraction_timing.call('table_roi', lambda: {
                    'table_debug': {'post_processing_timing': {'structure_rules_ms': 3, 'remote_call_ms': 7}}})
            return {}
        stage = process()['extraction_timing']['stages']['table_roi']
        self.assertEqual(stage['count'], 2)
        self.assertEqual(stage['structure_rules_ms'], 6)
        self.assertEqual(stage['remote_call_ms'], 14)

    def test_nested_recognition_polygons_keep_bbox_text_index_alignment(self):
        polygon = [[104, 4], [190, 4], [190, 30], [104, 30]]
        items = table.extract_recognized_items({'table_ocr_pred': {
            'rec_boxes': [[4, 4, 90, 30], None], 'rec_polys': [None, polygon],
            'rec_texts': ['first', 'second'], 'rec_scores': [.8, .9]}})
        self.assertEqual(table.box_from_values(items[1]['bbox']), [104, 4, 190, 30])
        self.assertEqual(items[1]['text'], 'second')
        self.assertEqual(items[1]['score'], .9)
        self.assertIsNone(table.box_from_values([[0], None, [2], [3]]))

    def test_table_polygons_are_mapped_without_losing_text(self):
        output = self.output()
        ocr = output['table_res_list'][0]['table_ocr_pred']
        boxes = ocr.pop('rec_boxes')
        ocr['rec_polys'] = [[[x, y], [right, y], [right, bottom], [x, bottom]]
                            for x, y, right, bottom in boxes]
        result = table._reconstruct_with_test_structure_rules(output, np.zeros((80, 200, 3), dtype=np.uint8), time.perf_counter())
        self.assertEqual(result['table_rows'], [['Name', 'Value'], ['Example', '123']])
        self.assertEqual(result['table_debug']['ocr_mapping_audit']['unassigned_source_count'], 0)

    def test_literal_ocr_noise_and_real_single_letters_are_preserved_and_traced(self):
        output = self.output()
        texts = ['9เอกสารข้อความ', '99Oทดสอบ', 'นตารางข้อมูลa', 'ข้อความ a 9']
        output['table_res_list'][0]['table_ocr_pred']['rec_texts'] = texts
        result = table._reconstruct_with_test_structure_rules(output, np.zeros((80, 200, 3), dtype=np.uint8), time.perf_counter())
        self.assertEqual(result['table_rows'], [texts[:2], texts[2:]])
        self.assertEqual([item['text'] for item in result['table_debug']['ocr_mapping_audit']['items']], texts)
        self.assertTrue(all(item['assigned_cells'] for item in result['table_debug']['ocr_mapping_audit']['items']))
        self.assertIn('ข้อความ a 9', result['text'])
        self.assertEqual(result['table_structured']['cells'][-1]['text'], texts[-1])

    def test_unmapped_text_is_visible_in_audit_without_deleting_source(self):
        items = [{'text': 'real data', 'bbox': [1, 2, 3, 4], 'score': .8}]
        original = copy.deepcopy(items)
        audit = table._ocr_mapping_audit(items, [])
        self.assertEqual(audit['unassigned_source_count'], 1)
        self.assertEqual(items, original)

    def test_tiny_ocr_fragments_use_real_reported_boxes_even_with_high_confidence(self):
        items = [
            {'text': 'ทดสอบการสกัดข้อความด้วย Text OCR', 'bbox': [486, 65, 817, 84], 'score': .9958549738},
            {'text': '9.', 'bbox': [622, 62, 631, 68], 'score': .9567728043},
            {'text': 'ตารางข้อมูล', 'bbox': [115, 150, 221, 177], 'score': .9961839318},
            {'text': '9', 'bbox': [172, 149, 183, 156], 'score': .3729420304},
            {'text': 'ข้อมูลรวมทั้งหมด', 'bbox': [116, 192, 262, 219], 'score': .9550209045},
            {'text': 'F', 'bbox': [205, 187, 210, 192], 'score': .6981261969},
        ]
        original = copy.deepcopy(items)
        cells = [[475, 53, 910, 98], [107, 140, 349, 185], [107, 185, 349, 229]]
        retained, excluded = table.remove_tiny_overlapping_ocr_fragments(items, cells)
        self.assertEqual([item['text'] for item in retained], [items[i]['text'] for i in (0, 2, 4)])
        self.assertEqual([item['text'] for item in excluded], ['9.', '9', 'F'])
        self.assertTrue(all(item.get('mapping_warning') for item in excluded))
        self.assertEqual(items, original)

    def test_real_single_characters_and_outside_markers_are_preserved(self):
        main = {'text': 'ข้อมูลรวมทั้งหมด', 'bbox': [116, 192, 262, 219], 'score': .955}
        items = [main,
                 {'text': '1', 'bbox': [80, 195, 94, 216], 'score': .99},  # adjacent cell
                 {'text': 'F', 'bbox': [120, 193, 132, 215], 'score': .99},  # normal height
                 {'text': '9.', 'bbox': [265, 187, 273, 192], 'score': .99},  # outside text
                 {'text': 'A', 'bbox': [175, 203, 180, 208], 'score': .99},  # not at upper edge
                 {'text': '๔', 'bbox': [48, 196, 70, 219], 'score': .99}]
        cells = [[107, 185, 263, 229], [48, 185, 106, 229], [264, 185, 350, 229]]
        retained, excluded = table.remove_tiny_overlapping_ocr_fragments(items, cells)
        self.assertEqual(retained, items)
        self.assertEqual(excluded, [])
        low_conf_main = {**main, 'score': .4}
        retained, excluded = table.remove_tiny_overlapping_ocr_fragments(
            [low_conf_main, {'text': 'F', 'bbox': [205, 187, 210, 192], 'score': .7}], cells)
        self.assertEqual(len(retained), 2)
        self.assertEqual(excluded, [])
        retained, excluded = table.remove_tiny_overlapping_ocr_fragments(
            [main, {'text': 'F', 'bbox': [205, 187, 210, 192], 'score': .7}], [])
        self.assertEqual(len(retained), 2)
        self.assertEqual(excluded, [])
        for score in (.95, None):
            retained, excluded = table.remove_tiny_overlapping_ocr_fragments(
                [main, {'text': 'F', 'bbox': [205, 187, 210, 192], 'score': score}], cells)
            self.assertEqual(len(retained), 2)
            self.assertEqual(excluded, [])

    def test_reconstruction_removes_fragment_but_audit_keeps_source(self):
        output = self.output()
        ocr = output['table_res_list'][0]['table_ocr_pred']
        ocr['rec_texts'][2] = 'ข้อมูลรวมทั้งหมด'
        ocr['rec_boxes'].append([45, 40, 50, 44])
        ocr['rec_texts'].append('F')
        ocr['rec_scores'].append(.698)
        result = table._reconstruct_with_test_structure_rules(output, np.zeros((80, 200, 3), dtype=np.uint8), time.perf_counter())
        self.assertEqual(result['table_rows'][1][0], 'ข้อมูลรวมทั้งหมด')
        self.assertEqual(result['table_structured']['cells'][2]['text'], 'ข้อมูลรวมทั้งหมด')
        audit = result['table_debug']['ocr_mapping_audit']
        fragment = next(item for item in audit['items'] if item['text'] == 'F')
        self.assertEqual(fragment['assigned_cells'], [])
        self.assertEqual(fragment['bbox'], [45.0, 40.0, 50.0, 44.0])
        self.assertTrue(any('Ignored tiny OCR fragment' in warning for cell in result['table_structured']['cells']
                            for warning in cell.get('mappingWarnings', [])))

    def test_dictionary_count_is_not_recognition_count(self):
        output = self.output()
        # Root + table + table_ocr_pred + 40 metadata dictionaries = 43.
        output.pop('input_img')
        output['metadata'] = [{} for _ in range(40)]
        boxes, text_boxes, texts = [], [], []
        for row in range(11):
            for col in range(3):
                x, y = col * 100, row * 40
                boxes.append([x, y, x + 100, y + 40])
                text_boxes.append([x + 4, y + 4, x + 90, y + 30])
                texts.append(f'{row}-{col}')
        table_result = output['table_res_list'][0]
        table_result['cell_box_list'] = boxes
        table_result['table_ocr_pred'] = {'rec_boxes': text_boxes, 'rec_texts': texts, 'rec_scores': [.9] * 33}
        result = table._reconstruct_with_test_structure_rules(output, np.zeros((440, 300, 3), dtype=np.uint8), time.perf_counter())
        self.assertEqual(result['table_debug']['raw_result_count'], 43)
        self.assertEqual(result['table_debug']['central_rules']['recognized_item_count'], 33)
        self.assertEqual(result['table_debug']['raw_result_count_kind'], 'nested_dictionary_count')
        self.assertEqual(result['table_debug']['ocr_mapping_audit']['source_ocr_count'], 33)


if __name__ == '__main__':
    unittest.main()
