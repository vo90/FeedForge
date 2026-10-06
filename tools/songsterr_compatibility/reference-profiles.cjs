'use strict';
// Direction normalization is a distinct, versioned comparison policy. It is
// deliberately not a new default for the previously qualified authored cases.
const LEGACY_BRUSH_PROFILE='legacy-brush-authored-v1';
const LEGACY_BRUSH_POLICY='songsterr-legacy-brush-direction-swap-v1';
function options(profile,tpqn) {
  if(profile==='authored')return {synth:'fluidsynth',useRSE:false,autoFixJson:false,humanize:false,tpqn};
  if(profile===LEGACY_BRUSH_PROFILE)return {synth:'fluidsynth',useRSE:false,autoFixJson:true,humanize:false,tpqn};
  if(profile==='player-defaults')return {tpqn};
  throw Error('Unknown reference profile');
}
function evidence(profile) {
  options(profile,1);
  return profile===LEGACY_BRUSH_PROFILE?{
    policy:LEGACY_BRUSH_POLICY,version:1,nativeStages:['ro','no'],
    scope:'Native ro/no swaps old-brush direction and refreshes internal old-stroke aliases from modern brush direction; modern timing is unchanged. Deterministic fluidsynth, RSE and humanization disabled. Raw-authored direction is separate.'
  }:null;
}
module.exports={LEGACY_BRUSH_PROFILE,LEGACY_BRUSH_POLICY,options,evidence};
