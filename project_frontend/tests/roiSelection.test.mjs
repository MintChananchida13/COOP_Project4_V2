import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';

const source = fs.readFileSync(new URL('../src/shared/workspace/roiSelection.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const loaded = { exports: {} };
new Function('exports', 'module', compiled)(loaded.exports, loaded);
const { canvasPoint, selectionRect, intersectsSelection, groupTranslation, translateRoi } = loaded.exports;

test('pointer coordinates account for zoom and scrolled image positions', () => {
  for (const zoom of [0.5, 1, 2, 3]) {
    const rect = { x: -100, y: -200, width: 800 * zoom, height: 1000 * zoom };
    assert.deepEqual(canvasPoint(-100 + 50 * zoom, -200 + 80 * zoom, rect, 800, 1000), { x: 50, y: 80 });
    assert.deepEqual(canvasPoint(-1000, -1000, rect, 800, 1000), { x: 0, y: 0 });
  }
});

test('marquee supports reverse dragging and partially covered ROI boxes', () => {
  const rect = selectionRect({ x: 100, y: 80 }, { x: 20, y: 10 });
  assert.deepEqual(rect, { x: 20, y: 10, width: 80, height: 70 });
  assert.equal(intersectsSelection({ x: 90, y: 70, width: 50, height: 40 }, rect), true);
  assert.equal(intersectsSelection({ x: 110, y: 90, width: 20, height: 20 }, rect), false);
});

test('all boxes stop together at image boundaries without changing relative spacing', () => {
  const rois = [{ id: 1, x: 10, y: 20, width: 30, height: 40 }, { id: 2, x: 100, y: 80, width: 40, height: 50 }];
  for (const [requested, expected] of [
    [{ x: 1000, y: 1000 }, { x: 60, y: 70 }],
    [{ x: -1000, y: -1000 }, { x: -10, y: -20 }],
    [{ x: 10, y: -5 }, { x: 10, y: -5 }],
  ]) {
    const delta = groupTranslation(rois, requested.x, requested.y, 200, 200);
    assert.deepEqual(delta, expected);
    const moved = rois.map(roi => translateRoi(roi, delta));
    assert.equal(moved[1].x - moved[0].x, 90);
    assert.equal(moved[1].y - moved[0].y, 60);
    assert.deepEqual(moved.map(roi => [roi.width, roi.height]), [[30, 40], [40, 50]]);
  }
});

test('polygon points follow their box and metadata remains unchanged', () => {
  const roi = { id: 1, x: 10, y: 20, width: 30, height: 40, pageIndex: 2,
    fieldName: 'Name', type: 'text', points: [{ x: 10, y: 20 }, { x: 40, y: 60 }] };
  const moved = translateRoi(roi, { x: 5, y: -10 });
  assert.deepEqual(moved.points, [{ x: 15, y: 10 }, { x: 45, y: 50 }]);
  assert.equal(moved.pageIndex, 2);
  assert.equal(moved.fieldName, 'Name');
  assert.equal(moved.type, 'text');
  assert.equal(roi.x, 10);
  assert.deepEqual(roi.points, [{ x: 10, y: 20 }, { x: 40, y: 60 }]);
});
