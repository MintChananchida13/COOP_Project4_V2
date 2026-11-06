const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const zlib = require('node:zlib');
const { detect, detectImage } = require('../public/card-crop-worker.js');
const moduleCv = require('@techstark/opencv-js');
const ready = new Promise(resolve => {
  if (moduleCv.Mat) resolve({ cv: moduleCv });
  else moduleCv.then(cv => resolve({ cv }));
});

// Disable ONLY the new PDF guard to prove each regression exercises a formerly
// accepted candidate, rather than passing because an existing shape guard fires.
const source = fs.readFileSync(path.join(__dirname, '../public/card-crop-worker.js'), 'utf8');
const entry = 'function detect(cv,rgba){return detectBoundary(cv,rgba,true);}';
assert.ok(source.includes(entry));
const legacy = { exports: {} };
vm.runInNewContext(source.replace(entry, entry.replace('true', 'false')), { module: legacy });
const legacyPdf = legacy.exports.detect;
const color = (cv, level) => new cv.Scalar(level, level, level, 255);

function page(cv, faint = false) {
  const image = new cv.Mat(1132, 800, cv.CV_8UC4, new cv.Scalar(255, 255, 255, 255));
  cv.rectangle(image, new cv.Point(160, 400), new cv.Point(640, 700), color(cv, faint ? 230 : 30), 3);
  // Card portrait and print remain inside its physical boundary.
  cv.rectangle(image, new cv.Point(190, 440), new cv.Point(270, 560), color(cv,160), -1);
  cv.putText(image, 'ID CARD 123456', new cv.Point(290, 480), cv.FONT_HERSHEY_SIMPLEX, .55, color(cv,80), 1);
  return image;
}
function text(cv, image, x, y, level = 40) {
  cv.putText(image, 'DOCUMENT CONTENT', new cv.Point(x, y), cv.FONT_HERSHEY_SIMPLEX, .6, color(cv,level), 1);
}
function photo(cv, image) {
  cv.rectangle(image, new cv.Point(60, 100), new cv.Point(150, 200), new cv.Scalar(160, 100, 70, 255), -1);
  cv.circle(image, new cv.Point(105, 130), 18, color(cv,210), -1);
  cv.line(image, new cv.Point(65, 190), new cv.Point(145, 150), color(cv,40), 3);
}
function table(cv, image) {
  for (const y of [100, 125, 150]) cv.line(image, new cv.Point(60, y), new cv.Point(150, y), color(cv,40), 1);
  for (const x of [60, 105, 150]) cv.line(image, new cv.Point(x, 100), new cv.Point(x, 150), color(cv,40), 1);
}
function noise(cv, image) {
  // Deterministic, spatially scattered single-pixel scanner noise.
  let state = 12345;
  for (let i = 0; i < 1500; i++) {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    const x = state % 800;
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    const y = state % 1132;
    cv.rectangle(image, new cv.Point(x, y), new cv.Point(x, y), color(cv,110), -1);
  }
}

const outsideCases = {
  'sparse text above': (cv, image) => text(cv, image, 60, 130),
  'text below': (cv, image) => text(cv, image, 60, 900),
  'text to the left': (cv, image) => text(cv, image, 10, 550),
  'text to the right': (cv, image) => text(cv, image, 655, 550),
  'faint text': (cv, image) => text(cv, image, 60, 130, 225),
  'small external table': table,
  'external image': photo,
  'mixed text, table and image': (cv, image) => { table(cv, image); text(cv, image, 60, 900); photo(cv, image); },
};
for (const [name, draw] of Object.entries(outsideCases)) {
  test(`PDF retains full A4 with ${name} outside a card-shaped region`, async () => {
    const { cv } = await ready;
    const image = page(cv);
    try {
      draw(cv, image);
      assert.ok(legacyPdf(cv, image).box, 'Old geometric checks must accept this unsafe crop');
      assert.equal(detect(cv, image).box, null);
      // Both photo detection and its geometry fallback retain their old behavior.
      assert.deepEqual(detectImage(cv, image), JSON.parse(JSON.stringify(legacy.exports.detectImage(cv, image))));
    } finally { image.delete(); }
  });
}
for (const faint of [false, true]) {
  test(`PDF crops a ${faint ? 'faint' : 'normal'} card with scan noise on A4`, async () => {
    const { cv } = await ready;
    const image = page(cv, faint);
    try {
      noise(cv, image);
      const before = legacyPdf(cv, image), after = detect(cv, image);
      assert.ok(before.box);
      assert.ok(after.box, JSON.stringify(after));
      for (let i = 0; i < 4; i++) assert.ok(Math.abs(after.box[i] - [160,400,640,700][i]) <= 8);
    } finally { image.delete(); }
  });
}

test('PDF rejects all candidates when two separate card-like regions contain content', async () => {
  const { cv } = await ready;
  const image = new cv.Mat(1132, 800, cv.CV_8UC4, new cv.Scalar(255,255,255,255));
  try {
    cv.rectangle(image, new cv.Point(60,150), new cv.Point(460,400), color(cv,30), 3);
    cv.rectangle(image, new cv.Point(260,700), new cv.Point(660,950), color(cv,30), 3);
    assert.ok(legacyPdf(cv,image).box);
    assert.equal(detect(cv,image).box,null);
  } finally { image.delete(); }
});

test('JPG/PNG geometry fallback never invokes the PDF content guard', async () => {
  const { cv } = await ready;
  const isolated = { exports: {} };
  vm.runInNewContext(source
    .replace('const photo=detectPhoto(cv,rgba);', 'const photo=null;')
    .replace('function pdfContentComponents(cv,gray){', 'function pdfContentComponents(cv,gray){throw new Error("PDF-only guard invoked");'),
    { module: isolated });
  const image = page(cv);
  try {
    text(cv,image,60,130);
    const expected=legacyPdf(cv,image);
    assert.ok(expected.box);
    assert.deepEqual(JSON.parse(JSON.stringify(isolated.exports.detectImage(cv,image))),JSON.parse(JSON.stringify(expected)));
  } finally { image.delete(); }
});

// Optional real PDF/raster integration corpus; generated locally to avoid
// committing document pixels. See pdf_content_preservation_fixtures.py.
const fixtureIndex = process.argv.indexOf('--fixtures');
const fixtures = fixtureIndex < 0 ? [] : JSON.parse(fs.readFileSync(process.argv[fixtureIndex+1], 'utf8'));
for (const info of fixtures) {
  test(`rendered PDF content preservation: ${info.name}`, async () => {
    const { cv } = await ready;
    const image = cv.matFromArray(info.height,info.width,cv.CV_8UC4,zlib.inflateSync(Buffer.from(info.pixels,'base64')));
    try {
      const before=legacyPdf(cv,image), after=detect(cv,image);
      assert.ok(before.box, 'Fixture must exercise a previously accepted PDF crop');
      if(info.expectedCrop){
        assert.deepEqual(after.box,Array.from(before.box));
      } else {
        assert.equal(after.box,null);
      }
    } finally { image.delete(); }
  });
}
