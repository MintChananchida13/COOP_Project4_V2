const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/shared/imageCropCache.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const loaded = { exports: {} };
new Function('exports', 'module', compiled)(loaded.exports, loaded);
const { createImageCropCache, roiCropKey } = loaded.exports;

test('shares concurrent image loads and crops across ROI previews', async () => {
  let loads = 0;
  let renders = 0;
  const cache = createImageCropCache(async src => { loads++; return { src }; });
  const render = image => { renders++; return `crop:${image.src}`; };
  const result = await Promise.all([
    cache.getCrop('page1', 'roi1', render),
    cache.getCrop('page1', 'roi1', render),
    cache.getCrop('page1', 'roi2', render),
  ]);
  assert.deepEqual(result, ['crop:page1', 'crop:page1', 'crop:page1']);
  assert.equal(loads, 1);
  assert.equal(renders, 2);
  await cache.getCrop('page1', 'roi1', render);
  assert.equal(renders, 2);
});

test('separates pages and invalidates crops when geometry or polygon changes', async () => {
  let loads = 0;
  let renders = 0;
  const cache = createImageCropCache(async src => { loads++; return { src }; });
  const roi = { x: 1, y: 2, width: 30, height: 40 };
  const render = () => String(++renders);
  const first = await cache.getCrop('page1', roiCropKey(roi), render);
  assert.equal(await cache.getCrop('page1', roiCropKey({ ...roi, fieldName: 'renamed' }), render), first);
  await cache.getCrop('page1', roiCropKey({ ...roi, x: 2 }), render);
  await cache.getCrop('page1', roiCropKey({ ...roi, points: [{ x: 1, y: 2 }] }), render);
  await cache.getCrop('page2', roiCropKey(roi), render);
  assert.equal(loads, 2);
  assert.equal(renders, 4);
});

test('failed loads and failed or empty crops can be retried', async () => {
  let loads = 0;
  const cache = createImageCropCache(async () => {
    if (++loads === 1) throw new Error('load failed');
    return {};
  });
  await assert.rejects(cache.getCrop('page', 'roi', () => 'crop'), /load failed/);
  assert.equal(await cache.getCrop('page', 'roi', () => 'crop'), 'crop');
  await assert.rejects(cache.getCrop('page', 'bad', () => { throw new Error('canvas failed'); }), /canvas failed/);
  assert.equal(await cache.getCrop('page', 'bad', () => 'retry'), 'retry');
  assert.equal(await cache.getCrop('page', 'empty', () => null), null);
  assert.equal(await cache.getCrop('page', 'empty', () => 'retry'), 'retry');
});

test('evicts retained images and crops and isolates workspace instances', async () => {
  let loads = 0;
  let renders = 0;
  const load = async src => { loads++; return { src }; };
  const render = () => String(++renders);
  const cache = createImageCropCache(load, 1, 1);
  await cache.getCrop('page1', 'roi1', render);
  await cache.getCrop('page1', 'roi2', render);
  await cache.getCrop('page1', 'roi1', render);
  assert.equal(renders, 3);
  await cache.getImage('page2');
  await cache.getImage('page1');
  assert.equal(loads, 3);
  await createImageCropCache(load).getImage('page1');
  assert.equal(loads, 4);
});
