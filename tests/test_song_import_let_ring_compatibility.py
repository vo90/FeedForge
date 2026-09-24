"""Disclose missing game expression without altering or excusing source notes."""
import pytest

from feedback_converter.song_import.compatibility import inspect_songsterr
from test_song_import_compatibility_verification import reported_fixture
from test_song_import_verification import verify


def marked_fixture():
    source, package = reported_fixture()
    source['parts'][0]['measures'][0]['voices'][0]['beats'][2]['letRing'] = True
    package['chart.json']['notes'][2]['lr'] = True
    package['notation.json']['measures'][0]['staves']['staff']['voices'][0]['beats'][2]['lr'] = True
    report = package['import/compatibility.json']
    report['findingCount'] += 1
    report['findings'].append({'feature': 'beat.letRing',
        'location': 'parts/0/measures/0/voices/0/beats/2/letRing', 'value': True,
        'valueTruncated': False, 'retained': 'original_source',
        'impact': 'display_or_expression', 'category': 'game_limitation'})
    return source, package


@pytest.mark.parametrize('enabled,instrument,expected', [(True,30,True), (False,30,False), (True,0,False)])
def test_only_active_guitar_bass_marks_get_located_nonblocking_warning(enabled, instrument, expected):
    source, _ = marked_fixture()
    source['tracks'][0]['instrumentId'] = instrument
    source['parts'][0]['measures'][0]['voices'][0]['beats'][2]['letRing'] = enabled
    report = inspect_songsterr(source)
    findings = [f for f in report['findings'] if f['feature'] == 'beat.letRing']
    assert bool(findings) is expected
    if expected:
        f = findings[0]
        assert f['impact'] == 'display_or_expression'
        assert f['category'] == 'game_limitation'
        assert (f['measure'], f['voice'], f['beat']) == (1,1,3)
        assert f['value'] is True and f['retained'] == 'original_source'
        assert 'does not display' in f['message'] and 'durations' in f['message']


@pytest.mark.parametrize('fault', [None, 'remove_warning', 'change_warning', 'remove_mark', 'change_pitch', 'extend_sustain'])
def test_report_preserves_mark_and_does_not_excuse_a_changed_note(tmp_path, fault):
    source, package = marked_fixture()
    report = package['import/compatibility.json']
    if fault == 'remove_warning':
        report['findings'].pop(); report['findingCount'] -= 1
    elif fault == 'change_warning': report['findings'][-1]['value'] = False
    elif fault == 'remove_mark': del package['chart.json']['notes'][2]['lr']
    elif fault == 'change_pitch': package['chart.json']['notes'][2]['f'] += 1
    elif fault == 'extend_sustain': package['chart.json']['notes'][2]['sus'] += .5
    result = verify(tmp_path, source, package)
    assert result['status'] == ('passed' if fault is None else 'failed'), result
