"""Compare playable targets with the separately captured native worker output."""
import hashlib
import json
from pathlib import Path

import pytest

from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from test_songsterr_harmonic_fallback_reference import source_for


PATH = Path(__file__).parent/'fixtures/songsterr_natural_playback_reference.json'
FIXTURE = json.loads(PATH.read_text())


def test_native_qualification_identity_and_scope():
    assert hashlib.sha256(PATH.read_bytes()).hexdigest() == '365ca56d89b727e28e8dabe403270cf86363372203bf61939e158a3f9ec684b9'
    assert FIXTURE['policy'] == 'songsterr-natural-playback-v1'
    assert FIXTURE['summary'] == {'direct': 25, 'fullScorePairs': 45, 'comparisons': 90, 'matched': 90}
    assert FIXTURE['currentWorkerSha256'] == 'a7f191f19196af972bd61ed5f9c67a6b63151fc8b0b8a1f665061a0cb493c980'
    manifest = json.loads((PATH.parents[2]/'tools/songsterr_compatibility/reference-manifest.json').read_text())
    assert manifest['sha256'] == FIXTURE['pinnedWorkerSha256']


@pytest.mark.parametrize('row', [row for row in FIXTURE['rows'] if 'profiles' in row], ids=lambda row: str(row['fret']))
def test_producer_pitch_matches_captured_native_event(row):
    raw = source_for({'string': 1, 'fret': row['fret'], 'harmonic': 'natural'})
    produced = render(parse(raw))['tracks'][0]
    note, = produced['notes']
    written = produced['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]
    for profile in row['profiles'].values():
        assert profile['equivalent'] is True
        target, = profile['targets']
        # Full-score authored/RSE targets may be sample-library keys. The
        # separately captured fluidsynth interval is the sounding-pitch oracle.
        if row['disposition'] == 'ordinary-playback':
            assert written['midi'] == target['pitch']
        assert note['f'] == target['fret']
        assert note['t'] == 0 and note['sus'] == 2
    assert written['midi'] == 59 + row['fret'] + row['direct']['notes'][0]['interval']
    if row['disposition'] == 'ordinary-playback':
        assert row['direct']['applied'] is False
        assert not any(key in note for key in ('hm', 'hn', 'hps', 'harmonic_alias'))
    else:
        assert note['hm'] is True
        assert note['hps'] == row['fret'] + row['direct']['notes'][0]['interval']
