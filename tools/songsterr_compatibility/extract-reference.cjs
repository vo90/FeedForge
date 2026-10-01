'use strict';
// Explicit maintenance operation. Produces a candidate manifest for review, never updates the accepted one.
const fs=require('node:fs'), crypto=require('node:crypto');
const qualificationToolchain=require('./toolchain.cjs').verify();
const parser=require('@babel/parser'), traverse=require('@babel/traverse').default;
const [input,output]=process.argv.slice(2);
if(!input||!output||fs.existsSync(output))throw Error('Supply a worker and a NEW candidate manifest path');
const bytes=fs.readFileSync(input),source=bytes.toString('utf8');
const ast=parser.parse(source,{sourceType:'script'});let scope;
traverse(ast,{VariableDeclarator(p){if(p.node.id.name==='GeneratorModel'){if(scope)throw Error('Ambiguous GeneratorModel');scope=p.scope;}}});
if(!scope)throw Error('Unrecognized worker structure');
const entries=['mc','Pi','xc','_r'],done=new Set(),queue=[...entries],free=new Set();
while(queue.length){const name=queue.pop();if(done.has(name))continue;done.add(name);
 const binding=scope.getBinding(name);if(!binding)throw Error('Missing '+name);
 binding.path.traverse({ReferencedIdentifier(p){const b=p.scope.getBinding(p.node.name);
  if(!b)free.add(p.node.name);else if(b.scope===scope&&!done.has(p.node.name))queue.push(p.node.name);}});
}
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const bindings=[...done].map(name=>{const n=scope.getBinding(name).path.node;
 return{name,type:n.type,start:n.start,end:n.end,sha256:hash(source.slice(n.start,n.end))};}).sort((a,b)=>a.start-b.start);
fs.writeFileSync(output,JSON.stringify({version:1,sha256:hash(bytes),exposeOffset:scope.path.node.body.end-1,entries,bindings,freeGlobals:[...free].sort(),qualificationToolchain,
 traceStages:['Ts','ts','ks','Cs','oo','Ss','Ha'],qualification:'candidate_requires_review',
 profiles:{authored:{synth:'fluidsynth',useRSE:false,autoFixJson:false,humanize:false},'player-defaults':'captured xc defaults'},
 scope:'Captured scheduling reference; original recording alignment is separate.'},null,2)+'\n');
