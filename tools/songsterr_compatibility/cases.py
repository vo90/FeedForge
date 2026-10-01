"""Deterministic synthetic music, independent of captured songs and converter output."""
from copy import deepcopy
from itertools import product


def envelope(measures):
    tuning = [64, 59, 55, 50, 45, 40]
    return {"format": "songsterr", "songId": 1, "revisionId": 1,
            "title": "Compatibility fixture", "artist": "Synthetic",
            "tracks": [{"id": 0, "name": "Lead", "instrumentId": 30, "tuning": tuning}],
            "parts": [{"name": "Lead", "instrumentId": 30, "tuning": tuning,
                       "strings": 6, "frets": 24, "measures": deepcopy(measures),
                       "automations": {"tempo": [{"measure": 0, "position": 0, "bpm": 120, "type": 4}]}}]}


def note(fret=5, string=0, duration=(1, 4), **extra):
    return {"duration": list(duration), "notes": [{"fret": fret, "string": string, **extra}]}


def bar(*beats, **extra):
    return {"signature": [4, 4], "voices": [{"beats": list(beats)}], **extra}


def cases():
    for direction, shift, placement, count, denominator, grace_count in product(
            ("up", "down"), (0, 50, 100), ("beforeBeat", "onBeat"), (2, 4), (8, 16), (1, 2, 3)):
        params = dict(direction=direction, shift=shift, grace=placement,
                      strings=count, denominator=denominator, graceCount=grace_count)
        chord = {"duration": [1, denominator], "type": denominator,
                 "arpeggio": {"direction": direction, "duration": 86, "shift": shift},
                 "notes": [{"string": i, "fret": 5} for i in range(count)]}
        grace = [{**note(7, i % count, (1, 32)), "type": 32, "graceNote": placement}
                 for i in range(grace_count)]
        doc = envelope([bar({"duration": [1, 4], "rest": True, "notes": [{"rest": True}]},
                            chord, *grace, note(9))])
        yield {"id": "strum-grace/" + "/".join(map(str, params.values())),
               "family": "timing.strum_grace", "parameters": params, "source": doc}
    yield {"id": "basic/attack", "family": "timing.basic",
           "source": envelope([bar(note(5, duration=(1, 1)))])}
    yield {"id": "ties/continuation", "family": "timing.ties",
           "source": envelope([bar(note(5, duration=(1, 1))),
                               bar(note(5, duration=(1, 1), tie=True))])}
    yield {"id": "repeats/two", "family": "timing.repeats",
           "source": envelope([bar(note(5, duration=(1, 1)), repeatStart=True),
                               bar(note(7, duration=(1, 1)), repeat=2)])}
    for feel in ("8th", "16th", "dotted8th", "scottish8th"):
        yield {"id": "swing/" + feel, "family": "timing.swing",
               "source": envelope([bar(*(note(5+i, duration=(1, 8)) for i in range(8)), tripletFeel=feel)])}
    for before in (True, False):
        g = {**note(3, duration=(1, 32)), "graceNote": "beforeBeat" if before else "onBeat", "type": 32}
        yield {"id": "grace/opening/" + str(before), "family": "timing.grace",
               "source": envelope([bar(g, note(7, duration=(1, 1)))])}
    # Relevant pairs and triples, including supported old/new field precedence.
    for direction, shift, tied, placement in product(("up", "down"), (0, 50, 100), (False, True), ("beforeBeat", "onBeat")):
        chord = {"duration": [1, 4], "type": 4,
                 "arpeggio": {"direction": direction, "duration": 30, "shift": shift},
                 "notes": [{"string": i, "fret": 5} for i in range(2)]}
        continuation = deepcopy(chord)
        if tied:
            for n in continuation["notes"]: n["tie"] = True
        g = {**note(7, duration=(1, 32)), "type": 32, "graceNote": placement}
        yield {"id": f"strum-tie-grace/{direction}/{shift}/{tied}/{placement}",
               "family": "timing.strum_tie_grace",
               "source": envelope([bar(note(3), chord, continuation, g, note(9))])}
    for swing, repeat in product(("off", "8th"), (False, True)):
        measures = [bar(*(note(5, duration=(1, 8)) for _ in range(8)),
                        tripletFeel=swing, **({"repeatStart": True} if repeat else {})),
                    bar(*(note(7, duration=(1, 12)) for _ in range(12)), **({"repeat": 2} if repeat else {}))]
        for b in measures[1]["voices"][0]["beats"]: b.update(type=8, tuplet=3)
        doc = envelope(measures)
        doc["parts"][0]["automations"]["tempo"].append({"measure": 1, "position": 0, "bpm": 90, "type": 4})
        yield {"id": f"swing-tuplet-repeat/{swing}/{repeat}", "family": "timing.swing_tuplet_repeat", "source": doc}
    yield {"id": "repeats/alternate-endings", "family": "timing.repeats",
           "source": envelope([bar(note(3, duration=(1, 1)), repeatStart=True),
                               bar(note(5, duration=(1, 1)), repeat=2, alternateEnding=[1]),
                               bar(note(7, duration=(1, 1)), alternateEnding=[2])])}
    for modern in (False, True):
        b = {"duration": [1, 1], "type": 1, "upArpeggio": 4,
             "notes": [{"string": i, "fret": 5} for i in range(3)]}
        if modern: b["arpeggio"] = {"direction": "down", "duration": 100, "shift": 100}
        yield {"id": f"legacy-strum/{modern}", "family": "timing.strum_legacy", "source": envelope([bar(b)])}
