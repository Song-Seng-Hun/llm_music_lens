"""Part activity (worker 0.13.0): the worker's levels and rows, and the server's field."""
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

from helpers import REPO, server
import test_other
from test_other import BASS, out
from test_rhythm import ESSENTIA_PY, RHYTHM_OUT_V40, WAV, rw
from test_separation import FULL

SECTIONS = RHYTHM_OUT_V40["sections"]
SECTION = SECTIONS[0]
ROW = {"start": SECTION["start"], "end": SECTION["end"], "label": SECTION["label"], "vocals": 1.0, "bass": 0.5, "drums": None, "bass_notes_per_s": 1.5,
       "other_notes_per_s": None, "drum_hits_per_s": 0.0}
PARTS = {"status": "done", "method": rw.PART_METHOD, "reliability": "reference_candidate", "window_s": 2.0, "threshold_db": -15,
         "sections": [dict(ROW, start=s["start"], end=s["end"], label=s["label"]) for s in SECTIONS]}


def stored(parts):
    return dict(RHYTHM_OUT_V40, parts=parts)


class PartRowsTest(unittest.TestCase):
    def test_share_uses_window_midpoints(self):
        self.assertEqual(rw._active_share([True, False, True, True], 0.0, 4.0), 0.5)
        self.assertEqual(rw._active_share([True, False, True, True], 4.0, 8.0), 1.0)

    def test_short_section_uses_the_window_holding_it(self):
        self.assertEqual(rw._active_share([True, False], 0.1, 0.5), 1.0)
        self.assertEqual(rw._active_share([True, False], 2.1, 2.5), 0.0)

    def test_section_past_the_stem_is_off(self):
        self.assertEqual(rw._active_share([True], 10.1, 10.5), 0.0)

    def test_missing_layer_is_none(self):
        self.assertIsNone(rw._active_share(None, 0.0, 2.0))
        self.assertIsNone(rw._rate(None, 0.0, 2.0))

    def test_rate_counts_starts_in_the_section(self):
        self.assertEqual(rw._rate([0.0, 1.0, 2.0], 0.0, 2.0), 1.0)

    def test_field_follows_layer_status(self):
        sep = {"parts": (None, {"vocals": [True, True], "bass": [True, False], "drums": None})}
        skipped = {"status": "skipped", "reason": "too_many_notes"}
        one = {"start": 0.0, "end": 4.0, "label": "A"}
        p = rw.parts_field(sep, [one], BASS, [{"start": 0.5}], skipped, [], None, [])
        self.assertEqual(p["sections"], [dict(one, vocals=1.0, bass=0.5, drums=None, bass_notes_per_s=0.25,
                                              other_notes_per_s=None, drum_hits_per_s=None)])
        self.assertEqual((p["status"], p["reliability"], p["window_s"], p["threshold_db"]), ("done", "reference_candidate", 2.0, -15))

    def test_field_without_separation(self):
        self.assertIsNone(rw.parts_field(None, [], None, [], None, [], None, []))
        self.assertIsNone(rw.parts_field({}, [], None, [], None, [], None, []))
        field = {"status": "skipped", "reason": "canonical_16k"}
        self.assertEqual(rw.parts_field({"parts": (field, None)}, [], None, [], None, [], None, []), field)


class PartValidatorTest(unittest.TestCase):
    def test_passes(self):
        self.assertIsNotNone(server.check_rhythm(json.loads(json.dumps(stored(PARTS)))))
        self.assertIsNotNone(server.check_rhythm(json.loads(json.dumps(stored({"status": "skipped", "reason": "levels_failed"})))))

    def test_method_fits(self):
        self.assertLessEqual(len(rw.PART_METHOD), 200)

    def test_refused(self):
        rest = PARTS["sections"][1:]
        for bad in ("x", {"status": "skipped", "reason": "other"}, {"status": "skipped"}, dict(PARTS, reliability="low_reference"),
                    dict(PARTS, extra=1), dict(PARTS, sections=[]), dict(PARTS, sections=[dict(ROW, vocals=1.5)] + rest),
                    dict(PARTS, sections=[dict(ROW, drum_hits_per_s=-1)] + rest), dict(PARTS, sections=[dict(ROW, start=99.0)] + rest),
                    dict(PARTS, sections=[dict(ROW, label="Z")] + rest), dict(PARTS, sections=[{k: v for k, v in ROW.items() if k != "bass"}] + rest)):
            self.assertIsNone(server.check_rhythm(json.loads(json.dumps(stored(bad)))), bad)


class PartServerTest(test_other.OtherServerTest):
    test_summary_and_timeline = test_not_stored = test_reuse = None  # the parent's own tests

    def test_summary(self):
        self.fw.write(stdout=json.dumps(dict(out(), parts=PARTS)))
        self.assertEqual(self.post(WAV)["parts"], PARTS)

    def test_not_stored_is_null(self):
        self.fw.write(stdout=json.dumps(out()))
        self.assertIsNone(self.post(WAV)["parts"])

    def test_failed_levels_recompute(self):
        for parts, hit in (({"status": "skipped", "reason": "levels_failed"}, False), (PARTS, True)):
            with open(self.rhythm_file(), "w") as f:
                f.write(json.dumps(dict(out(), parts=parts, **self.ids())))
            self.fw.write(stdout=json.dumps(dict(out(), parts=PARTS)))
            self.assertEqual(self.post(FULL)["cache_hit"], hit, parts)


DRIVER = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import rhythm_worker as rw
sep = {"scratch": sys.argv[2]}
rw.parts_input(sep, json.loads(sys.argv[3]))
print(json.dumps(sep["parts"]))
'''
ANALYZE = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import rhythm_worker as rw
mode, wav, scratch = sys.argv[2:5]
sep = {"source": None}
if mode == "stems":
    sep = {"source": wav, "scratch": scratch}
    rw.vocal_pitch = lambda s, n: (None, None, None)
v = rw.analyze(wav, sep=sep)
print(json.dumps({"parts": v.get("parts"), "warnings": v["warnings"], "n": len(v["sections"]), "scratch": os.path.exists(scratch)}))
'''


def tone(path, amps, rate=1000, channels=1, width=2):
    """A WAV holding a square wave of amplitude amps[i] in each 2 s window."""
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        for a in amps:
            for i in range(2 * rate):
                v = a if i % 2 else -a
                w.writeframes(struct.pack("<h", v) * channels if width == 2 else b"\0" * width * channels)


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class PartLevelsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mlparts-")
        os.makedirs(os.path.join(self.d, "full"))
        self.driver = os.path.join(self.d, "driver.py")
        with open(self.driver, "w") as f:
            f.write(DRIVER)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_parts(self, vocal_reason=None):
        p = subprocess.run([ESSENTIA_PY, "-I", self.driver, REPO, self.d, json.dumps(vocal_reason)], capture_output=True, timeout=60,
                           env=self.env())
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout)

    def env(self):
        return {"PATH": server.CHILD_ENV_PATH, "HOME": self.d, "LANG": "en_US.UTF-8"}

    def wav(self, name):
        return os.path.join(self.d, name)

    def run_analyze(self, mode):
        """The whole worker on 6 s of 16 kHz tone: wiring from melody_input to the output field."""
        song = os.path.join(tempfile.mkdtemp(dir=self.d), "song.wav")
        tone(song, [8000, 8000, 8000], rate=16000)
        drv = self.wav("analyze.py")
        with open(drv, "w") as f:
            f.write(ANALYZE)
        p = subprocess.run([ESSENTIA_PY, "-I", drv, REPO, mode, song, self.d], capture_output=True, timeout=120, env=self.env())
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout)

    def test_analyze_canonical_16k(self):
        v = self.run_analyze("canonical")
        self.assertEqual(v["parts"], {"status": "skipped", "reason": "canonical_16k"})
        self.assertNotIn("parts_failed: levels_failed", v["warnings"])

    def test_analyze_levels_failed_warns_and_clears_the_scratch(self):
        tone(self.wav("full.wav"), [10000])
        v = self.run_analyze("stems")
        self.assertEqual(v["parts"], {"status": "skipped", "reason": "levels_failed"})
        self.assertIn("parts_failed: levels_failed", v["warnings"])
        self.assertFalse(v["scratch"])

    def test_analyze_gives_one_row_per_section(self):
        tone(self.wav("full.wav"), [10000])
        tone(self.wav("full/vocals.wav"), [10000])
        v = self.run_analyze("stems")
        self.assertEqual((v["parts"]["status"], len(v["parts"]["sections"])), ("done", v["n"]))
        self.assertEqual({r["vocals"] for r in v["parts"]["sections"]} - {0.0, 1.0}, set())

    def test_threshold_against_the_mix(self):
        tone(self.wav("full.wav"), [10000, 10000, 10000])
        tone(self.wav("full/vocals.wav"), [10000, 1000, 0], channels=2)  # 0, -20 dB and silence against the mix
        tone(self.wav("full/bass.wav"), [1800, 1700, 0])  # either side of -15 dB (1778)
        self.assertEqual(self.run_parts(), [None, {"vocals": [True, False, False], "bass": [True, False, False], "drums": None}])

    def test_silent_mix_is_all_off(self):
        tone(self.wav("full.wav"), [0, 0])
        tone(self.wav("full/drums.wav"), [0, 0])
        self.assertEqual(self.run_parts()[1]["drums"], [False, False])

    def test_no_mix(self):
        self.assertEqual(self.run_parts(), [{"status": "skipped", "reason": "separation_failed"}, None])

    def test_no_stems(self):
        tone(self.wav("full.wav"), [10000])
        self.assertEqual(self.run_parts(), [{"status": "skipped", "reason": "levels_failed"}, None])

    def test_no_stems_keeps_the_separation_reason(self):
        tone(self.wav("full.wav"), [10000])
        self.assertEqual(self.run_parts("separation_failed"), [{"status": "skipped", "reason": "separation_failed"}, None])

    def test_unreadable_stem_is_none(self):
        tone(self.wav("full.wav"), [10000])
        tone(self.wav("full/bass.wav"), [10000])
        with open(self.wav("full/vocals.wav"), "w") as f:
            f.write("not a wav")
        self.assertEqual(self.run_parts()[1], {"vocals": None, "bass": [True], "drums": None})


if __name__ == "__main__":
    unittest.main()
