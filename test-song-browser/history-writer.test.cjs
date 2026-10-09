const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { HistoryWriter } = require('../electron/song-browser/history-writer.cjs');
function fixture(t, rename) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-history-'));
  t.after(() => fs.rmSync(root, {recursive:true,force:true}));
  const file = path.join(root,'jobs.json'); fs.writeFileSync(file,'{"old":true}');
  return {root,file, writer:new HistoryWriter({io:{...fs,renameSync:rename},delays:[1,2,3]})};
}
test('uncontended saves stay synchronous and leave no temporary files', t => {
  const f=fixture(t,fs.renameSync);
  assert.equal(f.writer.write(f.file,{new:true}),undefined);
  assert.deepEqual(JSON.parse(fs.readFileSync(f.file)),{new:true});
  assert.deepEqual(fs.readdirSync(f.root),['jobs.json']);
});
test('temporary locks yield and snapshots retain write order and captured values', async t => {
  let locked=true;const seen=[];
  const f=fixture(t,(from,to)=>{if(locked)throw Object.assign(Error('locked'),{code:'EPERM'});seen.push(JSON.parse(fs.readFileSync(from)).n);fs.renameSync(from,to);});
  const value={n:1}; const first=f.writer.write(f.file,value); value.n=999;
  const second=f.writer.write(f.file,{n:2});
  assert.deepEqual(JSON.parse(fs.readFileSync(f.file)),{old:true});
  setTimeout(()=>{locked=false;},0);
  await Promise.all([first,second]);await f.writer.flush();
  assert.deepEqual(seen,[1,2]);assert.equal(JSON.parse(fs.readFileSync(f.file)).n,2);
  assert.deepEqual(fs.readdirSync(f.root),['jobs.json']);
});
test('exhausted lock retries keep old history and reject later writes', async t => {
  let attempts=0;const f=fixture(t,()=>{attempts++;throw Object.assign(Error('locked'),{code:'EACCES'});});
  const first=f.writer.write(f.file,{n:1});const second=f.writer.write(f.file,{n:2});
  await assert.rejects(first,{code:'EACCES'});await assert.rejects(second,{code:'EACCES'});
  assert.equal(attempts,4);await assert.rejects(f.writer.flush(),{code:'EACCES'});
  assert.throws(()=>f.writer.write(f.file,{n:3}),{code:'EACCES'});
  assert.deepEqual(JSON.parse(fs.readFileSync(f.file)),{old:true});assert.deepEqual(fs.readdirSync(f.root),['jobs.json']);
});
test('disk-full errors are not retried',t=>{
  let attempts=0;const f=fixture(t,()=>{attempts++;throw Object.assign(Error('full'),{code:'ENOSPC'});});
  assert.throws(()=>f.writer.write(f.file,{}),{code:'ENOSPC'});assert.equal(attempts,1);
  assert.deepEqual(JSON.parse(fs.readFileSync(f.file)),{old:true});
});
test('receipt is durable before publication and cancellation during its wait prevents a file',async t=>{
  const {publishFeedpak}=require('../electron/song-browser/publication.cjs');
  const {hashFile}=require('../electron/song-browser/songsterr-jobs.cjs');
  const f=fixture(t,fs.renameSync),staging=path.join(f.root,'staging');fs.writeFileSync(staging,'test');
  const controller=new AbortController(),job={id:'fixture',outputDir:f.root,outputRelativePath:'song.feedpak',outputHash:await hashFile(staging),controller};
  let release;const held=new Promise(r=>release=r);
  const published=publishFeedpak({job,staging,hashFile,check:()=>{if(controller.signal.aborted)throw Error('cancelled');},writeReceipt:()=>held});
  await new Promise(r=>setTimeout(r,20));assert.equal(fs.existsSync(path.join(f.root,'song.feedpak')),false);
  controller.abort();release();await assert.rejects(published,/cancelled/);assert.equal(job.committed,undefined);
});
