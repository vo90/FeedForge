'use strict';
// Research only. Execute reviewed closures from captured public bundles, no network or DOM.
const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const parser=require('@babel/parser'),traverse=require('@babel/traverse').default;
require('./toolchain.cjs').verify();
const accepted=require('./video-clock-reference-manifest.json');
function prepare(commonPath,vendorPath){
const paths={common:commonPath,vendor:vendorPath};
const modules={};
for(const [id,path] of Object.entries(paths)){
 const text=fs.readFileSync(path,'utf8');assert.equal(crypto.createHash('sha256').update(text).digest('hex'),accepted.find(m=>m.id===id).sha256,'Unreviewed video clock asset');const ast=parser.parse(text,{sourceType:'module'});let scope;
 const exports={};traverse(ast,{Program(p){scope=p.scope;},ExportNamedDeclaration(p){for(const s of p.node.specifiers)exports[s.exported.name]=s.local.name;}});
 modules[id]={text,scope,exports,used:new Set(),free:new Set(),imports:[]};
}
function requireBinding(id,name){const m=modules[id];if(m.used.has(name))return;m.used.add(name);
 const b=m.scope.getBinding(name);if(!b)throw Error('Missing '+id+':'+name);
 if(b.path.isImportSpecifier()){
  const file=b.path.parent.source.value;if(!file.includes('vendor-zmbb5PfyugbiSOMw'))throw Error('Unexpected imported module '+file);
  const exported=b.path.node.imported.name, local=modules.vendor.exports[exported];if(!local)throw Error('Missing export '+exported);
  m.imports.push({name,id:'vendor',local});requireBinding('vendor',local);return;
 }
 b.path.traverse({ReferencedIdentifier(p){const a=p.scope.getBinding(p.node.name);if(!a)m.free.add(p.node.name);else if(a.scope===m.scope)requireBinding(id,p.node.name);}});
}
for(const n of ['uu','ju','Rh','KO','hu'])requireBinding('common',n);
let code='';const manifest=[];
for(const id of ['vendor','common']){
 const m=modules[id];const selected=[...m.used].map(name=>({name,node:m.scope.getBinding(name).path.node})).filter(x=>x.node.type!=='ImportSpecifier').sort((a,b)=>a.node.start-b.node.start);
 const free=[...m.free].sort();assert.ok(free.every(n=>['Array','Error','JSON','Map','Math','Set','console','Number','Infinity','Object','String','undefined','isNaN'].includes(n)),JSON.stringify(free));
 code+=`globalThis.${id}=(()=>{\n`+m.imports.map(i=>`const ${i.name}=${i.id}.${i.local};`).join('\n')+'\n';
 code+=selected.map(({node})=>(node.type==='VariableDeclarator'?'const ':'')+m.text.slice(node.start,node.end)+';').join('\n');
 code+=`\nreturn {${[...m.used].join(',')}};})();\n`;
 manifest.push({id,sha256:crypto.createHash('sha256').update(m.text).digest('hex'),free,bindings:selected.map(({name,node})=>({name,type:node.type,start:node.start,end:node.end,sha256:crypto.createHash('sha256').update(m.text.slice(node.start,node.end)).digest('hex')})),imports:m.imports});
}
const ctx=vm.createContext({console:{log(){},warn(){},error(){}}},{codeGeneration:{strings:false,wasm:false}});
vm.runInContext(code,ctx,{timeout:5000});
function replay(part){ctx.input=structuredClone(part);vm.runInContext(`globalThis.p=common.uu(input,{useReprisesCheck:true});
for(const m of p.measures)common.ju(m,{isAnacrusis:p.anacrusis,tpqn:p.tpqn,lastMeasureIndex:p.measures.length-1,partTempos:p.automations.tempo});
globalThis.result={tpqn:p.tpqn,order:p.progression,boundaries:common.Rh(p),tempos:common.hu.forProgression(p.progression.map(i=>p.measures[i])).getEvents(),bars:p.measures.map(m=>({index:m.index,ticks:m.totalTicks,tempos:m.tempos,layout:[...m.temposLayoutsByTime.keys()]}))};`,ctx,{timeout:5000});return JSON.parse(JSON.stringify(ctx.result));}
assert.deepEqual(manifest,accepted,'Reference binding set drift');
return {replay,manifest,interpolate:(from,to,value)=>{ctx.from=from;ctx.to=to;ctx.value=value;return vm.runInContext('common.KO(from,to,value)',ctx,{timeout:1000});}};
}
module.exports={prepare};
