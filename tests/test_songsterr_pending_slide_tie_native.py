"""Captured complete-worker controls qualify slide identity, not synth timbre."""
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path

import pytest

from test_songsterr_pending_slide_ties import checked
from test_songsterr_plain_tie_identity import flat

FIXTURE = json.loads((Path(__file__).parent/'fixtures/songsterr_pending_slide_tie_native.json').read_bytes())


@pytest.mark.parametrize('row', FIXTURE['cases'], ids=lambda r:r['id'])
def test_native_hidden_identity_clock_and_slide_target(row):
    raw = row['source']
    assert hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest() == row['sourceSha256']
    if row['guarded']:
        with pytest.raises(ValueError): checked(raw)
        return
    p, v = checked(raw)
    notes = flat(p['tracks'][0])
    strings = len(raw['parts'][0]['tuning'])
    for reference in row['reference'].values():
        held = [n for n in reference['held'] if not n['hidden']]
        resolution = reference['tpqn']
        seconds = lambda ticks: float(F(ticks, resolution)*F(5,6))
        assert len(notes) == len(held)
        for note, native in zip(notes, held):
            assert (note['s'],note['f']) == (strings-1-native['string'],native['fret'])
            assert note['t'] == pytest.approx(seconds(native['attackTick']), abs=1e-9)
            assert note['t']+note['sus'] == pytest.approx(seconds(native['endTick']+1),abs=1e-9)
        slide = reference['slides'][0]
        assert notes[0]['sl'] == slide['to'] == notes[-1]['f']
        assert notes[0]['f'] == slide['from']
        assert bool(notes[0].get('ln')) is (slide['kind'] == 'legato')
        assert reference['sameFretsFullScheduleExact'] is True
        assert reference['noSlideFullScheduleExact'] is False


def test_worker_identity_and_qualification_count_are_pinned():
    assert FIXTURE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert FIXTURE['fullWorkerComparisons'] == FIXTURE['fullWorkerMatches'] == 540
    assert len(FIXTURE['cases']) == 58
