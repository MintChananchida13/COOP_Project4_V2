const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const compilerOptions = { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 };
const helper = { exports: {} };
const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/shared/detectionCandidateResolver.ts'), 'utf8'), { compilerOptions }).outputText;
new Function('exports', 'module', code)(helper.exports, helper);
const { createDetectionCandidateResolver } = helper.exports;

test('maps pooled candidates once while retaining distinct page results', () => {
  let maps = 0;
  const resolve = createDetectionCandidateResolver([{ template_id: 'card', score: .9 }, { template_id: 'card', score: .8 }], value => { maps++; return { ...value }; });
  const first = resolve({ candidate_ref: 0 });
  assert.equal(first, resolve({ candidate_ref: 0 }));
  assert.notEqual(first, resolve({ candidate_ref: 1 }));
  assert.equal(maps, 2);
});

test('supports legacy Admin payloads and rejects invalid references', () => {
  const resolve = createDetectionCandidateResolver([], value => value);
  const original = { template_id: 'card' };
  assert.equal(resolve(original), original);
  for (const value of [-1, 0, .5, '0']) assert.throws(() => resolve({ candidate_ref: value }), /Invalid detection/);
});

// Use the actual mapping functions, without executing module-level API setup.
const apiSource = fs.readFileSync(path.join(__dirname, '../src/admin/adminApi.ts'), 'utf8');
const tree = ts.createSourceFile('adminApi.ts', apiSource, ts.ScriptTarget.Latest, true);
const functions = tree.statements.filter(ts.isFunctionDeclaration).map(node => node.getText(tree));
const mapperCode = ts.transpileModule(functions.join('\n') + '\nexports.map = mapDetectionDevResult;', { compilerOptions }).outputText;
const mapped = {};
new Function('exports', 'createDetectionCandidateResolver', mapperCode)(mapped, createDetectionCandidateResolver);

test('full mapper preserves UI/ROI/verification/log data for pooled and legacy responses', () => {
  const candidate = { template_id: 'card', template_name: 'Card', final_score: .9, final_passed: true,
    roi_coordinate_space: 'processing', query_page_index: 1,
    metadata: { version_name: 'v1' }, verification: { score: .8, checked_fields: [{ field_id: 'name', passed: true }] },
    processing_pages: [{ pageNumber: 1, processingImageReference: 'page.png' }],
    template_rois: [{ field_id: 'name', roi: { x_ratio: .1, y_ratio: .2, width_ratio: .3, height_ratio: .4 } }],
    projected_fields: [] };
  const pageCandidate = { ...candidate, final_score: .85, query_page_index: 2 };
  const page = { page_index: 2, matched: true, best_candidate: pageCandidate, candidates: [pageCandidate],
    processing_template_id: 'card', processing_image_preview_url: 'data:image/png;base64,final' };
  const legacy = { matched: true, best_candidate: candidate, candidates: [candidate], pages: [page], debug: { timing: { total_runtime_ms: 123 } } };
  const pooled = { ...legacy, candidate_pool: [candidate, pageCandidate], best_candidate: { candidate_ref: 0 },
    candidates: [{ candidate_ref: 0 }], pages: [{ ...page, best_candidate: { candidate_ref: 1 }, candidates: [{ candidate_ref: 1 }] }] };
  const actual = mapped.map(pooled);
  assert.deepEqual(actual, mapped.map(legacy));
  assert.equal(actual.bestCandidate, actual.candidates[0]);
  assert.equal(actual.pages[0].bestCandidate, actual.pages[0].candidates[0]);
  assert.notEqual(actual.bestCandidate, actual.pages[0].bestCandidate);
});

test('empty and unmatched compact responses retain their status and message', () => {
  const value = mapped.map({ matched: false, best_candidate: null, candidates: [], pages: [], candidate_pool: [], message: 'No template' });
  assert.equal(value.matched, false);
  assert.equal(value.bestCandidate, null);
  assert.equal(value.message, 'No template');
});
