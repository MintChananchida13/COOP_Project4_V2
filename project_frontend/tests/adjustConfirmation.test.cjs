const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

const file = path.resolve(__dirname,'../src/user/components/AdjustZone.tsx');
const source = ts.createSourceFile(file,fs.readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let declaration;
function find(node){if(ts.isVariableDeclaration(node)&&node.name.getText(source)==='handleConfirmAll')declaration=node;ts.forEachChild(node,find);}
find(source);
const code = ts.transpileModule(`const ${declaration.getText(source)}; globalThis.confirm = handleConfirmAll;`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
const defaults={rotation:0,brightness:100,contrast:100,sharpness:0,perspectiveV:0,perspectiveH:0,flipH:false,flipV:false,cropBox:null,isCropped:false,croppedLocalUrl:null};
function make(configs,overrides={}){
  let confirmed, error;const renders=[];
  class ImageStub {set src(value){this.value=value;queueMicrotask(()=>this.onload());}}
  const ctx={isProcessing:false,autoDetecting:false,hasPendingCrop:false,sourceFileType:'image',originalBackupList:['original-1','original-2'],imagesList:['original-1','original-2'],pagesConfig:configs,DEFAULT_CONFIG:defaults,
    Image:ImageStub,setIsProcessing:()=>{},setCropError:v=>{error=v},console,onBatchConfirm:pages=>{confirmed=Array.from(pages)},
    processSingleImageCanvas:(image,config,cropped)=>{renders.push({src:image.value,rotation:config.rotation,cropped});return `adjusted:${image.value}:${config.rotation}`},...overrides};
  vm.runInNewContext(code,ctx);return {ctx,renders,get confirmed(){return confirmed},get error(){return error}};
}
test('confirmation sends selected image for each page and applies adjustments once',async()=>{
  const run=make([{...defaults,isCropped:true,croppedLocalUrl:'crop-1'}, {...defaults,isCropped:false,croppedLocalUrl:'old-crop-2',rotation:90}]);
  await run.ctx.confirm();
  assert.deepEqual(run.confirmed,['crop-1','adjusted:original-2:90']);
  assert.deepEqual(run.renders,[{src:'original-2',rotation:90,cropped:false}]);
});
test('switching to original keeps original even when a crop remains available',async()=>{
  const run=make([{...defaults,isCropped:false,croppedLocalUrl:'crop-1'}, {...defaults}]);
  await run.ctx.confirm();
  assert.deepEqual(run.confirmed,['original-1','original-2']);
  assert.equal(run.renders.length,0);
});
test('confirmation waits while detecting or editing a crop',async()=>{
  for(const state of [{autoDetecting:true},{hasPendingCrop:true}]){
    const run=make([{...defaults},{...defaults}],state);await run.ctx.confirm();assert.equal(run.confirmed,undefined);
  }
});

test('admin editor keeps the existing confirmation behavior',async()=>{
  const image={complete:true,naturalWidth:800,value:'admin-current'};
  const run=make([{...defaults},{...defaults,croppedLocalUrl:'saved-admin-page'}],
    {sourceFileType:undefined,currentIndex:0,isCropped:false,rawImageRef:{current:image},croppedImageRef:{current:null}});
  await run.ctx.confirm();
  assert.deepEqual(run.confirmed,['adjusted:admin-current:0','saved-admin-page']);
});
