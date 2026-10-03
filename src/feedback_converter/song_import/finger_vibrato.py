"""Authored finger-vibrato controls on the completed tie timeline.

Initial controls extend over the tie; hidden continuations own their written
interval. A later reset stops the active control, rather than resuming an older
one. Sub-tick synth resets and invented slide-out pitch are not tablature.
"""
from ..vibrato import slice_marks


def finish(output, articulation, at):
    segments = articulation['bend_segments']
    if not any(n.effects.get('__finger_vibrato') for n, *_ in segments):
        return
    controls = []
    for i, (note, start, end, _) in enumerate(segments):
        kind = note.effects.get('__finger_vibrato')
        if not kind:
            continue
        stop = articulation['end'] if i == 0 else end
        controls.extend([(start, 1, i, kind), (stop, 0, i, None)])
    controls.sort()
    marks, active, left = [], None, None
    for when, _, _, kind in controls:
        if active and left < when:
            mark = {'start': at(left)-output['t'], 'end': at(when)-output['t'], 'intensity': active}
            if marks and marks[-1]['end'] == mark['start'] and marks[-1]['intensity'] == active:
                marks[-1]['end'] = mark['end']
            else:
                marks.append(mark)
        left, active = when, kind
    output['vibrato_marks'] = slice_marks(marks, 0, output['sus'])
