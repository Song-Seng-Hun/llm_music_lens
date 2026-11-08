"""Bass layer (worker 0.8.0): server wiring with a fake worker, and the worker's fallbacks with fake Demucs, RMVPE and Basic Pitch.
The Basic Pitch confirmation (0.25.0) on fake inputs, and once with the real models when MUSIC_LENS_BASS_CONFIG names the
server config (unverified otherwise)."""
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import PY, REPO, Client, Env, Harness, server
from test_rhythm import AR, ESSENTIA_PY, RHYTHM_OUT_V40, WAV, FakeWorker, RhythmBase, RID64, rw
from test_separation import FULL

DONE = {"status": "done", "method": rw.BASS_METHOD, "reliability": "low_reference", "note_count": 1}
NOTE = {"start": 0.1, "end": 0.5, "midi": 40, "note": "E2"}


def out(bass=DONE, notes=(NOTE,)):
    return json.loads(json.dumps(dict(RHYTHM_OUT_V40, bass=bass, bass_notes=list(notes))))


class BassServerTest(RhythmBase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        paths = {k: os.path.join(self.env.dir, k) for k in ("demucs", "rmvpe", "bp")}
        for p in paths.values():
            with open(p, "w") as f:
                f.write("x")
            os.chmod(p, 0o700)
        self.bp = paths["bp"]
        self.h = Harness(self.env, rhythm={"python": PY, "worker": self.fw.path,
                                           "separation": {"demucs": paths["demucs"], "rmvpe": paths["rmvpe"]},
                                           "bass": {"basic_pitch_python": self.bp}})
        self.c = Client(self.env, self.h.port)
        self.write_result()
        self.fw.write(stdout=json.dumps(out()))

    def post(self, body):
        return self.c.json("POST", AR, body)[1]["rhythm"]

    def store(self, bass):
        with open(self.rhythm_file(), "w") as f:
            notes = [NOTE] if bass and bass.get("status") == "done" else []
            other = dict(other={"status": "skipped", "reason": "band_limited"}, other_notes=[])  # only bass decides here
            f.write(json.dumps(dict(RHYTHM_OUT_V40, **(dict(bass=bass, bass_notes=notes) if bass else {}), **other, **self.ids())))

    def test_flag_passed(self):
        self.assertEqual(self.post(WAV)["status"], "done")
        argv = self.fw.calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--basic-pitch-python") + 1], self.bp)

    def test_status(self):
        self.assertTrue(self.c.json("GET", "/status")[1]["rhythm"]["bass_configured"])

    def test_summary_and_timeline(self):
        self.assertEqual(self.post(WAV)["bass"], DONE)
        st, b = self.c.json("GET", "/timeline/%s?layer=bass" % RID64)
        self.assertEqual((st, b["bass"], b["reliability"], b["layer_status"]), (200, [NOTE], "low_reference", "done"))

    def test_reuse(self):
        self.store(DONE)
        self.assertTrue(self.post(WAV)["cache_hit"])
        self.store({"status": "skipped", "reason": "band_limited"})
        self.assertTrue(self.post(FULL)["cache_hit"])
        for bass, body, hit in ((None, WAV, True), ({"status": "skipped", "reason": "onsets_failed"}, WAV, True),
                                ({"status": "skipped", "reason": "canonical_16k"}, WAV, True),
                                (None, FULL, False), ({"status": "skipped", "reason": "onsets_failed"}, FULL, False),
                                ({"status": "skipped", "reason": "canonical_16k"}, FULL, False)):
            self.store(bass)
            self.assertEqual(self.post(body)["cache_hit"], hit, (bass, hit))


class BassValidatorTest(unittest.TestCase):
    def test_passes(self):
        for v in (out(), out({"status": "skipped", "reason": "canonical_16k"}, ()), json.loads(json.dumps(RHYTHM_OUT_V40))):
            self.assertIs(server.check_rhythm(v), v)

    def test_method_fits(self):
        self.assertLessEqual(len(rw.BASS_METHOD), 200)

    def test_refused(self):
        lone = json.loads(json.dumps(dict(RHYTHM_OUT_V40, bass=DONE)))
        for v in (lone, out(dict(DONE, note_count=2)), out(dict(DONE, reliability="high")), out(dict(NOTE=1)),
                  out({"status": "skipped", "reason": "other"}, ()), out({"status": "skipped", "reason": "band_limited"}),
                  out(notes=(dict(NOTE, note="C4"),)), out(notes=(dict(NOTE, end=0.1),))):
            self.assertIsNone(server.check_rhythm(v))


class BassConfigTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        self.exe = os.path.join(self.env.dir, "exe")
        with open(self.exe, "w") as f:
            f.write("x")
        os.chmod(self.exe, 0o700)
        self.sp = {"demucs": self.exe, "rmvpe": self.exe}

    def tearDown(self):
        self.env.cleanup()

    def load(self, **extra):
        rh = dict({"python": PY, "worker": self.fw.path}, **extra)
        return server.load_config(self.env.write_config(self.env.config(rhythm=rh)))

    def test_valid(self):
        rh = self.load(separation=self.sp, bass={"basic_pitch_python": self.exe})["rhythm"]
        self.assertEqual(rh["basic_pitch_python"], self.exe)

    def test_refused(self):
        plain = os.path.join(self.env.dir, "plain")
        with open(plain, "w") as f:
            f.write("x")
        os.chmod(plain, 0o600)
        for extra in ({"bass": {"basic_pitch_python": self.exe}}, dict(separation=self.sp, bass={}),
                      dict(separation=self.sp, bass=None), dict(separation=self.sp, bass={"basic_pitch_python": "rel"}),
                      dict(separation=self.sp, bass={"basic_pitch_python": plain})):
            with self.assertRaises(server.ConfigError, msg=str(extra)):
                self.load(**extra)


DRIVER = r'''
import json, os, sys, hashlib
sys.path.insert(0, sys.argv[1])
import rhythm_worker as rw
import numpy as np
cfg = json.loads(sys.argv[2])
if cfg.get("rows"):
    print(json.dumps(rw.bass_rows(np.array(cfg["rows"]["f0"], dtype=float), cfg["rows"]["starts"])))
    sys.exit(0)
if cfg.get("tidy"):
    print(json.dumps(rw.bass_tidied(cfg["tidy"]["rows"], cfg["tidy"]["notes"])))
    sys.exit(0)
if cfg.get("confirm"):
    print(json.dumps(rw.bass_confirmed(cfg["confirm"]["rows"], cfg["confirm"]["notes"])))
    sys.exit(0)
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
rw.RMVPE_CODE, rw.RMVPE_CODE_SHA256 = cfg["rmvpe_code"], sha(cfg["rmvpe_code"])
rw.RMVPE_SHA256 = sha(cfg["sep"]["rmvpe"])
rw.DEMUCS_WEIGHTS_SHA256 = sha(os.path.expanduser(rw.DEMUCS_WEIGHTS))
rw.BASS_ONSETS_CODE, rw.BASS_ONSETS_SHA256 = cfg["onsets"], cfg.get("onsets_sha", sha(cfg["onsets"]))
sep = cfg["sep"]
_, _, field = rw.melody_input(sep, 16000 * 2)
print(json.dumps({"separation": field, "bass": sep.get("bass"), "scratch_left": os.path.exists(sep["scratch"])}))
'''
FAKE_DEMUCS = r'''#!%s -I
import os, shutil, sys
a = sys.argv[1:]
stem = a[a.index("--stem") + 1]
if stem in "%s".split(","):
    sys.exit(3)
d = os.path.join(a[a.index("-o") + 1], "full")
os.makedirs(d, exist_ok=True)
shutil.copy(a[-1], os.path.join(d, stem + ".wav"))
'''
FAKE_RMVPE = r'''import sys, wave
import numpy as np
if "bass16" in sys.argv[2] and "%s" == "fail":
    sys.exit(4)
with wave.open(sys.argv[2]) as w:
    n = w.getnframes() // 160 + 1
np.save(sys.argv[3], np.full(n, 82.4069))
'''
FAKE_ONSETS = r'''import json, sys
if "%s" == "fail":
    sys.exit(5)
if "%s" == "bad":
    open(sys.argv[2], "w").write("[[0.0, 1.0]]")
elif "%s" == "drums":
    json.dump([[0.0, 2.0, 50]], open(sys.argv[2], "w"))
else:
    json.dump([[0.0, 1.0, 40], [1.0, 2.0, 41]], open(sys.argv[2], "w"))
'''


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class BassFallbackTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mlbass-")
        self.home = os.path.join(self.d, "home")
        os.makedirs(os.path.join(self.home, ".cache", "demucs-mlx"))
        with open(os.path.join(self.home, ".cache", "demucs-mlx", "htdemucs_ft.safetensors"), "w") as f:
            f.write("weights")
        self.model = os.path.join(self.d, "rmvpe.pt")
        with open(self.model, "w") as f:
            f.write("model")
        self.driver = os.path.join(self.d, "driver.py")
        with open(self.driver, "w") as f:
            f.write(DRIVER)
        self.noise = os.path.join(self.d, "noise.wav")
        gen = ("import sys, wave, numpy as np\n"
               "y = np.random.default_rng(0).normal(0, 0.1, 44100 * 2)\n"
               "w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)\n"
               "w.writeframes((np.clip(y, -1, 1) * 32767).astype('<i2').tobytes()); w.close()\n")
        subprocess.run([ESSENTIA_PY, "-I", "-c", gen, self.noise], check=True, timeout=60)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_driver(self, cfg):
        p = subprocess.run([ESSENTIA_PY, "-I", self.driver, REPO, json.dumps(cfg)], capture_output=True, timeout=120,
                           env={"PATH": server.CHILD_ENV_PATH, "HOME": self.home, "LANG": "en_US.UTF-8"})
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout)

    def run_bass(self, demucs_fail="", rmvpe="ok", onsets="ok", source="noise", **cfg):
        files = {}
        for name, text in (("demucs", FAKE_DEMUCS % (ESSENTIA_PY, demucs_fail)), ("rmvpe.py", FAKE_RMVPE % rmvpe),
                           ("onsets.py", FAKE_ONSETS % (onsets, onsets, onsets)), ("bp", "#!/bin/sh\nexec %s \"$@\"\n" % ESSENTIA_PY)):
            files[name] = os.path.join(self.d, name)
            with open(files[name], "w") as f:
                f.write(text)
            os.chmod(files[name], 0o700)
        sep = {"demucs": files["demucs"], "rmvpe": self.model, "scratch": os.path.join(self.d, "scratch"), "basic_pitch": files["bp"]}
        if source:
            sep["source"] = self.noise if source == "noise" else source
        out_ = self.run_driver(dict(cfg, sep=sep, rmvpe_code=files["rmvpe.py"], onsets=files["onsets.py"]))
        self.assertFalse(out_["scratch_left"])
        return out_

    def assert_skipped(self, out_, reason):
        self.assertEqual(out_["bass"], [{"status": "skipped", "reason": reason}, []])

    def test_done(self):
        field, rows = self.run_bass()["bass"]
        self.assertEqual((field["status"], field["note_count"], field["reliability"]), ("done", len(rows), "low_reference"))
        self.assertEqual({r["note"] for r in rows}, {"E2"})
        self.assertEqual(len(rows), 2)  # split at the start at 1.0 s

    def test_unconfirmed_notes_dropped(self):
        field, rows = self.run_bass(onsets="drums")["bass"]  # Basic Pitch hears only D3: the E2 run is not a bass note
        self.assertEqual((field["status"], field["note_count"], rows), ("done", 0, []))

    def test_vocal_failure_keeps_bass(self):
        out_ = self.run_bass(demucs_fail="vocals")
        self.assertEqual(out_["separation"], {"status": "skipped", "reason": "separation_failed"})
        self.assertEqual(out_["bass"][0]["status"], "done")

    def test_failures(self):
        self.assert_skipped(self.run_bass(demucs_fail="bass"), "separation_failed")
        self.assert_skipped(self.run_bass(rmvpe="fail"), "rmvpe_failed")
        self.assert_skipped(self.run_bass(onsets="fail"), "onsets_failed")
        self.assert_skipped(self.run_bass(onsets="bad"), "onsets_failed")
        self.assert_skipped(self.run_bass(onsets_sha="0" * 64), "models_invalid")
        self.assert_skipped(self.run_bass(source="/nonexistent/upload.m4a"), "decode_failed")

    def test_canonical(self):
        self.assert_skipped(self.run_bass(source=None), "canonical_16k")

    def test_rows(self):
        e2, g2 = 82.4069, 97.9989
        f0 = [e2] * 30 + [0.0] * 10 + [e2] * 30 + [g2] * 5 + [g2] * 30
        rows = self.run_driver({"rows": {"f0": f0, "starts": []}})
        self.assertEqual([r["midi"] for r in rows], [40, 43])  # the 100 ms gap joins, no start falls on it
        rows = self.run_driver({"rows": {"f0": f0, "starts": [0.4]}})
        self.assertEqual([r["midi"] for r in rows], [40, 40, 43])  # a start splits it
        rows = self.run_driver({"rows": {"f0": [e2] * 5 + [0.0] * 40, "starts": []}})
        self.assertEqual(rows, [])  # under 80 ms

    def test_confirmed(self):
        rows = [{"start": 0.0, "end": 0.5, "midi": 40, "note": "E2"}, {"start": 0.5, "end": 1.0, "midi": 50, "note": "D3"},
                {"start": 1.0, "end": 1.5, "midi": 28, "note": "E1"}, {"start": 2.0, "end": 2.5, "midi": 33, "note": "A1"}]
        notes = [[0.1, 0.4, 41], [0.5, 1.0, 52], [1.4, 1.6, 28], [2.5, 3.0, 33]]
        kept = self.run_driver({"confirm": {"rows": rows, "notes": notes}})
        # a semitone off counts, two do not, any overlap counts, touching ends do not
        self.assertEqual([r["note"] for r in kept], ["E2", "E1"])
        self.assertEqual(self.run_driver({"confirm": {"rows": rows, "notes": []}}), [])


REAL_DRIVER = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import numpy as np
import rhythm_worker as rw
sep = json.loads(sys.argv[2])
sr, rng = 44100, np.random.default_rng(0)
y = np.zeros(sr * 8)
def put(t, x):
    i = int(t * sr); y[i:i + len(x)] += x[:len(y) - i]
def ring(f_end, f_start, tau, n=1.0):
    t = np.arange(int(n * sr)) / sr
    f = f_end + (f_start - f_end) * np.exp(-t / 0.04)
    return 0.8 * np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t / tau) + 0.2 * rng.normal(0, 1, len(t)) * np.exp(-t / 0.004)
for k in range(8):  # 0-4 s: kick and toms only
    put(k * 0.5, ring(55, 165, 0.25) if k % 2 == 0 else ring(98 if k % 4 == 1 else 131, 140, 0.4))
truth = [(4.0, 33), (4.5, 33), (5.0, 36), (5.5, 31), (6.0, 28), (6.5, 40), (7.0, 38)]
for t0, m in truth:  # 4-8 s: a bass line with a kick on every note
    t = np.arange(int(0.45 * sr)) / sr
    ph = 2 * np.pi * 440 * 2 ** ((m - 69) / 12) * t
    put(t0, 0.4 * sum(np.sin(k * ph) / k ** 1.3 for k in range(1, 8)) * np.minimum(t / 0.005, 1) * np.clip((0.45 - t) / 0.03, 0, 1))
    put(t0, ring(55, 165, 0.25))
os.makedirs(sep["scratch"], mode=0o700)
rw.write_wav(os.path.join(sep["scratch"], "full.wav"), 44100, y / np.abs(y).max() * 0.9)
rows, reason = rw.bass_stem_notes(sep, 16000 * 8)
print(json.dumps({"rows": rows, "reason": reason, "truth": truth}))
'''
REAL_CONFIG = os.environ.get("MUSIC_LENS_BASS_CONFIG", "")


@unittest.skipUnless(REAL_CONFIG, "unverified: set MUSIC_LENS_BASS_CONFIG to the server config for the real Demucs, RMVPE and Basic Pitch")
class BassRealModelTest(unittest.TestCase):
    def test_bass_on_kick_kept(self):
        rh = server.load_config(REAL_CONFIG)["rhythm"]
        if "basic_pitch_python" not in rh:
            self.skipTest("unverified: the config has no bass layer")
        lock = os.open(server.load_config(REAL_CONFIG)["root"] + "/.analyze.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # one heavy job at a time, as /analyze
            except BlockingIOError:
                self.skipTest("unverified: the team flock is busy")
            d = tempfile.mkdtemp(prefix="mlbassreal-")
            try:
                sep = {"demucs": rh["demucs"], "rmvpe": rh["rmvpe"], "basic_pitch": rh["basic_pitch_python"],
                       "scratch": os.path.join(d, "scratch")}
                driver = os.path.join(d, "driver.py")
                with open(driver, "w") as f:
                    f.write(REAL_DRIVER)
                p = subprocess.run([rh["python"], "-I", driver, REPO, json.dumps(sep)], capture_output=True, timeout=600)
                self.assertEqual(p.returncode, 0, p.stderr[-800:])
                got = json.loads(p.stdout)
            finally:
                shutil.rmtree(d, ignore_errors=True)
        finally:
            os.close(lock)
        self.assertIsNone(got["reason"])
        # Sine-like toms are pitched notes to Basic Pitch too, so 0-4 s may keep a few: a known limit (see README.md).
        self.assertLessEqual(len([r for r in got["rows"] if r["start"] < 3.9]), 8)
        found = [m for t0, m in got["truth"] if any(abs(r["start"] - t0) <= 0.08 and r["midi"] == m for r in got["rows"])]
        self.assertGreaterEqual(len(found), len(got["truth"]) - 1, got["rows"])  # the bass on the kick stays


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class BassTidyTest(unittest.TestCase):
    """0.26.0: same-pitch pieces join, late starts move back to Basic Pitch's (fake rows and notes, no models)."""
    def tidy(self, rows, notes):
        d = tempfile.mkdtemp(prefix="mlbasstidy-")
        try:
            drv = os.path.join(d, "driver.py")
            with open(drv, "w") as f:
                f.write(DRIVER)
            p = subprocess.run([ESSENTIA_PY, "-I", drv, REPO, json.dumps({"tidy": {"rows": rows, "notes": notes}})],
                               capture_output=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr[-800:])
            return [(r["start"], r["end"], r["midi"]) for r in json.loads(p.stdout)]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @staticmethod
    def rows(*spec):
        return [{"start": a, "end": b, "midi": m, "note": "x"} for a, b, m in spec]

    def test_join(self):
        self.assertEqual(self.tidy(self.rows((0.0, 0.5, 40), (0.54, 1.0, 40)), []), [(0.0, 1.0, 40)])  # 40 ms apart, joined
        self.assertEqual(self.tidy(self.rows((0.0, 0.5, 40), (0.5, 1.0, 40)), [[0.51, 1.0, 41]]),
                         [(0.0, 0.5, 40), (0.5, 1.0, 40)])  # a Basic Pitch start on the join (a semitone off): two notes
        self.assertEqual(self.tidy(self.rows((0.0, 0.5, 40), (0.56, 1.0, 40)), []), [(0.0, 0.5, 40), (0.56, 1.0, 40)])  # 60 ms
        self.assertEqual(self.tidy(self.rows((0.0, 0.5, 40), (0.52, 1.0, 43)), []), [(0.0, 0.5, 40), (0.52, 1.0, 43)])  # other pitch

    def test_snap(self):
        self.assertEqual(self.tidy(self.rows((1.0, 1.5, 40)), [[0.82, 1.4, 40]]), [(0.82, 1.5, 40)])  # moved back
        self.assertEqual(self.tidy(self.rows((1.0, 1.5, 40)), [[0.82, 1.4, 42]]), [(1.0, 1.5, 40)])  # two semitones: not moved
        self.assertEqual(self.tidy(self.rows((1.0, 1.5, 40)), [[0.7, 1.4, 40]]), [(1.0, 1.5, 40)])  # over 250 ms: not moved
        self.assertEqual(self.tidy(self.rows((1.0, 1.5, 40)), [[0.9, 0.99, 40], [1.01, 1.5, 40]]), [(1.0, 1.5, 40)])  # just after
        self.assertEqual(self.tidy(self.rows((1.0, 1.5, 40)), [[0.8, 0.9, 40], [0.9, 1.5, 40]]), [(0.9, 1.5, 40)])  # the latest
        self.assertEqual(self.tidy(self.rows((0.5, 0.9, 43), (1.0, 1.5, 40)), [[0.8, 1.4, 40]]),
                         [(0.5, 0.9, 43), (0.9, 1.5, 40)])  # never before the row ahead ends


class BassPinTest(unittest.TestCase):
    def test_code_pin_matches_the_file(self):
        import hashlib
        with open(os.path.join(REPO, "bass_onsets.py"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), rw.BASS_ONSETS_SHA256)


if __name__ == "__main__":
    unittest.main()
