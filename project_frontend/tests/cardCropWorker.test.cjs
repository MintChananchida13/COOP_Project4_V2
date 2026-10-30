const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const ts = require('typescript');
const vm = require('node:vm');

test('preload reuses the worker and cancellation creates a fresh worker', () => {
  const workers = [];
  class FakeWorker {
    constructor(url) { this.url = url; this.messages = []; this.listeners = {}; workers.push(this); }
    postMessage(message) { this.messages.push(message); }
    addEventListener(type, listener) { this.listeners[type] = listener; }
    terminate() { this.terminated = true; }
  }
  const source = fs.readFileSync(require('node:path').join(__dirname, '../src/user/cardCropWorker.ts'), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, Worker: FakeWorker });
  const first = exports.getCardCropWorker();
  assert.equal(first.url, '/card-crop-worker.js');
  assert.equal(first.messages[0].type, 'preload');
  assert.equal(exports.getCardCropWorker(), first);
  exports.disposeCardCropWorker(first);
  assert.equal(first.terminated, true);
  const second = exports.getCardCropWorker();
  assert.notEqual(second, first);
  second.listeners.error();
  assert.equal(second.terminated, true);
  assert.notEqual(exports.getCardCropWorker(), second);
});
