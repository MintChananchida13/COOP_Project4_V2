const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');

test('real polling function measures only terminal response JSON and synchronous mapping', async () => {
  const text = fs.readFileSync(path.join(root, 'src/admin/adminApi.ts'), 'utf8');
  const source = ts.createSourceFile('adminApi.ts', text, ts.ScriptTarget.Latest, true);
  const declaration = source.statements.find(statement => ts.isVariableStatement(statement) &&
    statement.declarationList.declarations.some(item => item.name.getText(source) === 'detectTemplateDev'));
  const code = ts.transpileModule(declaration.getText(source).replace('export const', 'const') + '\nglobalThis.detect = detectTemplateDev;',
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  let clock = 0;
  let mapped = 0;
  const responses = [
    { data: { job_id: 'current', status: 'processing' } },
    { data: { job_id: 'current', status: 'processing' } },
    { data: { job_id: 'current', status: 'completed', result: { matched: false } } },
  ];
  const context = vm.createContext({ URLSearchParams, FormData: class { append() {} },
    performance: { now: () => clock++ }, ADMIN_API_BASE_URL: 'http://backend',
    fetchWithAuth: async () => ({ ok: true, json: async () => { clock += 5; return responses.shift(); } }),
    abortableDelay: async () => { clock += 1000; }, asRecord: value => value,
    mapDetectionDevResult: value => { mapped++; clock += 3; return value; } });
  vm.runInContext(code, context);
  const timing = {};
  const result = await context.detect({ name: 'input.pdf' }, { timing });
  assert.equal(result.matched, false);
  assert.equal(mapped, 1);
  assert.equal(timing.frontend_response_json_ms, 6);
  assert.equal(timing.frontend_response_processing_ms, 4);
  assert.ok(timing.frontendResponseMappedAt > timing.frontendResponseHeadersAt);
  assert.ok(timing.jobPollingMs > timing.frontend_response_json_ms);
});

test('layout checkpoint consumes completion once and defers logging without claiming paint', () => {
  const text = fs.readFileSync(path.join(root, 'src/app/page.tsx'), 'utf8');
  const source = ts.createSourceFile('page.tsx', text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let callback;
  const visit = node => {
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'useLayoutEffect' &&
      node.arguments[0].getText(source).includes('pendingDetectionCommitRef')) callback = node.arguments[0];
    ts.forEachChild(node, visit);
  };
  visit(source);
  assert.ok(callback);
  const microtasks = [];
  const emitted = [];
  const context = vm.createContext({ isTemplateDecisionOpen: true,
    pendingDetectionCommitRef: { current: timestamp => emitted.push(timestamp) },
    performance: { now: () => 315 }, queueMicrotask: operation => microtasks.push(operation) });
  const code = ts.transpileModule(`globalThis.checkpoint = ${callback.getText(source)};`,
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInContext(code, context);
  context.checkpoint();
  assert.equal(microtasks.length, 0);
  context.isTemplateDecisionOpen = false;
  context.checkpoint();
  context.checkpoint();
  assert.equal(microtasks.length, 1);
  assert.equal(emitted.length, 0);
  microtasks[0]();
  assert.deepEqual(emitted, [315]);
});
