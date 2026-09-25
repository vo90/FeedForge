"""Independent comparison of source-derived delayed harmonic contacts."""


def check_changes(wanted, actual, check, location):
    if wanted is None and actual is None: return
    if not isinstance(wanted, dict) or not isinstance(actual, dict):
        check.fail('harmonic_changes', location, 'Missing or invented delayed harmonic contact.')
        return
    check.equal('harmonic_changes', location, ['events','version'], sorted(actual))
    check.equal('harmonic_changes', location+'/version', 1, actual.get('version'))
    events = actual.get('events')
    if not isinstance(events, list):
        check.fail('harmonic_changes', location, 'Contact events must be an array.')
        return
    check.equal('harmonic_changes', location+'/count', len(wanted['events']), len(events))
    for i, (a,b) in enumerate(zip(wanted['events'],events)):
        loc=location+f'/{i}'
        if not isinstance(b,dict):
            check.fail('harmonic_changes',loc,'Invalid contact event.')
            continue
        check.equal('harmonic_changes',loc,sorted(a),sorted(b))
        for key in ('start','end'):check.near('harmonic_contact_time',loc+'/'+key,a[key],b.get(key))
        for key in ('target','source_id'):check.equal('harmonic_contact_target',loc+'/'+key,a[key],b.get(key))
