from copy import deepcopy
import pytest
from feedback_converter.song_import.local_sync import compare_opening_evidence
from feedback_converter.song_import.local_sync import compare_assessment
from feedback_converter.song_import.verification import Check


def example():
    return {'version':'bounded-opening-map-v1','status':'supported','audioSha256':'audio',
            'replacementBoundaries':[.18,1.01], 'fitTimes':[.18,.456667,.733333],
            'fitPitch':[.614689,.613554,.613176], 'heldOutPitch':[.181448,.477106,.661587],
            'alternativeRatio':1.287935, 'maxOnsetError':.014966,
            'tuning':{'referenceHz':451.948036,'status':'inconclusive'}}


@pytest.mark.parametrize('field',['fitPitch','heldOutPitch','alternativeRatio'])
def test_single_rounded_diagnostic_unit_is_portable(field):
    fresh=example();stored=deepcopy(fresh)
    if isinstance(stored[field],list):stored[field][-1]+=.000001
    else:stored[field]+=.000001
    check=Check();compare_opening_evidence(fresh,stored,check)
    assert not check.total_errors,check.errors


@pytest.mark.parametrize('fault',['large_metric','decision','coordinate','sample_time','onset_error','tuning','audio','extra','missing','count','boolean'])
def test_musical_decisions_identity_and_significant_diagnostic_changes_remain_strict(fault):
    fresh=example();stored=deepcopy(fresh)
    if fault=='large_metric':stored['fitPitch'][-1]+=.00001
    elif fault=='decision':stored['status']='inconclusive'
    elif fault=='coordinate':stored['replacementBoundaries'][0]+=.000001
    elif fault=='sample_time':stored['fitTimes'][0]+=.000001
    elif fault=='onset_error':stored['maxOnsetError']+=.000001
    elif fault=='tuning':stored['tuning']['referenceHz']+=.000001
    elif fault=='audio':stored['audioSha256']='other'
    elif fault=='extra':stored['other']=1
    elif fault=='missing':stored.pop('fitPitch')
    elif fault=='count':stored['fitPitch'].pop()
    else:stored['alternativeRatio']=True
    check=Check();compare_opening_evidence(fresh,stored,check)
    assert check.total_errors


@pytest.mark.parametrize('field',['pitchAtMap','pitchShiftRank','pitchNearBest','pitchBest','pitchContrast',
    'attackAtMap','attackBest','attackNearBest','attackContrast'])
@pytest.mark.parametrize('difference,accepted',[(.000001,True),(.00001,False)])
def test_recording_scalar_quantization_is_bounded(field,difference,accepted):
    fresh={'status':'supported','start':184,'tracks':[{field:.508104}]};stored=deepcopy(fresh)
    stored['tracks'][0][field]+=difference
    check=Check();compare_assessment(fresh,stored,check)
    assert (not check.total_errors)==accepted


@pytest.mark.parametrize('field',['start','end','offset','bestOffset','status','audioSha256'])
def test_recording_times_decisions_and_identity_stay_exact(field):
    fresh={field:.1 if field not in ('status','audioSha256') else 'original'};stored=deepcopy(fresh)
    stored[field]=fresh[field]+.000001 if isinstance(fresh[field],float) else 'changed'
    check=Check();compare_assessment(fresh,stored,check)
    assert check.total_errors
