"""Bass layer (worker 0.8.0): server wiring with a fake worker, and the worker's fallbacks with fake Demucs, RMVPE and Basic Pitch."""
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
    open(sys.argv[2], "w").write("[null]")
else:
    json.dump([0.0, 1.0], open(sys.argv[2], "w"))
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
                           ("onsets.py", FAKE_ONSETS % (onsets, onsets)), ("bp", "#!/bin/sh\nexec %s \"$@\"\n" % ESSENTIA_PY)):
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


class BassPinTest(unittest.TestCase):
    def test_code_pin_matches_the_file(self):
        import hashlib
        with open(os.path.join(REPO, "bass_onsets.py"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), rw.BASS_ONSETS_SHA256)


if __name__ == "__main__":
    unittest.main()
