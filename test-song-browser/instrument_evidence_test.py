"""Offline regression checks; no song files, codecs or external services needed."""
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "src/feedback_converter/instrument_evidence.py"
spec = importlib.util.spec_from_file_location("instrument_evidence", SOURCE)
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class InstrumentEvidenceTest(unittest.TestCase):
    def test_padded_bass_slots_and_unused_strings_never_become_exact_count(self):
        chart = {"tuning": [0, 0, 0, 0, 0, 0], "notes": [{"s": 0}, {"s": 3}]}
        result = evidence.psarc_instrument_evidence("songs/bin/generic/song_bass.sng", {"arrangement_names": {"song_bass": "Bass"}}, chart)
        self.assertEqual(result["instrument_family"], "bass")
        self.assertEqual(result["instrument_family_evidence"], "explicit")
        self.assertEqual(result["minimum_used_strings"], 4)
        self.assertIsNone(result["string_count"])
        self.assertEqual(result["string_count_evidence"], "unknown")

    def test_filename_is_not_authoritative_family_metadata(self):
        result = evidence.psarc_instrument_evidence("songs/bin/generic/song_bass.sng", {}, {"notes": [{"s": 4}]})
        self.assertIsNone(result["instrument_family"])
        self.assertIsNone(result["string_count"])
        self.assertEqual(result["minimum_used_strings"], 5)

    def test_minimum_usage_includes_chord_notes_but_not_tuning_slots_or_unused_templates(self):
        chart = {"tuning": [0] * 6, "notes": [{"s": 0}], "templates": [{"notes": [{"s": 5}]}], "chords": [{"notes": [{"s": 4}]}]}
        self.assertEqual(evidence.minimum_used_strings(chart), 5)
        self.assertIsNone(evidence.minimum_used_strings({"tuning": [0] * 6, "notes": []}))

    def test_feedpak_only_uses_explicit_manifest_count_and_family(self):
        unknown = evidence.feedpak_instrument_evidence({"type": "bass", "tuning": [0] * 6}, {"notes": [{"s": 3}]})
        self.assertEqual(unknown["instrument_family"], "bass")
        self.assertIsNone(unknown["string_count"])
        explicit = evidence.feedpak_instrument_evidence({"type": "bass", "string_count": 5}, {"notes": [{"s": 3}]})
        self.assertEqual(explicit["string_count"], 5)
        self.assertEqual(explicit["string_count_evidence"], "explicit")
        self.assertIsNone(evidence.feedpak_instrument_evidence({}, {})["instrument_family"])
        self.assertIsNone(evidence.feedpak_instrument_evidence({"type": "bass", "instrument_family": "guitar"}, {})["instrument_family"])
        self.assertIsNone(evidence.feedpak_instrument_evidence({"string_count": 5, "string_count_evidence": "filename_hint"}, {})["string_count"])
        self.assertIsNone(evidence.feedpak_instrument_evidence({"string_count": 4}, {"notes": [{"s": 4}]})["string_count"])

    def test_full_stem_label_does_not_verify_backing_audio(self):
        result = evidence.backing_evidence({"stems": [{"id": "full", "file": "stems/full.ogg"}]})
        self.assertEqual(result, {"backing_track": None, "backing_track_evidence": "unknown"})
        self.assertEqual(evidence.backing_evidence({"backing_track": "no-bass", "backing_track_evidence": "filename_hint"}), result)
        self.assertEqual(evidence.backing_evidence({"backing_track": "no-bass", "backing_track_evidence": "explicit"})["backing_track"], "no-bass")


if __name__ == "__main__":
    unittest.main()
