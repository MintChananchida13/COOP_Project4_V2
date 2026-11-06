const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const moduleCv = require('@techstark/opencv-js');
const ready = new Promise(resolve => {
  if (moduleCv.Mat) resolve({cv:moduleCv});
  else moduleCv.then(cv=>resolve({cv}));
});
const source=fs.readFileSync(path.join(__dirname,'../public/card-crop-worker.js'),'utf8');
const sandbox={exports:{}};
vm.runInNewContext(source.replace('module.exports={detect,detectImage}',
  'module.exports={detect,detectImage,tableReject}'),{module:sandbox});

for(const [name,ys,xs,expected] of [
  ['three rows and two columns',[35,80,125],[80,220],true],
  ['five rows and one column',[25,55,85,115,145],[150],true],
  ['only two rows',[35,125],[80,220],false],
  ['only one column and three rows',[35,80,125],[150],false],
  ['horizontal rules only',[25,55,85,115,145],[],false],
  ['blank',[],[],false],
]) {
  test(`table crop guard keeps rule/intersection decision: ${name}`,async()=>{
    const {cv}=await ready;
    // Non-square ROI with nonzero offset catches swapped row/column indexing.
    const binary=new cv.Mat(400,600,cv.CV_8UC1,new cv.Scalar(0));
    const b={x:80,y:100,width:300,height:180};
    try {
      for(const y of ys)cv.line(binary,new cv.Point(b.x+10,b.y+y),new cv.Point(b.x+290,b.y+y),new cv.Scalar(255),1);
      for(const x of xs)cv.line(binary,new cv.Point(b.x+x,b.y+10),new cv.Point(b.x+x,b.y+170),new cv.Scalar(255),1);
      assert.equal(sandbox.exports.tableReject(cv,binary,b),expected);
    }finally{binary.delete();}
  });
}
