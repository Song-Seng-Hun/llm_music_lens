"""Mix and stereo (worker 0.17.0): the worker's measures and the server's fields."""
import json
import math
import os
import tempfile
import unittest
import wave

try:
    import numpy as np
except ImportError:
    np = None

from helpers import server
from test_rhythm import ESSENTIA_PY, RHYTHM_OUT_V40, rw

SR = 44100
SECTIONS = RHYTHM_OUT_V40["sections"]


def write(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def noise(seconds, seed=0):
    return np.random.default_rng(seed).standard_normal(int(SR * seconds))


@unittest.skipUnless(np, "needs numpy")
class MeasureTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.path = os.path.join(self.d.name, "a.wav")

    def energy(self, x, bands=True):
        write(self.path, x)
        return rw.stereo_energy(self.path, bands=bands)

    def test_balance(self):
        c = noise(4) * 0.05
        for pan in (-0.7, 0.0, 0.5):
            rows = self.energy(np.stack([c * math.sqrt(1 - pan), c * math.sqrt(1 + pan)], 1))
            row = rw.mix_row({"start": 0.0, "end": 4.0, "label": "A"}, rows)
            self.assertAlmostEqual(row["balance"], pan, delta=0.002)

    def test_width(self):
        a, b = noise(4, 1) * 0.05, noise(4, 2)
        b -= a * (a @ b) / (a @ a)
        for want in (-12.0, 0.0, 6.0):
            side = b * math.sqrt((a @ a) / (b @ b)) * 10 ** (want / 20)
            row = rw.mix_row({"start": 0.0, "end": 4.0, "label": "A"}, self.energy(np.stack([a + side, a - side], 1)))
            self.assertAlmostEqual(row["width_db"], want, delta=0.05)

    def test_bands(self):
        t = np.arange(SR * 4) / SR
        tone = lambda hz: np.sin(2 * np.pi * hz * t)
        mid = tone(100) + tone(1000) + tone(5000)
        side = tone(100.5) * 0.1 + tone(1000.5) * 0.5 + tone(5000.5) * math.sqrt(2)
        row = rw.mix_row({"start": 0.0, "end": 4.0, "label": "A"}, self.energy(np.stack([mid + side, mid - side], 1) * 0.05))
        self.assertAlmostEqual(row["low_width_db"], -20.0, delta=1.5)
        self.assertAlmostEqual(row["mid_width_db"], -6.0, delta=1.5)
        self.assertAlmostEqual(row["high_width_db"], 3.0, delta=1.5)

    def test_silence_and_mono(self):
        row = rw.mix_row({"start": 0.0, "end": 2.0, "label": "A"}, self.energy(np.zeros((SR * 2, 2))))
        self.assertEqual((row["balance"], row["width_db"], row["low_width_db"]), (None, None, None))
        c = noise(2) * 0.05
        row = rw.mix_row({"start": 0.0, "end": 2.0, "label": "A"}, self.energy(np.stack([c, c], 1)))
        self.assertEqual((row["balance"], row["width_db"]), (0.0, -40.0))

    def test_silence_section_has_no_values(self):
        loud = self.energy(np.stack([noise(2, 4), noise(2, 5)], 1) * 0.1)
        sec = {"start": 0.0, "end": 2.0, "label": rw.SILENCE_LABEL}
        row = rw.mix_row(sec, loud)
        self.assertEqual({k: row[k] for k in ("label", "balance", "width_db", "low_width_db", "mid_width_db", "high_width_db")},
                         {"label": rw.SILENCE_LABEL, "balance": None, "width_db": None, "low_width_db": None, "mid_width_db": None, "high_width_db": None})
        parts = {s: self.energy(np.stack([noise(2, 4), noise(2, 5)], 1) * 0.1, bands=False) for s in rw.STEREO_PARTS}
        row = rw.position_row(sec, loud, parts)
        self.assertTrue(all(row[s] is None for s in rw.STEREO_PARTS))
        self.assertEqual(row["end"], 2.0)

    def test_short_section_and_past_the_end(self):
        rows = self.energy(np.stack([noise(2), noise(2, 3)], 1) * 0.05)
        self.assertIsNotNone(rw.mix_row({"start": 0.1, "end": 0.2, "label": "A"}, rows)["balance"])
        self.assertIsNone(rw.mix_row({"start": 9.0, "end": 9.2, "label": "A"}, rows)["balance"])

    def test_position_threshold(self):
        loud = self.energy(np.stack([noise(2, 4), noise(2, 5)], 1) * 0.1, bands=False)
        quiet = [[v * 0.01 for v in r] for r in loud]  # -20 dB
        parts = {"vocals": loud, "bass": quiet, "drums": [[0.0, 0.0, 0.0]] * 2, "other": loud}
        row = rw.position_row({"start": 0.0, "end": 2.0, "label": "A"}, loud, parts)
        self.assertEqual(row["vocals"]["level_db"], 0.0)
        self.assertIsNone(row["bass"])
        self.assertIsNone(row["drums"])



class FieldTest(unittest.TestCase):
    def fields(self, stereo):
        return rw.stereo_fields({"stereo": stereo}, [{"start": 0.0, "end": 1.0, "label": "A"}])

    def test_absent_without_separation(self):
        self.assertEqual(rw.stereo_fields(None, []), (None, None))
        self.assertEqual(rw.stereo_fields({}, []), (None, None))

    def test_skipped_passes_through(self):
        skip = {"status": "skipped", "reason": "canonical_16k"}
        self.assertEqual(self.fields((skip, skip, None)), (skip, skip))

    def test_mix_survives_a_failed_second_run(self):
        skip = {"status": "skipped", "reason": "separation_failed"}
        row = [1.0, 1.0, 0.5] + [1.0, 0.0] * 3
        mix, pos = self.fields((None, skip, {"mix": [row], "parts": None, "lufs": -10.0, "lra": 3.0}))
        self.assertEqual(mix["status"], "done")
        self.assertEqual(pos, skip)

    def test_input_skips_without_full_wav(self):
        with tempfile.TemporaryDirectory() as d:
            sep = {"scratch": d, "source": "x"}
            rw.stereo_input(sep, "band_limited")
        self.assertEqual(sep["stereo"][0], {"status": "skipped", "reason": "band_limited"})


FAKE_DEMUCS = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2;; -n|-b|--seed) shift 2;; *) src="$1"; shift;; esac
done
[ -n "$FAKE_DEMUCS_FAIL" ] && exit 1
d="$out/$(basename "$src" .wav)"
mkdir -p "$d"
for s in vocals bass drums other; do /bin/cp "$src" "$d/$s.wav"; done
"""


@unittest.skipUnless(np, "needs numpy")
class InputTest(unittest.TestCase):
    """stereo_input with the real afconvert and a fake Demucs that copies its input to all four stems."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.addCleanup(os.environ.pop, "FAKE_DEMUCS_FAIL", None)
        self.demucs = os.path.join(self.d.name, "demucs")
        with open(self.demucs, "w") as f:
            f.write(FAKE_DEMUCS)
        os.chmod(self.demucs, 0o700)
        self.scratch = os.path.join(self.d.name, "s")
        os.mkdir(self.scratch)
        open(os.path.join(self.scratch, "full.wav"), "wb").close()
        self.src = os.path.join(self.d.name, "up.wav")

    def run_input(self, x):
        write(self.src, x)
        sep = {"scratch": self.scratch, "source": self.src, "demucs": self.demucs}
        rw.stereo_input(sep, None)
        return sep, rw.stereo_fields(sep, [{"start": 0.0, "end": 4.0, "label": "A"}])

    def test_stereo(self):
        c = noise(4) * 0.05
        try:
            import essentia  # noqa: F401
        except ImportError:
            self.skipTest("run under the Essentia runtime")
        sep, (mix, pos) = self.run_input(np.stack([c * math.sqrt(0.5), c * math.sqrt(1.5)], 1))
        self.assertEqual(rw.tone_field(sep, [{"start": 0.0, "end": 4.0, "label": "A"}])["status"], "done")
        self.assertEqual(mix["status"], "done")
        self.assertAlmostEqual(mix["sections"][0]["balance"], 0.5, delta=0.01)
        self.assertEqual(pos["status"], "done")
        self.assertEqual(pos["sections"][0]["vocals"]["level_db"], 0.0)
        self.assertAlmostEqual(pos["sections"][0]["other"]["pan"], 0.5, delta=0.01)

    def test_identical_channels(self):
        c = noise(2) * 0.05
        sep, (mix, pos) = self.run_input(np.stack([c, c], 1))
        self.assertEqual((mix["reason"], pos["reason"]), ("mono_source", "mono_source"))

    def test_decode_failure(self):
        with open(self.src, "wb") as f:
            f.write(b"not audio")
        sep = {"scratch": self.scratch, "source": self.src, "demucs": self.demucs}
        rw.stereo_input(sep, None)
        self.assertEqual(sep["stereo"][0], {"status": "skipped", "reason": "decode_failed"})

    def test_second_run_failure_keeps_the_mix(self):
        try:
            import essentia  # noqa: F401
        except ImportError:
            self.skipTest("run under the Essentia runtime")
        os.environ["FAKE_DEMUCS_FAIL"] = "1"
        _, (mix, pos) = self.run_input(np.stack([noise(4, 1), noise(4, 2)], 1) * 0.05)
        self.assertEqual(mix["status"], "done")
        self.assertEqual(pos, {"status": "skipped", "reason": "separation_failed"})


def done_fields():
    row = [1.0, 1.0, 0.5] + [1.0, 0.1] * 3
    part = {s: [[1.0, 1.0, 0.5]] * 400 for s in rw.STEREO_PARTS}
    return rw.stereo_fields({"stereo": (None, None, {"mix": [row] * 400, "parts": part, "lufs": -10.5, "lra": 3.2})}, SECTIONS)


class ServerTest(unittest.TestCase):
    def stored(self, **fields):
        return json.loads(json.dumps(dict(RHYTHM_OUT_V40, **fields)))

    def test_accepts_worker_fields(self):
        mix, pos = done_fields()
        v = self.stored(mix=mix, part_positions=pos)
        self.assertIsNotNone(server.check_rhythm(v))
        s = server.rhythm_summary(v)
        self.assertEqual(s["mix"]["lufs"], -10.5)
        self.assertEqual(s["part_positions"]["reliability"], "low_reference")

    def test_accepts_a_silence_section(self):
        sections = SECTIONS[:-1] + [dict(SECTIONS[-1], label=rw.SILENCE_LABEL, similarity_to_first=None)]
        row = [1.0, 1.0, 0.5] + [1.0, 0.1] * 3
        part = {s: [[1.0, 1.0, 0.5]] * 400 for s in rw.STEREO_PARTS}
        mix, pos = rw.stereo_fields({"stereo": (None, None, {"mix": [row] * 400, "parts": part, "lufs": -10.5, "lra": 3.2})}, sections)
        self.assertIsNone(mix["sections"][-1]["balance"])
        self.assertIsNone(pos["sections"][-1]["vocals"])
        v = self.stored(sections=sections, mix=mix, part_positions=pos)
        v["melody"]["sections"][-1]["label"] = rw.SILENCE_LABEL
        self.assertIsNotNone(server.check_rhythm(v))

    def test_older_results_stay_valid(self):
        v = self.stored()
        self.assertIsNotNone(server.check_rhythm(v))
        self.assertIsNone(server.rhythm_summary(v)["mix"])

    def test_skipped(self):
        skip = {"status": "skipped", "reason": "mono_source"}
        self.assertIsNotNone(server.check_rhythm(self.stored(mix=skip, part_positions=skip)))
        self.assertIsNone(server.check_rhythm(self.stored(mix={"status": "skipped", "reason": "odd"})))
        self.assertIsNone(server.check_rhythm(self.stored(mix={"status": "skipped", "reason": "mono_source", "x": 1})))

    def test_rejects_bad_fields(self):
        def bad(change):
            mix, pos = done_fields()
            v = self.stored(mix=mix, part_positions=pos)
            change(v)
            self.assertIsNone(server.check_rhythm(json.loads(json.dumps(v))))
        bad(lambda v: v["mix"]["sections"].pop())
        bad(lambda v: v["mix"]["sections"][0].update(balance=1.5))
        bad(lambda v: v["mix"]["sections"][0].update(width_db=41))
        bad(lambda v: v["mix"]["sections"][0].update(extra=1))
        bad(lambda v: v["mix"]["sections"][0].update(start=-1))
        bad(lambda v: v["mix"].update(lufs="x"))
        bad(lambda v: v["mix"].update(reliability="low_reference"))
        bad(lambda v: v["part_positions"].update(reliability="reference_candidate"))
        bad(lambda v: v["part_positions"]["sections"][0].update(vocals={"pan": 2, "width_db": 0, "level_db": 0}))
        bad(lambda v: v["part_positions"]["sections"][0].update(vocals={"pan": 0, "width_db": 0, "level_db": -20}))
        bad(lambda v: v["part_positions"]["sections"][0].update(vocals={"pan": 0, "width_db": 0}))
        bad(lambda v: v["part_positions"]["sections"][0].pop("other"))

    def test_retry_rule(self):
        for reason, want in (("separation_failed", True), ("mix_failed", True), ("decode_failed", True), ("canonical_16k", True), ("mono_source", False)):
            self.assertEqual(server._stereo_can_improve({"mix": {"status": "skipped", "reason": reason}}, True), want, reason)
            self.assertFalse(server._stereo_can_improve({"mix": {"status": "skipped", "reason": reason}}, False))
        self.assertFalse(server._stereo_can_improve({}, True))
        mix, _ = done_fields()
        self.assertFalse(server._stereo_can_improve({"mix": mix}, True))

    def test_stored_position_counts_as_a_stem_layer(self):
        _, pos = done_fields()
        self.assertTrue(server._has_separated_layer({"part_positions": pos}))


if __name__ == "__main__":
    unittest.main()
