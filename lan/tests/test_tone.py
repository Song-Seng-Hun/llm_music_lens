"""Part tone (worker 0.19.0): the worker's measures and the server's field."""
import json
import math
import os
import tempfile
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from helpers import server
from test_rhythm import RHYTHM_OUT_V40, rw
from test_stereo import SECTIONS, SR, noise, write

KEYS = ("vocals_brightness_hz", "drums_brightness_hz", "other_brightness_hz", "kick_weight_db", "snare_brightness_hz")


def sine(hz, seconds):
    return np.sin(2 * np.pi * hz * np.arange(int(SR * seconds)) / SR)


def measures(x, hits=()):
    t = rw.tone_frames(x, SR)
    return {"frames": {s: t for s in rw.TONE_PARTS},
            "hits": {c: rw.tone_hit_rows(x, SR, [h for h, k in hits if k == c], c) for c in ("kick", "snare")}}


def rows_for(seconds, level=1.0):
    return [[level, level, level / 2]] * seconds


def stored_m(x, seconds, hits=(), part_level=1.0):
    parts = {s: rows_for(seconds, part_level) for s in rw.STEREO_PARTS}
    return {"mix": rows_for(seconds), "parts": parts, "tone": measures(x, hits)}


@unittest.skipUnless(np, "needs numpy")
class MeasureTest(unittest.TestCase):
    def test_brightness_of_a_tone(self):
        self.assertAlmostEqual(rw._tone_value(rw.tone_frames(sine(440, 4) * 0.1, SR), 0.0, 4.0), 440, delta=8)

    def test_gain_does_not_change_it(self):
        x = noise(4) * 0.1
        self.assertAlmostEqual(rw._tone_value(rw.tone_frames(x, SR), 0.0, 4.0), rw._tone_value(rw.tone_frames(x * 0.1, SR), 0.0, 4.0), delta=0.11)

    def test_quiet_frames_do_not_count(self):
        x = np.concatenate([sine(440, 4) * 0.5, sine(5000, 4) * 0.0001])
        self.assertAlmostEqual(rw._tone_value(rw.tone_frames(x, SR), 0.0, 8.0), 440, delta=8)

    def test_section_bounds_and_silence(self):
        x = np.concatenate([sine(440, 4), sine(3000, 4)]) * 0.1
        f = rw.tone_frames(x, SR)
        self.assertAlmostEqual(rw._tone_value(f, 5.0, 7.0), 3000, delta=20)
        self.assertIsNone(rw._tone_value(f, 20.0, 21.0))
        self.assertIsNone(rw._tone_value(rw.tone_frames(np.zeros(SR * 2), SR), 0.0, 2.0))
        self.assertEqual(rw.tone_frames(np.zeros(100), SR)[0].size, 0)

    def test_kick_weight_and_snare(self):
        hit = int(rw.TONE_HIT_S * SR)
        low, bright = np.zeros(SR * 10), np.zeros(SR * 10)
        times = [1.0 + i for i in range(6)]
        n = noise(rw.TONE_HIT_S)
        for t in times:
            i = int(t * SR)
            low[i:i + hit] = sine(60, rw.TONE_HIT_S)
            bright[i:i + hit] = n
        w = rw._hit_median(rw.tone_hit_rows(low, SR, times, "kick"), 0.0, 10.0)
        self.assertGreater(w, -1)
        self.assertLess(rw._hit_median(rw.tone_hit_rows(bright, SR, times, "kick"), 0.0, 10.0), -15)
        self.assertGreater(rw._hit_median(rw.tone_hit_rows(bright, SR, times, "snare"), 0.0, 10.0), rw._hit_median(rw.tone_hit_rows(low, SR, times, "snare"), 0.0, 10.0) + 3)

    def test_hits_need_a_count_and_fit_the_section(self):
        rows = [(1.0 + i, -1.0) for i in range(5)]
        self.assertEqual(rw._hit_median(rows, 0.0, 9.0), -1.0)
        self.assertIsNone(rw._hit_median(rows[:4], 0.0, 9.0))
        self.assertIsNone(rw._hit_median(rows, 0.0, 5.05))  # the last hit's 120 ms runs past the end
        self.assertEqual(rw.tone_hit_rows(np.zeros(SR), SR, [0.99, -1.0], "kick"), [])

    def test_row(self):
        x = sine(440, 10) * 0.1
        hits = [(1.0 + i, "kick") for i in range(6)]
        row = rw.tone_row({"start": 0.0, "end": 10.0, "label": "A"}, stored_m(x, 10, hits))
        self.assertAlmostEqual(row["vocals_brightness_hz"], 440, delta=8)
        self.assertIsNotNone(row["kick_weight_db"])
        self.assertIsNone(row["snare_brightness_hz"])

    def test_quiet_part_and_silence_section_have_no_values(self):
        m = stored_m(sine(440, 10) * 0.1, 10, [(1.0 + i, "kick") for i in range(6)], part_level=0.01)
        row = rw.tone_row({"start": 0.0, "end": 10.0, "label": "A"}, m)
        self.assertTrue(all(row[k] is None for k in KEYS))
        m = stored_m(sine(440, 10) * 0.1, 10, [(1.0 + i, "kick") for i in range(6)])
        row = rw.tone_row({"start": 0.0, "end": 10.0, "label": rw.SILENCE_LABEL}, m)
        self.assertTrue(all(row[k] is None for k in KEYS))

    def test_measures_read_the_stems(self):
        with tempfile.TemporaryDirectory() as d:
            for s in rw.TONE_PARTS:
                x = noise(4, 3) * 0.1
                write(os.path.join(d, s + ".wav"), np.stack([x, x], 1))
            m = rw.tone_measures(d, [{"time": 1.0, "class": "kick"}, {"time": 1.5, "class": "snare"}, {"time": 2.0, "class": "tom"}])
        self.assertEqual(set(m["frames"]), set(rw.TONE_PARTS))
        self.assertEqual((len(m["hits"]["kick"]), len(m["hits"]["snare"])), (1, 1))


class FieldTest(unittest.TestCase):
    def field(self, stereo, sections=None):
        return rw.tone_field({"stereo": stereo}, sections or [{"start": 0.0, "end": 1.0, "label": "A"}])

    def test_absent_without_separation(self):
        self.assertIsNone(rw.tone_field(None, []))
        self.assertIsNone(rw.tone_field({}, []))

    def test_skips_follow_the_stereo_run(self):
        skip = {"status": "skipped", "reason": "mono_source"}
        self.assertEqual(self.field((skip, skip, None)), skip)
        failed = {"status": "skipped", "reason": "separation_failed"}
        self.assertEqual(self.field((None, failed, {"mix": [], "parts": None, "tone": None})), failed)

    def test_failed_measures(self):
        self.assertEqual(self.field((None, None, {"mix": [], "parts": {}, "tone": None})), {"status": "skipped", "reason": "tone_failed"})


def done_field():
    x = sine(440, 400) * 0.1
    return rw.tone_field({"stereo": (None, None, stored_m(x, 400, [(1.0 + i, "kick") for i in range(6)]))}, SECTIONS)


@unittest.skipUnless(np, "needs numpy")
class ServerTest(unittest.TestCase):
    def stored(self, **fields):
        return json.loads(json.dumps(dict(RHYTHM_OUT_V40, **fields)))

    def test_accepts_the_worker_field(self):
        v = self.stored(tone=done_field())
        self.assertIsNotNone(server.check_rhythm(v))
        self.assertEqual(server.rhythm_summary(v)["tone"]["reliability"], "low_reference")

    def test_older_results_stay_valid(self):
        v = self.stored()
        self.assertIsNotNone(server.check_rhythm(v))
        self.assertIsNone(server.rhythm_summary(v)["tone"])

    def test_skipped(self):
        for reason in ("mono_source", "tone_failed", "canonical_16k"):
            self.assertIsNotNone(server.check_rhythm(self.stored(tone={"status": "skipped", "reason": reason})))
        self.assertIsNone(server.check_rhythm(self.stored(tone={"status": "skipped", "reason": "odd"})))

    def test_rejects_bad_fields(self):
        def bad(change):
            v = self.stored(tone=done_field())
            change(v)
            self.assertIsNone(server.check_rhythm(json.loads(json.dumps(v))))
        bad(lambda v: v["tone"]["sections"].pop())
        bad(lambda v: v["tone"]["sections"][0].update(vocals_brightness_hz=5))
        bad(lambda v: v["tone"]["sections"][0].update(kick_weight_db=1))
        bad(lambda v: v["tone"]["sections"][0].update(extra=1))
        bad(lambda v: v["tone"]["sections"][0].pop("snare_brightness_hz"))
        bad(lambda v: v["tone"].update(reliability="reference_candidate"))

    def test_retry_and_stem_layer(self):
        self.assertTrue(server._stereo_can_improve({"tone": {"status": "skipped", "reason": "tone_failed"}}, True))
        self.assertFalse(server._stereo_can_improve({"tone": {"status": "skipped", "reason": "mono_source"}}, True))
        self.assertTrue(server._has_separated_layer({"tone": done_field()}))


if __name__ == "__main__":
    unittest.main()
