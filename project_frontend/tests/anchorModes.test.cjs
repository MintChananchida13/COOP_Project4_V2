const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

function variables(relative, names) {
  const file = path.resolve(__dirname, '../src/admin', relative);
  const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
  const declarations = source.statements.filter(ts.isVariableStatement)
    .flatMap(statement => [...statement.declarationList.declarations])
    .filter(declaration => names.includes(declaration.name.getText(source)))
    .map(declaration => `const ${declaration.getText(source)};`).join('\n');
  const context = {};
  vm.runInNewContext(ts.transpileModule(`${declarations}\nglobalThis.result = {${names.join(',')}};`,
    {compilerOptions: {target: ts.ScriptTarget.ES2020}}).outputText, context);
  return context.result;
}

test('settings expose only standard and strict', () => {
  const { verificationStrategyOptions } = variables('AdminSettingsPage.tsx', ['verificationStrategyOptions']);
  assert.deepEqual(Array.from(verificationStrategyOptions, option => option.value), ['standard', 'strict']);
});

test('legacy API modes normalize to standard', () => {
  const { mapVerificationStrategy } = variables('adminApi.ts', ['mapVerificationStrategy']);
  assert.equal(mapVerificationStrategy('strict'), 'strict');
  for (const value of ['standard', 'layout_only', 'text_only', 'text_image', 'image_only', null]) {
    assert.equal(mapVerificationStrategy(value), 'standard');
  }
});

for (const file of ['AdminTemplateEditPage.tsx', 'AdminTemplateTestPage.tsx']) {
  test(`${file} uses only Text/Image weights`, () => {
    const { calculateMatchingWeights } = variables(file,
      ['clampUnit', 'roundWeight', 'MIN_DUAL_ANCHOR_WEIGHT', 'calculateMatchingWeights']);
    for (const counts of [[1, 1], [1, 0], [0, 1]]) {
      const result = calculateMatchingWeights({layoutWeight: .5,
        textAnchorCount: counts[0], imageAnchorCount: counts[1], preferredTextWeight: .6});
      assert.equal(result.layoutWeight, 0);
      assert.equal(result.textAnchorWeight + result.imageAnchorWeight, 1);
    }
  });
}
