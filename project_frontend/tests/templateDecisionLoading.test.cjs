const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const source = fs.readFileSync(path.join(__dirname, '../src/user/components/TemplateDecisionLoading.tsx'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const loaded = { exports: {} };
new Function('require', 'exports', 'module', compiled)(require, loaded.exports, loaded);
const { createTemplateDecisionStatus, default: Loading } = loaded.exports;

test('retains progress written before the loading component subscribes', () => {
  const status = createTemplateDecisionStatus();
  status.set('searching');
  assert.equal(status.getSnapshot(), 'searching');
  let updates = 0;
  const unsubscribe = status.subscribe(() => updates++);
  status.set('preparing ROI');
  assert.equal(status.getSnapshot(), 'preparing ROI');
  assert.equal(updates, 1);
  status.set('preparing ROI');
  assert.equal(updates, 1);
  unsubscribe();
  status.set('');
  assert.equal(updates, 1);
  assert.equal(status.getSnapshot(), '');
});

test('workspace instances and unsubscribe/remount cycles remain isolated', () => {
  const first = createTemplateDecisionStatus();
  const second = createTemplateDecisionStatus();
  first.set('first run');
  assert.equal(second.getSnapshot(), '');
  let current;
  const removeFirst = first.subscribe(() => { current = first.getSnapshot(); });
  removeFirst();
  const removeSecond = first.subscribe(() => { current = first.getSnapshot(); });
  first.set('second run');
  assert.equal(current, 'second run');
  removeSecond();
});

test('keeps loading observer marker, title, and fallback message', () => {
  const html = renderToStaticMarkup(React.createElement(Loading, { status: createTemplateDecisionStatus() }));
  assert.ok(html.includes('data-detection-loading="true"'));
  assert.ok(html.includes('กำลังค้นหา Template'));
  assert.ok(html.includes('กำลังประมวลผล...'));
});
