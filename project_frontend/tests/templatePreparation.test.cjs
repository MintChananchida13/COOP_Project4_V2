const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/app/page.tsx'), 'utf8');
const tree = ts.createSourceFile('page.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let handlerSource;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'handleBatchConfirm') {
    handlerSource = node.initializer.getText(tree);
  }
  ts.forEachChild(node, visit);
}
visit(tree);
const code = ts.transpileModule(`exports.handler = ${handlerSource};`, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
}).outputText;

function setup({ matched = true, fail = false, maintenance = false, emptyRois = false, mainPage = false } = {}) {
  const updates = [];
  const loaders = [];
  const env = {
    window: {}, currentIndex: 0, uploadedSourceFileType: 'image', uploadedSourceFile: null,
    visibilityAbortRef: { current: null }, detectionTimingRunRef: { current: 0 },
    pendingDetectionCommitRef: { current: null }, activeProcessingRunIdRef: { current: '' },
    ocrProcessingTimeMsRef: { current: null }, originalProcessingLogOcrResultsRef: { current: [] },
    loadImageElement: async src => ({ src }),
    createImageCropCache: load => ({ getImage: load }),
    dataUrlToFile: async src => ({ src }),
    updateOcrResults: value => updates.push(['ocr', value]),
    detectTemplateDev: async () => ({ matched, bestCandidate: matched ? { templateId: 'card' } : null,
      pages: [], candidates: [], mainPageAutoRoiPages: mainPage ? [{ page_number: 2, image_preview_data_url: 'auto' }] : [] }),
    buildTemplateDetectionSnapshot: () => ({}), devTemplateFlowLog: () => {},
    detectionFailureDetail: () => '', detectionVerificationStrategy: () => 'standard',
    fetchTemplateBundle: async () => {
      assert.equal(updates.filter(([key]) => key === 'setImagesList').length, 0);
      if (fail) throw new Error('test failure');
      return { template: { id: 'card', name: 'Card', detectionMode: mainPage ? 'main_page' : 'all_pages' }, fields: [], pages: [] };
    },
    buildTemplateCanvasImages: async () => mainPage ? ['processed', 'original2'] : ['processed'],
    imageUrlToCanvasSafeSrc: async src => src,
    buildDetectionAutoRois: async (...args) => { loaders.push(args[3]); return []; },
    buildWholePageAutoRois: async (...args) => { loaders.push(args[3]); return [{ id: 2, pageIndex: 1 }]; },
    templateFieldsToWorkspaceRois: async (...args) => {
      loaders.push(args[5]);
      assert.equal(updates.filter(([key]) => key === 'setImagesList').length, 0);
      return emptyRois ? [] : [{ id: 1, x: 10, y: 20, width: 30, height: 40 }];
    },
    buildFlexibleResolvedDisplayRois: async (...args) => { loaders.push(args[2]); return []; },
    handleSystemMaintenanceError: async () => maintenance,
    contextualTemplateError: (message, error) => new Error(`${message}: ${error.message}`),
    console: { warn() {}, table() {}, info() {} },
  };
  for (const name of new Set(handlerSource.match(/\bset[A-Z]\w+/g))) {
    env[name] = value => updates.push([name, value]);
  }
  const exports = {};
  new Function('env', 'exports', `with(env) { ${code} }`)(env, exports);
  return { run: exports.handler, updates, loaders };
}

test('matched flow commits final images once, after ROI preparation', async () => {
  const { run, updates, loaders } = setup();
  await run(['original']);
  assert.deepEqual(updates.filter(([key]) => key === 'setImagesList'), [['setImagesList', ['processed']]]);
  assert.deepEqual(updates.filter(([key]) => key === 'setRois').at(-1)[1], [{ id: 1, x: 10, y: 20, width: 30, height: 40 }]);
  assert.equal(typeof loaders[0], 'function');
  assert.equal(loaders[0], loaders[1]);
  assert.deepEqual(updates.filter(([key]) => key === 'setIsTemplateDecisionOpen').at(-1), ['setIsTemplateDecisionOpen', false]);
});

test('no match and failed detection preserve original images for Custom OCR', async () => {
  for (const options of [{ matched: false }, { fail: true }]) {
    const { run, updates } = setup(options);
    await run(['original']);
    assert.deepEqual(updates.filter(([key]) => key === 'setImagesList'), [['setImagesList', ['original']]]);
    assert.equal(updates.filter(([key]) => key === 'setIsTemplateDecisionOpen').at(-1)[1], false);
  }
});

test('empty ROI and missing input paths still finish loading', async () => {
  for (const images of [['original'], []]) {
    const { run, updates } = setup({ emptyRois: true });
    await run(images);
    assert.equal(updates.filter(([key]) => key === 'setImagesList').length, 1);
    assert.equal(updates.filter(([key]) => key === 'setIsTemplateDecisionOpen').at(-1)[1], false);
  }
});

test('maintenance reset is not overwritten by a late image commit', async () => {
  const { run, updates } = setup({ fail: true, maintenance: true });
  await run(['original']);
  assert.equal(updates.filter(([key]) => key === 'setImagesList').length, 0);
});

test('main-page auto ROI and fallback keep the final page images and shared loader', async () => {
  const { run, updates, loaders } = setup({ mainPage: true });
  await run(['original1', 'original2']);
  assert.deepEqual(updates.filter(([key]) => key === 'setImagesList'), [['setImagesList', ['processed', 'auto']]]);
  assert.equal(loaders.length, 4);
  assert.ok(loaders.every(load => load === loaders[0]));
  assert.equal(updates.filter(([key]) => key === 'setRois').at(-1)[1].length, 2);
});
