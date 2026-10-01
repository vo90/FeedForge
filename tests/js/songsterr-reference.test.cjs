'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {prepare,manifest}=require('../../tools/songsterr_compatibility/reference.cjs');
test('unknown reference code is rejected before evaluation',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'reference-test-')),file=path.join(dir,'worker.js');
 try {fs.writeFileSync(file,'throw Error("do not execute")');assert.throws(()=>prepare(file),/Unreviewed/);}
 finally {fs.unlinkSync(file);fs.rmdirSync(dir);}
});
test('accepted reference declares identity, profile and stage boundaries',()=>{
 assert.match(manifest.sha256,/^[a-f0-9]{64}$/);
 assert.ok(manifest.bindings.length>100);
 assert.ok(manifest.entries.includes('mc'));
 assert.ok(manifest.traceStages.includes('Ts'));
 assert.equal(manifest.profiles.authored.humanize,false);
});
