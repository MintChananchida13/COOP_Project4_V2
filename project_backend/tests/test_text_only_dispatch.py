import ast
import time
import unittest
from pathlib import Path


class TextOnlyDispatchTests(unittest.TestCase):
    def test_non_global_dispatch_does_not_load_unused_anchors(self):
        path = Path(__file__).resolve().parents[1] / 'app/processing/detection_service.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_detect_template_text_only')
        # Only the dispatch boundary is executed; DB/model calls are stubs.
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), function], type_ignores=[])
        calls = []
        def load(*args):
            self.fail('unused text anchors were loaded before dispatch')
        def delegate(*args):
            calls.append(args)
            return {'delegated': True}
        namespace = {'time': time, 'DETECTION_BATCH_MODE_GLOBAL': 'global',
                     'VERIFICATION_STRATEGY_TEXT_ONLY': 'text_only',
                     '_load_text_only_template_anchors': load,
                     '_detect_template_anchor_strategy': delegate}
        exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
        for mode in ['per_template', 'sequential']:
            result = namespace['_detect_template_text_only']('query', [], {}, None, 0, 'image', mode)
            self.assertEqual(result, {'delegated': True})
            self.assertEqual(calls[-1][-2:], ('text_only', mode))


if __name__ == '__main__':
    unittest.main()
