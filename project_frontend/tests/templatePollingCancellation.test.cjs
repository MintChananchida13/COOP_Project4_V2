const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function compile(source, name, env) {
  const tree = ts.createSourceFile('source.ts', source, ts.ScriptTarget.Latest, true);
  let expression;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === name) expression = node.initializer.getText(tree);
    if (ts.isFunctionDeclaration(node) && node.name.text === name) expression = node.getText(tree).replace('export ', '');
    ts.forEachChild(node, visit);
  }
  visit(tree);
  const assignment = expression.startsWith('function ') ? `${expression}; exports.fn = ${name}` : `exports.fn = ${expression}`;
  const code = ts.transpileModule(assignment, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } }).outputText;
  const exported = {};
  new Function('env', 'exports', `with(env) { ${code} }`)(env, exported);
  return exported.fn;
}
const delaySource = fs.readFileSync(path.join(__dirname, '../src/shared/abortableDelay.ts'), 'utf8');
const delay = compile(delaySource, 'abortableDelay', {});
const source = fs.readFileSync(path.join(__dirname, '../src/admin/adminApi.ts'), 'utf8');
const file = new Blob(['image']); file.name = 'page.png';
const ack = () => ({ ok: true, json: async () => ({ data: { job_id: 'job' } }) });
function detector(fetch, wait = delay) {
  return compile(source, 'detectTemplateDev', {
    fetchWithAuth: fetch, abortableDelay: wait, ADMIN_API_BASE_URL: 'local',
    asRecord: value => value, mapDetectionDevResult: value => value,
  });
}

test('abort during polling delay prevents further GET requests', async () => {
  const controller = new AbortController();
  let calls = 0;
  const detect = detector(async (url, options) => {
    assert.equal(options.signal, controller.signal); calls++; return ack();
  });
  const pending = detect(file, { signal: controller.signal });
  await new Promise(resolve => setImmediate(resolve));
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(calls, 1);
});

test('abort propagates to an in-flight polling request', async () => {
  const controller = new AbortController();
  let started;
  const entered = new Promise(resolve => { started = resolve; });
  const detect = detector(async (url, options) => {
    if (!url.includes('/jobs/')) return ack();
    assert.equal(options.signal, controller.signal);
    started();
    return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true }));
  }, async (ms, signal) => { assert.equal(ms, 1000); signal?.throwIfAborted(); });
  const pending = detect(file, { signal: controller.signal });
  await entered; controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
});

test('completed result is unchanged for callers without a cancellation signal', async () => {
  const detect = detector(async url => url.includes('/jobs/')
    ? { ok: true, json: async () => ({ data: { status: 'completed', result: { matched: true } } }) }
    : ack(), async ms => { assert.equal(ms, 1000); });
  assert.deepEqual(await detect(file), { matched: true });
});

test('an already cancelled request never uploads', async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(detector(() => { assert.fail('upload should not start'); })(file, { signal: controller.signal }), { name: 'AbortError' });
});
