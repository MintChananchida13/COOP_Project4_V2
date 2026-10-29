const assert = require('node:assert/strict');
const zlib = require('node:zlib');
const test = require('node:test');
const {detect, detectImage} = require('../public/card-crop-worker.js');
const moduleCv = require('@techstark/opencv-js');
const ready = new Promise(resolve => {
  if (moduleCv.Mat) resolve({cv:moduleCv});
  else moduleCv.then(cv => resolve({cv}));
});
const input = process.stdin.isTTY ? '' : require('node:fs').readFileSync(0, 'utf8');
const fixtures = input.trim() ? JSON.parse(input) : [];
const cases = [
  ['card', [157,337,643,643]], ['faint', [157,337,644,644]],
  ['broken', [157,337,643,643]], ['portrait', null], ['blank', null], ['grid', null],
];
for (const [name, expected] of cases) {
  test(`card boundary fixture: ${name}`, async () => {
    const {cv} = await ready;
    const image = new cv.Mat(1000, 800, cv.CV_8UC4, new cv.Scalar(255,255,255,255));
    const color = level => new cv.Scalar(level,level,level,255);
    const point = (x,y) => new cv.Point(x,y);
    try {
      if (name === 'card' || name === 'faint') cv.rectangle(image,point(160,340),point(640,640),color(name === 'card' ? 30 : 230),3);
      if (name === 'portrait') cv.rectangle(image,point(240,260),point(560,740),color(30),3);
      if (name === 'grid') {
        for (let y=100;y<901;y+=160) cv.line(image,point(80,y),point(720,y),color(0),3);
        for (const x of [80,400,720]) cv.line(image,point(x,100),point(x,900),color(0),3);
      }
      if (name === 'broken') {
        for (let x=160;x<640;x+=75) for (const y of [340,640]) cv.line(image,point(x,y),point(Math.min(x+48,640),y),color(100),3);
        for (let y=340;y<640;y+=75) for (const x of [160,640]) cv.line(image,point(x,y),point(x,Math.min(y+48,640)),color(100),3);
      }
      const result = detect(cv,image);
      assert.equal(Boolean(result.box),Boolean(expected));
      if (expected) for(let i=0;i<4;i++) assert.ok(Math.abs(result.box[i]-expected[i])<=8);
    } finally { image.delete(); }
  });
}

test('image mode detects a card against a surrounding background', async () => {
  const {cv} = await ready;
  const image = new cv.Mat(630, 1000, cv.CV_8UC4, new cv.Scalar(100, 110, 120, 255));
  try {
    cv.rectangle(image, new cv.Point(140,125), new cv.Point(800,535), new cv.Scalar(230,230,230,255), -1);
    cv.rectangle(image, new cv.Point(140,125), new cv.Point(800,535), new cv.Scalar(35,35,35,255), 3);
    const result = detectImage(cv,image);
    assert.ok(result.box, 'Card against background should be found');
    for (const [actual, expected] of result.box.map((value,index)=>[value,[132,120,808,540][index]])) {
      assert.ok(Math.abs(actual-expected)<=25, JSON.stringify(result));
    }
  } finally { image.delete(); }
});

test('image mode does not crop a small card-like region inside a full-frame image', async () => {
  const {cv} = await ready;
  const image = new cv.Mat(406, 610, cv.CV_8UC4, new cv.Scalar(255,255,255,255));
  try {
    cv.rectangle(image, new cv.Point(248,264), new cv.Point(467,392), new cv.Scalar(30,30,30,255), 3);
    assert.equal(detectImage(cv,image).box, null);
  } finally { image.delete(); }
});
for (const info of fixtures) {
  const fixture = info.name;
  test(`browser detector matches Python v8 on ${fixture}`, async () => {
    const {cv} = await ready;
    const bytes=zlib.inflateSync(Buffer.from(info.pixels,'base64'));
    const mat=cv.matFromArray(info.height,info.width,cv.CV_8UC4,new Uint8Array(bytes));
    let result;
    try {result=detect(cv,mat);} finally {mat.delete();}
    assert.equal(Boolean(result.box),Boolean(info.bbox), JSON.stringify({reference:info.bbox, actual:result}));
    if(info.bbox)for(let i=0;i<4;i++)assert.ok(Math.abs(result.box[i]-info.bbox[i])<=8, JSON.stringify({reference:info.bbox,actual:result.box}));
  });
}

for (const info of fixtures.filter(item => /\.(jpg|png)$/i.test(item.name))) {
  test(`photo card detection on ${info.name}`, async () => {
    const {cv} = await ready;
    const bytes=zlib.inflateSync(Buffer.from(info.pixels,'base64'));
    const mat=cv.matFromArray(info.height,info.width,cv.CV_8UC4,new Uint8Array(bytes));
    let result;
    try {result=detectImage(cv,mat);} finally {mat.delete();}
    if (/\.jpg$/i.test(info.name) || info.name === 'photo-converted.png') {
      assert.ok(result.box, 'A card photographed on a background should be detected');
      const expected=[134,124,808,541];
      for(let i=0;i<4;i++) assert.ok(Math.abs(result.box[i]-expected[i])<=25, JSON.stringify(result));
    } else {
      assert.equal(result.box,null,'A full-frame card must not crop an interior region');
    }
  });
}
