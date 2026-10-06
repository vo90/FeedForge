'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {prepare,manifest}=require('../../tools/songsterr_compatibility/reference.cjs');
const profiles=require('../../tools/songsterr_compatibility/reference-profiles.cjs');
test('unknown reference code is rejected before evaluation',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'reference-test-')),file=path.join(dir,'worker.js');
 try {fs.writeFileSync(file,'throw Error("do not execute")');assert.throws(()=>prepare(file),/Unreviewed/);}
 finally {fs.unlinkSync(file);fs.rmdirSync(dir);}
});

test('old-brush normalization is an explicit deterministic versioned profile',()=>{
 const raw=profiles.options('authored',960),normalized=profiles.options(profiles.LEGACY_BRUSH_PROFILE,960);
 assert.deepEqual(raw,{synth:'fluidsynth',useRSE:false,autoFixJson:false,humanize:false,tpqn:960});
 assert.deepEqual(normalized,{...raw,autoFixJson:true});
 assert.deepEqual(profiles.options('player-defaults',960),{tpqn:960});
 assert.equal(profiles.evidence('authored'),null);
 assert.equal(profiles.evidence(profiles.LEGACY_BRUSH_PROFILE).policy,'songsterr-legacy-brush-direction-swap-v1');
 assert.deepEqual(profiles.evidence(profiles.LEGACY_BRUSH_PROFILE).nativeStages,['ro','no']);
 assert.throws(()=>profiles.options('unversioned-repair',960),/Unknown reference profile/);
 normalized.humanize=true;
 assert.equal(profiles.options(profiles.LEGACY_BRUSH_PROFILE,960).humanize,false);
});
test('accepted reference declares identity, profile and stage boundaries',()=>{
 assert.match(manifest.sha256,/^[a-f0-9]{64}$/);
 assert.ok(manifest.bindings.length>100);
 assert.ok(manifest.entries.includes('mc'));
 assert.ok(manifest.traceStages.includes('Ts'));
 assert.equal(manifest.profiles.authored.humanize,false);
});

test('reference extraction tools are pinned separately from app dependency declarations',()=>{
 const {verify}=require('../../tools/songsterr_compatibility/toolchain.cjs');
 const lock=require('../../package-lock.json');
 const actual=verify(name=>lock.packages['node_modules/'+name].version);
 assert.equal(actual.babelParser,manifest.qualificationToolchain.babelParser);
 assert.equal(actual.babelTraverse,manifest.qualificationToolchain.babelTraverse);
 assert.throws(()=>verify(()=> '0.0.0'),/Unqualified reference extraction tool/);
});
