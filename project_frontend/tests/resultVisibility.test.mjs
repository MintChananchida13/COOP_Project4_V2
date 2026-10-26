import assert from 'node:assert/strict';
import test from 'node:test';
import { observeResultVisibility, visibilityMetrics, elementReady } from '../src/shared/resultVisibility.ts';
import fs from 'node:fs';

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
};
const flush = async () => { for (let count = 0; count < 8; count++) await Promise.resolve(); };

function harness(overrides = {}) {
  let clock = 100;
  let sequence = 0;
  const frames = new Map();
  const timers = new Map();
  const state = { current: true, domReady: true, overlay: false, previewRequired: false, image: null, ...overrides };
  const controller = new AbortController();
  let finished = false;
  const promise = observeResultVisibility(() => state, { signal: controller.signal, scheduler: {
    now: () => clock,
    frame: callback => { frames.set(++sequence, callback); return sequence; },
    cancelFrame: handle => frames.delete(handle),
    timeout: callback => { timers.set(++sequence, callback); return sequence; },
    cancelTimeout: handle => timers.delete(handle),
  } }).then(result => { finished = true; return result; });
  return { state, promise, controller, frames, timers, get finished() { return finished; },
    step() { clock += 16; const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback()); },
    expire() { clock += 30000; [...timers.values()].forEach(callback => callback()); },
  };
}

test('commit and visible DOM do not imply readiness while required image is loading', async () => {
  const decode = deferred();
  let calls = 0;
  const image = { complete: false, naturalWidth: 0, src: 'current.png', decode: () => { calls++; return decode.promise; } };
  const run = harness({ previewRequired: true, image });
  run.step(); run.step(); await flush();
  assert.equal(run.finished, false);
  image.complete = true; image.naturalWidth = 50;
  run.step(); await flush();
  assert.equal(run.finished, false);
  decode.resolve(); await flush();
  run.step(); run.step();
  const result = await run.promise;
  assert.equal(result.status, 'READY');
  assert.equal(calls, 1);
  assert.ok(result.readyAt >= result.previewReadyAt);
  assert.ok(result.uiReadyAt < result.previewReadyAt);
  assert.equal(run.frames.size, 0); assert.equal(run.timers.size, 0);
});

test('PDF rendering promise must complete before readiness', async () => {
  const render = deferred();
  const run = harness({ previewRequired: true, pdfRender: render.promise });
  run.step(); run.step(); await flush();
  assert.equal(run.finished, false);
  render.resolve(); await flush(); run.step(); run.step();
  assert.equal((await run.promise).status, 'READY');
  const upload = fs.readFileSync(new URL('../src/user/components/UploadZone.tsx', import.meta.url), 'utf8');
  assert.match(upload, /await page\.render\([\s\S]*?\)\.promise/);
});

test('overlay blocks readiness even when preview decode succeeds', async () => {
  const run = harness({ overlay: true, previewRequired: true,
    image: { complete: true, naturalWidth: 10, src: 'ready.png', decode: async () => {} } });
  run.step(); await flush(); run.step(); run.step(); await flush();
  assert.equal(run.finished, false);
  run.state.overlay = false; run.step(); run.step();
  assert.equal((await run.promise).status, 'READY');
});

test('no required preview does not wait for a missing image or invent zero duration', async () => {
  const run = harness(); run.step(); run.step();
  const result = await run.promise;
  assert.equal(result.status, 'READY');
  assert.equal(result.previewReadyAt, null);
  assert.equal(visibilityMetrics(0, 90, 50, result).frontend_preview_ready_ms, null);
});

test('broken image and failed decode are preview errors, not detection failures', async () => {
  for (const image of [
    { complete: true, naturalWidth: 0, src: 'broken.png' },
    { complete: true, naturalWidth: 10, src: 'broken.png', decode: async () => { throw new Error('decode failed'); } },
  ]) {
    const run = harness({ previewRequired: true, image }); run.step(); await flush();
    const result = await run.promise;
    assert.equal(result.status, 'PREVIEW_ERROR');
    assert.equal(visibilityMetrics(0, 90, 50, result).end_to_end_ms, null);
  }
});

test('changing run cancels observer and late decode cannot finish the next run', async () => {
  const decode = deferred();
  const run = harness({ previewRequired: true,
    image: { complete: true, naturalWidth: 10, src: 'old.png', decode: () => decode.promise } });
  run.step(); await flush(); run.state.current = false; run.step();
  assert.equal((await run.promise).status, 'RUN_CANCELLED');
  decode.resolve(); await flush();
  assert.equal(run.frames.size, 0);
});

test('abort/unmount and background-tab timeout do not depend on animation frames firing', async () => {
  const cancelled = harness(); cancelled.controller.abort();
  assert.equal((await cancelled.promise).status, 'RUN_CANCELLED');
  const timeout = harness({ domReady: false }); timeout.expire();
  const result = await timeout.promise;
  assert.equal(result.status, 'VISIBILITY_TIMEOUT');
  assert.equal(result.readyAt, null);
  assert.equal(timeout.frames.size, 0); assert.equal(timeout.timers.size, 0);
});

test('replaced image cannot use the old decode result', async () => {
  const old = deferred(); const current = deferred();
  const run = harness({ previewRequired: true, image: { complete: true, naturalWidth: 10, src: 'old', decode: () => old.promise } });
  run.step(); await flush();
  run.state.image = { complete: true, naturalWidth: 10, src: 'new', decode: () => current.promise };
  run.step(); await flush(); old.resolve(); await flush(); run.step(); run.step();
  assert.equal(run.finished, false);
  current.resolve(); await flush(); run.step(); run.step();
  assert.equal((await run.promise).status, 'READY');
});

test('metrics are observed intervals, not additive stages or actual paint', () => {
  const values = visibilityMetrics(0, 100, 40, { status: 'READY', readyAt: 180, previewReadyAt: 140,
    uiReadyAt: 120, previewRequired: true, reason: null });
  assert.equal(values.frontend_render_ms, 80);
  assert.equal(values.frontend_result_visible_ms, 140);
  assert.equal(values.frontend_preview_ready_ms, 100);
  assert.equal(values.frontend_ui_ready_ms, 80);
  assert.equal(values.frontend_commit_end_to_end_ms, 100);
  assert.equal(values.end_to_end_ms, 180);
});

test('DOM readiness rejects hidden, offscreen and background results', () => {
  const saved = { document: globalThis.document, window: globalThis.window, getComputedStyle: globalThis.getComputedStyle };
  try {
    globalThis.document = { visibilityState: 'visible' };
    globalThis.window = { innerWidth: 1000, innerHeight: 800 };
    globalThis.getComputedStyle = () => ({ display: 'block', visibility: 'visible', opacity: '1' });
    const element = { isConnected: true, hidden: false, getAttribute: () => null, parentElement: null,
      getBoundingClientRect: () => ({ width: 100, height: 100, top: 0, left: 0, bottom: 100, right: 100 }) };
    assert.equal(elementReady(element), true);
    element.hidden = true; assert.equal(elementReady(element), false); element.hidden = false;
    globalThis.document.visibilityState = 'hidden'; assert.equal(elementReady(element), false);
    globalThis.document.visibilityState = 'visible';
    element.getBoundingClientRect = () => ({ width: 100, height: 100, top: 900, left: 0, bottom: 1000, right: 100 });
    assert.equal(elementReady(element), false);
  } finally {
    Object.assign(globalThis, saved);
  }
});
