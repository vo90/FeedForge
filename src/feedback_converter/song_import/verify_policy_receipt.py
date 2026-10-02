"""Check a preservation receipt against independently reconstructed evidence."""
import math


def compare(expected, actual, check, path):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(expected) != set(actual):
            check.fail('policy_receipt', path, 'Preservation receipt fields differ from source.')
            return
        for key, value in expected.items():
            compare(value, actual[key], check, path + '/' + key)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            check.fail('policy_receipt', path, 'Preservation receipt entries differ from source.')
            return
        for i, (a, b) in enumerate(zip(expected, actual)):
            compare(a, b, check, path + '/' + str(i))
    elif type(expected) is float:
        if type(actual) not in (int, float) or not math.isfinite(actual) or abs(expected - actual) > 1e-6:
            check.fail('policy_receipt', path, 'Preservation timing differs from source.')
    elif type(expected) is not type(actual) or expected != actual:
        check.fail('policy_receipt', path, 'Preservation value differs from source.')
