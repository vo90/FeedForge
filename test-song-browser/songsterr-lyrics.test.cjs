'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { acquireAnonymous, lyricsUrl } = require('../electron/song-browser/providers/songsterr/acquire.cjs');

const descriptor = { id: '123', revisionId: '456', approval: 'approved', title: 'Synthetic', artist: 'Artist' };
const meta = { songId: 123, revisionId: 456, tracks: [{ name: 'Vocals' }], image: 'fixture-image', lyrics: true };
const part = { withLyrics: true, measures: [{ voices: [{ beats: [{duration: [1, 1], notes: []}] }] }] };
function response(value, status=200) {
  return {ok:status === 200, status, headers:{get:()=>null}, body:(async function*(){yield Buffer.from(JSON.stringify(value));})()};
}
async function directory(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'feedforge-lyric-acquire-'));
  t.after(()=>fs.rm(dir, {recursive:true, force:true}));
  return dir;
}

test('legacy lyric URLs are pinned to the same revision and validated host', () => {
  assert.equal(lyricsUrl('123','456','fixture-image'), 'https://dqsljvtekg760.cloudfront.net/123/456/fixture-image/lyrics.json');
  assert.equal(lyricsUrl('123','456','fixture-stage'), 'https://d3d3l6a6rcgkaf.cloudfront.net/123/456/fixture-stage/lyrics.json');
  assert.equal(lyricsUrl('123','456',null), 'https://d3rrfvx08uyjp1.cloudfront.net/lyrics/456');
  assert.throws(()=>lyricsUrl('123','456','../outside'));
});

test('legacy sidecar is retained together with its source revision', async t => {
  const dir = await directory(t), calls = [];
  const lyrics = [{beats:[{duration:[1,4],lyrics:[{text:'Hello'}]}]}];
  const result = await acquireAnonymous(descriptor, {directory:dir, fetch:async(url, options)=>{
    calls.push(url); assert.equal(options.credentials,'omit');
    return response(calls.length === 1 ? meta : calls.length === 2 ? part : lyrics);
  }});
  const source = JSON.parse(await fs.readFile(result.path,'utf8'));
  assert.deepEqual(source.legacyLyrics,lyrics);
  assert.deepEqual(source.lyricsAcquisition,{status:'captured',songId:'123',revisionId:'456'});
  assert.equal(calls.length,3);
});

test('missing or malformed optional sidecar preserves instrument acquisition', async t => {
  for (const bad of [response({},404),response({beats:[]})]) {
    const dir = await directory(t); let calls=0;
    const result = await acquireAnonymous(descriptor,{directory:dir,fetch:async()=>++calls===1?response(meta):calls===2?response(part):bad});
    const source = JSON.parse(await fs.readFile(result.path,'utf8'));
    assert.equal(source.parts.length,1);
    assert.equal(source.lyricsAcquisition.status,'unavailable');
    assert.equal(source.legacyLyrics,undefined);
  }
});

test('modern lyrics do not trigger legacy downloads',async t=>{
  const dir=await directory(t);let calls=0;
  const result=await acquireAnonymous(descriptor,{directory:dir,fetch:async()=>response(++calls===1?meta:{...part,newLyrics:[{text:'Hello',offset:1}]})});
  assert.equal(calls,2);
  assert.equal(JSON.parse(await fs.readFile(result.path,'utf8')).parts[0].newLyrics[0].text,'Hello');
});

test('cancellation during optional lyric retrieval is not swallowed',async t=>{
  const dir=await directory(t),controller=new AbortController();let calls=0;
  await assert.rejects(acquireAnonymous(descriptor,{directory:dir,signal:controller.signal,fetch:async()=>{
    if(++calls===3)controller.abort();
    return response(calls===1?meta:calls===2?part:[]);
  }}));
  assert.deepEqual(await fs.readdir(dir),[]);
});
