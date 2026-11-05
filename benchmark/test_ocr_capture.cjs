const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, 'ocr_flow.py'), 'utf8');
const capture = source.match(/CAPTURE = r"""([\s\S]*?)"""/)[1];

function setup() {
  const bodies = [];
  const window = {fetch: async () => new Response(JSON.stringify(bodies.shift()), {
    headers: {'Content-Type': 'application/json'},
  })};
  const console = {info() {}};
  vm.runInNewContext(capture, {window, console, URL, location: {href: 'http://localhost:3000/'}});
  const state = () => ({token: 'run', pending: 0, posts: 0, jobs: {}, client: [], errors: []});
  window.__ocrOnlyRun = state();
  return {window, console, bodies, state};
}

async function drain(state) {
  for (let tries = 0; tries < 100 && state.pending; tries++) {
    await new Promise(resolve => setTimeout(resolve, 1));
  }
  assert.equal(state.pending, 0);
}

test('captures OCR only, matches jobs and excludes OCR content', async () => {
  const {window, console, bodies} = setup();
  const state = window.__ocrOnlyRun;
  bodies.push({data: {job_id: 'search'}});
  await window.fetch('http://localhost:8000/api/templates/detect-dev', {method: 'POST'});
  assert.equal(state.posts, 0);
  bodies.push({success: true, job_id: 'ocr', job_create_ms: 0});
  await window.fetch('http://localhost:8000/api/ai/process', {method: 'POST', body: JSON.stringify({
    image: 'PRIVATE IMAGE', rois: [{type: 'text'}, {type: 'table'}, {type: 'image'}, {roiMode: 'flexible'}],
  })});
  await drain(state);
  bodies.push({success: true, job_id: 'ocr', status: 'completed', result: {
    extracted_data: [{text: 'PRIVATE TEXT'}], extraction_timing: {processing_ms: 10},
  }});
  await window.fetch('http://localhost:8000/api/ai/jobs/ocr');
  await drain(state);
  console.info('[OCR timing] extraction completed', {job_id: 'ocr', pageNumber: 2, frontend_total_ms: 25,
    backend: {processing_ms: 10}});
  assert.equal(state.jobs.ocr.roi_count, 4);
  assert.equal(state.jobs.ocr.flexible_count, 1);
  assert.equal(state.jobs.ocr.result_count, 1);
  assert.equal(state.jobs.ocr.timing.processing_ms, 10);
  assert.equal(state.client[0].pageNumber, 2);
  assert.equal(JSON.stringify(state).includes('PRIVATE'), false);
  assert.deepEqual(state.errors, []);
});

test('mismatched polling result is rejected', async () => {
  const {window, bodies} = setup();
  const state = window.__ocrOnlyRun;
  state.jobs.ocr = {job_id: 'ocr'};
  bodies.push({success: true, job_id: 'wrong', status: 'completed'});
  await window.fetch('http://localhost:8000/api/ai/jobs/ocr');
  await drain(state);
  assert.equal(state.jobs.ocr.status, undefined);
  assert.match(state.errors[0], /mismatch/);
});

test('late response remains attached to its original run', async () => {
  const {window, bodies, state} = setup();
  const first = window.__ocrOnlyRun;
  bodies.push({success: true, job_id: 'first'});
  await window.fetch('http://localhost:8000/api/ai/process', {method: 'POST', body: '{"rois":[]}'});
  window.__ocrOnlyRun = state();
  await drain(first);
  assert.equal(first.jobs.first.job_id, 'first');
  assert.deepEqual(Object.keys(window.__ocrOnlyRun.jobs), []);
});
