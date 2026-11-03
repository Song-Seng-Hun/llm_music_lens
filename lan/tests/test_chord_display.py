"""Chord display (worker 0.10.0): key spelling and slash bass on chord rows, and the server's check of the field."""
import json
import unittest

from helpers import server
from test_rhythm import RHYTHM_OUT_V40, rw


def show(label, tonic="C", scale="major", notes=(), sections=()):
    row = {"start": 0.0, "end": 2.0, "label": label, "strength": None}
    rw.add_chord_display([row], list(sections), tonic, scale, list(notes))
    return row


def bass(midi, start=0.0, end=2.0):
    return {"start": start, "end": end, "midi": midi, "note": rw.note_name(midi)}


class SpellingTest(unittest.TestCase):
    def test_keys(self):
        for label, tonic, scale, want in (("A#", "F", "major", "Bb"), ("A#", "D", "minor", "Bb"), ("A#", "B", "major", "A#"),
                                          ("A#7", "C", "major", "Bb7"), ("F#m", "C", "major", "F#m"), ("D#maj7", "A", "minor", "Ebmaj7"),
                                          ("G#m7b5", "E", "major", "G#m7b5"), ("C#mMaj7", "Ab", "major", "DbmMaj7"), ("Bb", "E", "major", "A#")):
            self.assertEqual(show(label, tonic, scale)["display"], want, (label, tonic, scale))

    def test_label_stays(self):
        self.assertEqual(show("A#", "F")["label"], "A#")

    def test_no_chord(self):
        for label in ("N", "X"):
            self.assertEqual(show(label, notes=[bass(40)])["display"], label)

    def test_section_at_the_middle_wins(self):
        sections = [{"start": 0.0, "end": 1.5, "tonic": "F", "scale": "major"}, {"start": 1.5, "end": 3.0, "tonic": "E", "scale": "major"}]
        self.assertEqual(show("A#", "E", sections=sections)["display"], "Bb")

    def test_unknown_tonic(self):
        self.assertEqual(show("A#", "?")["display"], "A#")


class SlashTest(unittest.TestCase):
    def test_chord_tone(self):
        self.assertEqual(show("C", notes=[bass(40)])["display"], "C/E")
        self.assertEqual(show("Am", notes=[bass(48)])["display"], "Am/C")
        self.assertEqual(show("A#m7", "F", notes=[bass(49)])["display"], "Bbm7/Db")

    def test_no_slash(self):
        for notes in ((), [bass(36)], [bass(38)], [bass(40, 0.0, 0.5)]):  # none, root, not a chord tone, too short
            self.assertEqual(show("C", notes=notes)["display"], "C", notes)

    def test_split_notes_add_up(self):
        self.assertEqual(show("C", notes=[bass(40, 0.0, 0.6), bass(52, 1.0, 1.5)])["display"], "C/E")

    def test_most_sounding_note_decides(self):
        self.assertEqual(show("C", notes=[bass(36, 0.0, 0.9), bass(40, 0.9, 2.0)])["display"], "C/E")
        self.assertEqual(show("C", notes=[bass(40, 0.0, 0.9), bass(36, 0.9, 2.0)])["display"], "C")

    def test_note_outside_the_segment(self):
        self.assertEqual(show("C", notes=[bass(40, 2.0, 4.0)])["display"], "C")

    def test_every_quality_has_tones(self):
        for q in ("", "m", "dim", "aug", "m6", "6", "m7", "mMaj7", "maj7", "7", "dim7", "m7b5", "sus2", "sus4"):
            self.assertIn(q, rw.CHORD_TONES)


class ValidatorTest(unittest.TestCase):
    def rows(self, display):
        v = json.loads(json.dumps(RHYTHM_OUT_V40))
        for c in v["chords"]:
            c["display"] = display
        return v

    def test_passes(self):
        v = self.rows("Bbm7/Db")
        self.assertIs(server.check_rhythm(v), v)

    def test_refused(self):
        for display in (None, 3, "", "x" * 25):
            self.assertIsNone(server.check_rhythm(self.rows(display)), display)


if __name__ == "__main__":
    unittest.main()
