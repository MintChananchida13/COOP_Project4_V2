const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

test('PDF guard refreshes WASM views after memory growth and retains content/noise decisions', () => {
  const source = fs.readFileSync(path.join(__dirname, '../public/card-crop-worker.js'), 'utf8');
  const sandbox = { exports: {} };
  vm.runInNewContext(source.replace('module.exports={detect,detectImage}',
    'module.exports={detect,detectImage,pdfContentComponents}'), { module: sandbox });
  const mats = [], reads = [];
  class Mat {
    constructor() { this.id=mats.length; this.rows=0; this.cols=0; mats.push(this); reads.push({}); }
    get data() { reads[this.id].bytes=(reads[this.id].bytes||0)+1; return this.bytes; }
    get data32S() { reads[this.id].ints=(reads[this.id].ints||0)+1; return this.ints; }
    delete() { this.deleted=true; }
  }
  const W=100,H=80;
  let components=0;
  const cv = {
    Mat, Size: class {}, MORPH_RECT:0, ADAPTIVE_THRESH_GAUSSIAN_C:0, THRESH_BINARY_INV:0, CV_32S:0,
    getStructuringElement() { return new Mat(); },
    adaptiveThreshold(gray, ink) {
      ink.bytes=new Uint8Array(W*H);
      for(let y=20;y<25;y++)for(let x=10;x<41;x++)ink.bytes[y*W+x]=255;
      ink.bytes[60*W+70]=255; // An isolated scanner speck must remain ignored.
    },
    connectedComponentsWithStats(input, labels, stats) {
      components++;
      labels.ints=new Int32Array(W*H);
      for(let y=20;y<25;y++)for(let x=10;x<41;x++)labels.ints[y*W+x]=1;
      stats.rows=components===1?3:2;
      stats.ints=new Int32Array(stats.rows*5);
      stats.ints.set([10,20,31,5,155],5);
      if(components===1){
        labels.ints[60*W+70]=2;
        stats.ints.set([70,60,1,1,1],10);
      }
    },
    dilate(ink) {
      assert.equal(ink.bytes[60*W+70],0);
      // Simulate WebAssembly memory growth: cached pre-call views detach.
      const oldBuffer=ink.bytes.buffer;
      ink.bytes=new Uint8Array(ink.bytes);
      structuredClone(oldBuffer,{transfer:[oldBuffer]});
      assert.equal(oldBuffer.byteLength,0);
    },
  };
  const result=sandbox.exports.pdfContentComponents(cv,{cols:W,rows:H});
  assert.deepEqual(JSON.parse(JSON.stringify(result)),[{x:10,y:20,right:41,bottom:25,ink:155}]);
  assert.equal(reads[0].bytes,2,'Read the pixel view once per allocation-free phase');
  assert.equal(reads[1].ints,1);
  assert.equal(reads[2].ints,1);
  assert.equal(reads[5].ints,1);
  assert.ok(mats.every(m=>m.deleted),'All OpenCV Mats must still be released');
});
