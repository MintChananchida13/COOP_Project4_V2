const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const requests = [];
const window = {
  fetch: (input, options) => new Promise((resolve, reject) => requests.push({ input, options, resolve, reject })),
};
vm.runInNewContext(source, { window, URL, location: { href: 'http://localhost:3000/' } });
const fresh = token => window.__ocrBenchmarkRun = { token, pending: 0, posts: 0, post_requests: 0, job_id: null, jobs: {}, errors: [] };
const response = data => ({ ok: true, clone: () => ({ json: async () => ({ data }) }),
  json: () => { throw new Error('CDP response body evicted: must never read original'); } });
const settle = () => new Promise(resolve => setImmediate(resolve));
const post = () => window.fetch('http://backend/api/templates/detect-dev', { method: 'POST' });
const poll = id => window.fetch(`http://backend/api/templates/detect-dev/jobs/${id}`);

(async () => {
  const current = fresh('first');
  const submitted = post();
  const completed = poll('current');
  const old = poll('old-job');
  const processing = poll('current');
  requests[1].resolve(response({ job_id: 'current', status: 'completed', result: { matched: false } }));
  requests[2].resolve(response({ job_id: 'old-job', status: 'completed', result: { matched: true } }));
  await Promise.all([completed, old]);
  await settle();
  assert.equal(current.job_id, null);
  requests[0].resolve(response({ job_id: 'current', status: 'processing' }));
  requests[3].resolve(response({ job_id: 'current', status: 'processing' }));
  await Promise.all([submitted, processing]);
  await settle();
  assert.equal(current.job_id, 'current');
  assert.equal(current.jobs[current.job_id].status, 'completed');
  assert.equal(current.jobs[current.job_id].result.matched, false);
  assert.equal(current.pending, 0);

  const delayed = poll('current');
  const next = fresh('second');
  requests[4].resolve(response({ job_id: 'current', status: 'completed', result: { matched: true } }));
  await delayed;
  await settle();
  assert.deepEqual(next.jobs, {});
  assert.equal(next.job_id, null);

  const malformed = post();
  requests[5].resolve({ ok: true, clone: () => ({ json: async () => { throw new Error('evicted'); } }) });
  await malformed;
  await settle();
  assert.equal(next.pending, 0);
  assert.equal(next.errors.length, 1);
  assert.equal(next.job_id, null);

  const mismatch = poll('expected');
  requests[6].resolve(response({ job_id: 'different', status: 'completed', result: { matched: true } }));
  await mismatch;
  await settle();
  assert.equal(next.jobs.expected, undefined);
  assert.equal(next.pending, 0);

  const failed = poll('failed');
  requests[7].resolve(response({ job_id: 'failed', status: 'failed', error: 'model unavailable' }));
  await failed;
  await settle();
  assert.equal(next.jobs.failed.status, 'failed');
  assert.equal(next.pending, 0);
  next.postSearchStarted = true;
  const layout = window.fetch('http://backend/api/layout/analyze', { method: 'POST' });
  assert.equal(new URL(requests[8].input).searchParams.get('benchmark_trace_id'), 'second');
  assert.equal(next.post_requests, 1);
  requests[8].resolve(response({}));
  await layout;
  console.log('Fetch capture: out-of-order, stale-run, eviction, failed and mismatched jobs passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
