"""Compile bounded alternate-ending regions for the production walker."""
from .model import ScoreImportError


def repeat_regions(measures, closes):
    masks = [frozenset() for _ in measures]
    assigned = set()
    for start, end in closes.items():
        markers = [i for i in range(start, end + 1) if measures[i].endings]
        outside = end + 1
        if outside < len(measures) and measures[outside].endings and not measures[outside].repeat_start:
            markers.append(outside)
        if not markers:
            continue
        if markers[0] == start or (not measures[start].repeat_start and markers[0] < end):
            raise ScoreImportError('This ending has no independently established common repeat prefix.')
        if any(a != start and a <= end and b >= start for a, b in closes.items()):
            raise ScoreImportError('Alternate endings inside nested repeats need independent navigation support.')
        count = measures[end].repeat_count
        used = set()
        for pos, index in enumerate(markers):
            passes = measures[index].endings
            if any(type(p) is not int or not 1 <= p <= count for p in passes):
                raise ScoreImportError(f'Invalid ending pass in measure {index + 1}.')
            if index == outside and passes != frozenset({count}):
                raise ScoreImportError('An ending outside the repeat must be its final pass.')
            if used.intersection(passes):
                raise ScoreImportError('A repeat pass has multiple disjoint ending regions.')
            used.update(passes)
            stop = min(end + 1, markers[pos + 1] if pos + 1 < len(markers) else end + 1)
            for bar in range(index, stop):
                masks[bar] = passes
            assigned.add(index)
        if used != set(range(1, count + 1)):
            raise ScoreImportError('Alternate endings do not cover every repeat pass.')
    if any(bar.endings and i not in assigned for i, bar in enumerate(measures)):
        raise ScoreImportError('An alternate ending has no definite owning repeat.')
    return masks
