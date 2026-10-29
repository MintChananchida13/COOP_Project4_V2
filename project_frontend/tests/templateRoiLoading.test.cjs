const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/app/page.tsx'), 'utf8');
const tree = ts.createSourceFile('page.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = ['templateFieldsToWorkspaceRois', 'buildDetectionAutoRois', 'compareTemplateFieldsForWorkspace',
  'getWorkspaceRoiType', 'getWorkspaceExtractionMethod', 'roiFromLayoutBlock', 'isCountableWorkspaceField', 'stableNumericId'];
const declarations = tree.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name.text));
const compiled = ts.transpileModule(declarations.map(node => node.getText(tree)).join('\n') +
  '\nexports.mapFields = templateFieldsToWorkspaceRois; exports.mapAuto = buildDetectionAutoRois;', {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const api = {};
new Function('exports', 'normalizeResolvedBlockType', compiled)(api, () => 'text');
const field = { id: 'field1', fieldName: 'Name', dataType: 'text', roi: {
  pageNumber: 1, xRatio: 0.1, yRatio: 0.2, widthRatio: 0.3, heightRatio: 0.4,
} };
const image = { naturalWidth: 1000, naturalHeight: 2000 };

test('fixed fields load only referenced pages and keep the same coordinates', async () => {
  const calls = [];
  const result = await api.mapFields([field], ['page1', 'page2', 'page3'], null, 'card', undefined,
    async src => { calls.push(src); return image; });
  assert.deepEqual(calls, ['page1']);
  assert.deepEqual([result[0].x, result[0].y, result[0].width, result[0].height], [75, 300, 225, 600]);
});

test('no fields and verification-only fields do not load any images', async () => {
  for (const fields of [[], [{ ...field, useForVerification: true }]]) {
    let loads = 0;
    assert.deepEqual(await api.mapFields(fields, ['page1', 'page2'], null, 'card', undefined,
      async () => { loads++; return image; }), []);
    assert.equal(loads, 0);
  }
});

test('main-page remapping and image-load errors preserve coordinate fallback', async () => {
  const calls = [];
  const mapped = await api.mapFields([field], ['page1', 'page2'], null, 'card', { templatePageToImageIndex: { 1: 1 } },
    async src => { calls.push(src); return image; });
  assert.deepEqual(calls, ['page2']);
  assert.equal(mapped[0].pageIndex, 1);
  const failed = await api.mapFields([field], ['page1'], null, 'card', undefined, async () => { throw new Error('failed'); });
  assert.equal(failed[0].y, 200);
});

test('auto ROI skips empty pages and loads only pages with regions', async () => {
  const calls = [];
  const loader = async src => { calls.push(src); return image; };
  assert.deepEqual(await api.mapAuto(['page1', 'page2'], [], [], loader), []);
  assert.deepEqual(await api.mapAuto(['page1', 'page2'], [], [{ page_number: 1, regions: [] }], loader), []);
  const result = await api.mapAuto(['page1', 'page2'], [], [{ page_number: 2,
    regions: [{ type: 'text', roi: { x_ratio: 0.1, y_ratio: 0.2, width_ratio: 0.3, height_ratio: 0.4 } }] }], loader);
  assert.deepEqual(calls, ['page2']);
  assert.equal(result[0].pageIndex, 1);
  assert.equal(result[0].y, 300);
});
