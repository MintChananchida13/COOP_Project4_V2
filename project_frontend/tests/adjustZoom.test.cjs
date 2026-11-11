const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

const file = path.resolve(__dirname, '../src/user/components/AdjustZone.tsx');
const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let declaration;
function find(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'previewPointToImage') declaration = node;
  ts.forEachChild(node, find);
}
find(source);
assert.ok(declaration, 'crop pointer mapping is present');
const code = ts.transpileModule(`const ${declaration.getText(source)}; globalThis.map = previewPointToImage;`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;
const context = {};
vm.runInNewContext(code, context);

test('crop corner maps to the same image coordinates at each preview zoom', () => {
  for (const zoom of [0.5, 1, 1.5, 2, 3]) {
    const rect = { left: 100, top: 50, width: 400 * zoom, height: 300 * zoom };
    const point = context.map(100 + 120 * zoom, 50 + 240 * zoom, rect, 400, 300);
    assert.equal(point.x, 120);
    assert.equal(point.y, 240);
  }
});

test('crop corner stays inside the unscaled image when pointer moves beyond it', () => {
  const rect = { left: 100, top: 50, width: 800, height: 600 };
  const point = context.map(50, 900, rect, 400, 300);
  assert.equal(point.x, 0);
  assert.equal(point.y, 300);
});
