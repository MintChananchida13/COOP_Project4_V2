const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const ts = require('typescript');

const file = path.resolve(__dirname, '../src/admin/adminApi.ts');
const source = ts.createSourceFile(file, fs.readFileSync(file,'utf8'), ts.ScriptTarget.Latest,true);
const declaration = source.statements.filter(ts.isVariableStatement).flatMap(s=>[...s.declarationList.declarations])
  .find(d=>d.name.getText(source)==='runPrepublishSimulation');
const code = ts.transpileModule('const '+declaration.getText(source)+'; globalThis.run=runPrepublishSimulation;',
  {compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;

test('simulation drains pre-existing loads and discards both stale bundles before refresh', async()=>{
  const cache = new Map([['tpl',{old:true}],['user:tpl',{old:true}],['unrelated',{keep:true}]]);
  let release;
  const oldLoad = new Promise(resolve=>{release=()=>{cache.set('tpl',{lateOld:true});resolve();};});
  let refreshes=0;
  const context = vm.createContext({
    templateBundleCache:cache, templateBundlePromises:new Map([['tpl',oldLoad]]),
    ADMIN_API_BASE_URL:'http://backend',
    fetchWithAuth:async()=>({ok:true,json:async()=>({data:{passed:true,layout_signature_pages:[]}})}),
    asRecord:v=>v||{}, asRecordArray:v=>v||[], debugPrepublishPagesAccess:()=>{},
    mapApiTemplate:()=>({id:'tpl'}), mapPrepublishLayoutSignaturePage:v=>v,mapPrepublishCandidate:v=>v,
    fetchTemplateBundle:async()=>{assert.equal(cache.has('tpl'),false); assert.equal(cache.has('user:tpl'),false); refreshes++; return {template:{id:'tpl'}};},
  });
  vm.runInContext(code,context);
  const running=context.run('tpl');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(refreshes,0);
  release();
  const result=await running;
  assert.equal(refreshes,1);
  assert.equal(result.separationAnalysis.simulationPassed,true);
  assert.equal(cache.has('unrelated'),true);
});
