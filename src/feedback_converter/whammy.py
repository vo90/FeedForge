"""Versioned signed bar-expression wire data. Times are note-relative seconds."""
from copy import deepcopy
import math

EPSILON = 0.0000011


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def valid_whammy(value, sustain):
    if (not number(sustain) or sustain < 0 or not isinstance(value, dict)
            or set(value) != {"version", "policy", "segments"}
            or type(value["version"]) is not int or value["version"] != 1 or value["policy"] != "optional"
            or not isinstance(value["segments"], list) or not 1 <= len(value["segments"]) <= 20000):
        return False
    previous = 0
    for segment in value["segments"]:
        required = {"start", "end", "source_id", "group", "curve"}
        if (not isinstance(segment, dict) or not required <= set(segment) <= required | {"vibrato"}
                or not all(number(segment[k]) for k in ("start", "end"))
                or segment["start"] < -EPSILON or segment["start"] < previous - EPSILON
                or not segment["start"] < segment["end"] <= sustain + EPSILON
                or any(not isinstance(segment[k], str) or not 1 <= len(segment[k]) <= 256 for k in ("source_id", "group"))
                or segment.get("vibrato") not in (None, "slight", "wide")
                or not isinstance(segment["curve"], list) or len(segment["curve"]) > 2048
                or not segment["curve"] and not segment.get("vibrato")):
            return False
        previous = segment["end"]
        last = -math.inf
        for point in segment["curve"]:
            if (not isinstance(point, dict) or set(point) != {"t", "v"}
                    or not all(number(point[k]) for k in ("t", "v")) or not -16 <= point["v"] <= 8
                    or not segment["start"] - EPSILON <= point["t"] <= segment["end"] + EPSILON
                    or point["t"] <= last):
                return False
            last = point["t"]
    return True


def retime_whammy(value, original, sustain, mapped_start, map_at, boundaries=()):
    if not valid_whammy(value, sustain):
        raise ValueError("Invalid whammy expression.")
    result = deepcopy(value)
    for segment in result["segments"]:
        curve = []
        for point in segment["curve"]:
            if curve:
                left = curve[-1]
                for absolute in boundaries:
                    when = float(absolute) - original
                    if left["t"] + 1e-9 < when < point["t"] - 1e-9:
                        ratio = (when - left["t"]) / (point["t"] - left["t"])
                        curve.append({"t": when, "v": left["v"] + ratio * (point["v"] - left["v"])})
            curve.append(point)
        segment["curve"] = [{"t": round(map_at(original + p["t"]) - mapped_start, 6), "v": p["v"]} for p in curve]
        for key in ("start", "end"):
            segment[key] = round(map_at(original + segment[key]) - mapped_start, 6)
    if not valid_whammy(result, round(map_at(original + sustain) - mapped_start, 6)):
        raise ValueError("Whammy cannot be represented at microsecond timing precision.")
    return result


def trim_whammy(value, shortened):
    """Allow only an already constant held tail, never delete a later gesture."""
    result = deepcopy(value)
    for segment in result["segments"]:
        if segment["end"] <= shortened + EPSILON:
            continue
        if segment["start"] >= shortened:
            raise ValueError("Audio ends before an authored bar gesture.")
        before = [p for p in segment["curve"] if p["t"] <= shortened]
        after = [p for p in segment["curve"] if p["t"] > shortened]
        if after and (not before or any(abs(p["v"] - before[-1]["v"]) > 1e-10 for p in after)):
            raise ValueError("Audio ends during a changing bar gesture.")
        if after:
            segment["curve"] = before + ([{**before[-1], "t": shortened}] if before[-1]["t"] < shortened else [])
        segment["end"] = shortened
    return result
