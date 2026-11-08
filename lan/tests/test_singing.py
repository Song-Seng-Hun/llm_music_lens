"""Sung notes (worker 0.21.0): server wiring with a fake worker, and the worker's fallbacks with fake Demucs, RMVPE and model."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import PY, REPO, Client, Env, Harness, server
from test_rhythm import AR, ESSENTIA_PY, MELODY, RHYTHM_OUT_V40, WAV, FakeWorker, RhythmBase, rw
from test_separation import FULL

DONE, SEP = {"status": "done"}, {"status": "done"}


def with_singing(sg, sp=SEP):
    return json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY, separation=sp, singing=sg))))


class SingingServerTest(RhythmBase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        paths = {k: os.path.join(self.env.dir, k) for k in ("demucs", "rmvpe", "ymt3-python")}
        for p in paths.values():
            with open(p, "w") as f:
                f.write("x")
            os.chmod(p, 0o700)
        self.py = paths["ymt3-python"]
        self.h = Harness(self.env, rhythm={"python": PY, "worker": self.fw.path, "singing": {"yourmt3_python": self.py},
                                           "separation": {"demucs": paths["demucs"], "rmvpe": paths["rmvpe"]}})
        self.c = Client(self.env, self.h.port)
        self.write_result()
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V40))

    def post(self, body):
        return self.c.json("POST", AR, body)[1]["rhythm"]

    def store(self, sg, sp=SEP):
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(dict(with_singing(sg, sp), **self.ids())))

    def test_flag_passed(self):
        self.assertEqual(self.post(WAV)["status"], "done")
        argv = self.fw.calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--singing-python") + 1], self.py)
        self.assertLessEqual(float(argv[argv.index("--time-budget") + 1]), server.DEFAULT_LIMITS["rhythm_timeout"])

    def test_status(self):
        self.assertTrue(self.c.json("GET", "/status")[1]["rhythm"]["singing_configured"])

    def test_reuse(self):
        for sg, body, hit in ((DONE, WAV, True), (DONE, FULL, True),
                              ({"status": "skipped", "reason": "timeout"}, FULL, True),
                              ({"status": "skipped", "reason": "no_time"}, FULL, True),
                              ({"status": "skipped", "reason": "notes_failed"}, WAV, True),
                              ({"status": "skipped", "reason": "notes_failed"}, FULL, False),
                              ({"status": "skipped", "reason": "models_invalid"}, FULL, False),
                              ({"status": "skipped", "reason": "canonical_16k"}, FULL, False)):
            self.store(sg)
            self.assertEqual(self.post(body)["cache_hit"], hit, (sg, hit))

    def test_stored_without_the_field(self):
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY, separation=SEP), **self.ids())))
        self.assertTrue(self.post(WAV)["cache_hit"])
        self.assertFalse(self.post(FULL)["cache_hit"])


class SingingValidatorTest(unittest.TestCase):
    def test_passes(self):
        for sg, sp in ((DONE, SEP), ({"status": "skipped", "reason": "timeout"}, SEP),
                       ({"status": "skipped", "reason": "canonical_16k"}, {"status": "skipped", "reason": "canonical_16k"})):
            v = with_singing(sg, sp)
            self.assertIs(server.check_rhythm(v), v, sg)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY, method=rw.MEL_METHOD_SING, separation=SEP, singing=DONE))))
        self.assertIs(server.check_rhythm(v), v)

    def test_refused(self):
        for sg, sp in ((DONE, {"status": "skipped", "reason": "band_limited"}), (None, SEP), ({}, SEP),
                       ({"status": "skipped"}, SEP), ({"status": "skipped", "reason": "other"}, SEP),
                       ({"status": "done", "reason": "x"}, SEP)):
            self.assertIsNone(server.check_rhythm(with_singing(sg, sp)), sg)


class SingingConfigTest(unittest.TestCase):
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
        self.assertEqual(self.load(separation=self.sp, singing={"yourmt3_python": self.exe})["rhythm"]["yourmt3_python"], self.exe)

    def test_refused(self):
        plain = os.path.join(self.env.dir, "plain")
        with open(plain, "w") as f:
            f.write("x")
        os.chmod(plain, 0o600)
        for extra in ({"singing": {"yourmt3_python": self.exe}}, dict(separation=self.sp, singing={}),
                      dict(separation=self.sp, singing={"yourmt3_python": "rel"}),
                      dict(separation=self.sp, singing={"yourmt3_python": self.exe, "x": self.exe}),
                      dict(separation=self.sp, singing={"yourmt3_python": plain})):
            with self.assertRaises(server.ConfigError, msg=str(extra)):
                self.load(**extra)


DRIVER = r'''
import json, os, sys, time, hashlib
sys.path.insert(0, sys.argv[1])
import rhythm_worker as rw
import numpy as np
cfg = json.loads(sys.argv[2])
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
rw.RMVPE_CODE, rw.RMVPE_CODE_SHA256 = cfg["rmvpe_code"], sha(cfg["rmvpe_code"])
rw.RMVPE_SHA256 = sha(cfg["sep"]["rmvpe"])
rw.DEMUCS_WEIGHTS_SHA256 = sha(os.path.expanduser(rw.DEMUCS_WEIGHTS))
rw.SING_CODE, rw.SING_CODE_SHA256 = cfg["sing_code"], sha(cfg["sing_code"])
rw.SING_DIR = cfg["sing_dir"]
rw.SING_CKPT_SHA256 = cfg.get("ckpt_sha", sha(os.path.join(rw.SING_DIR, rw.SING_CKPT)))
rw.SING_MAX = cfg.get("sing_max", rw.SING_MAX)
rw.START = time.monotonic() - cfg.get("spent", 0)
sep = cfg["sep"]
t = np.arange(16000 * 4) / 16000.0
x = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
separated = rw.melody_input(sep, len(x))
m, notes, warns = rw.melody(x, 4.0, "C", "major", cfg["sections"], separated=separated, sing=sep.get("sing"))
print(json.dumps({"melody": m, "notes": notes, "warns": warns, "scratch_left": os.path.exists(sep["scratch"])}))
'''
FAKE_DEMUCS = r'''#!%s -I
import os, shutil, sys
a = sys.argv[1:]
if "%s" == "fail":
    sys.exit(3)
d = os.path.join(a[a.index("-o") + 1], "full")
os.makedirs(d, exist_ok=True)
for s in [a[a.index("--stem") + 1]] if "--stem" in a else ["vocals", "bass", "drums", "other"]:
    shutil.copy(a[-1], os.path.join(d, s + ".wav"))
'''
FAKE_RMVPE = r'''import sys, wave
import numpy as np
with wave.open(sys.argv[2]) as w:
    n = w.getnframes() // 160 + 1
np.save(sys.argv[3], np.full(n, 440.0))
'''
FAKE_SING = r'''import json, os, sys, time
mode = "%s"
assert os.path.isfile(sys.argv[2]) and sys.argv[2].endswith("/full/vocals.wav")
if mode == "fail":
    sys.exit(3)
if mode == "sleep":
    time.sleep(30)
rows = {"ok": [[0.5, 1.0, 72], [0.1, 0.4, 60], [3.9, 9.0, 64], [5.0, 6.0, 65]], "empty": [[5.0, 6.0, 65]]}.get(mode, [[None, 1.0, 60]])
json.dump(rows, open(sys.argv[3], "w"))
'''


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class SingingWorkerTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mlsing-")
        self.home = os.path.join(self.d, "home")
        os.makedirs(os.path.join(self.home, ".cache", "demucs-mlx"))
        with open(os.path.join(self.home, ".cache", "demucs-mlx", "htdemucs_ft.safetensors"), "w") as f:
            f.write("weights")
        self.sing_dir = os.path.join(self.d, "yourmt3")
        ck = os.path.join(self.sing_dir, rw.SING_CKPT)
        os.makedirs(os.path.dirname(ck))
        with open(ck, "w") as f:
            f.write("checkpoint")
        with open(os.path.join(self.d, "rmvpe.pt"), "w") as f:
            f.write("model")
        self.driver = os.path.join(self.d, "driver.py")
        with open(self.driver, "w") as f:
            f.write(DRIVER)
        self.noise = os.path.join(self.d, "noise.wav")
        gen = ("import sys, wave, numpy as np\n"
               "t = np.arange(44100 * 4) / 44100.0\n"
               "y = 0.2 * np.sin(2 * np.pi * 440.0 * t) + np.random.default_rng(0).normal(0, 0.1, len(t))\n"
               "w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)\n"
               "w.writeframes((np.clip(y, -1, 1) * 32767).astype('<i2').tobytes()); w.close()\n")
        subprocess.run([ESSENTIA_PY, "-I", "-c", gen, self.noise], check=True, timeout=60)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_sing(self, sing="ok", demucs="ok", source="noise", **cfg):
        files = {}
        for name, text in (("demucs", FAKE_DEMUCS % (ESSENTIA_PY, demucs)), ("rmvpe.py", FAKE_RMVPE), ("sing.py", FAKE_SING % sing)):
            files[name] = os.path.join(self.d, name)
            with open(files[name], "w") as f:
                f.write(text)
            os.chmod(files[name], 0o700)
        sep = {"demucs": files["demucs"], "rmvpe": os.path.join(self.d, "rmvpe.pt"), "scratch": os.path.join(self.d, "scratch"),
               "singing_python": ESSENTIA_PY}
        if source:
            sep["source"] = self.noise
        arg = dict(cfg, sep=sep, rmvpe_code=files["rmvpe.py"], sing_code=files["sing.py"], sing_dir=self.sing_dir,
                   sections=RHYTHM_OUT_V40["sections"])
        p = subprocess.run([ESSENTIA_PY, "-I", self.driver, REPO, json.dumps(arg)], capture_output=True, timeout=120,
                           env={"PATH": server.CHILD_ENV_PATH, "HOME": self.home, "LANG": "en_US.UTF-8"})
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        out = json.loads(p.stdout)
        self.assertFalse(out["scratch_left"])
        return out

    def assert_rmvpe(self, out, reason, warned=True):
        m = out["melody"]
        self.assertEqual(m["singing"], {"status": "skipped", "reason": reason})
        self.assertEqual({n["midi"] for n in out["notes"]}, {69})  # the fake RMVPE's 440 Hz
        self.assertEqual(m["method"], rw.MEL_METHOD_VOCAL)
        self.assertEqual(any(w.startswith("melody_singing_failed: " + reason) for w in out["warns"]), warned)

    def test_done(self):
        out = self.run_sing()
        m = out["melody"]
        self.assertEqual((m["singing"], m["separation"], m["method"]), (DONE, SEP, rw.MEL_METHOD_SING))
        self.assertEqual([(n["start"], n["end"], n["midi"], n["note"]) for n in out["notes"]],
                         [(0.1, 0.4, 60, "C4"), (0.5, 1.0, 72, "C5"), (3.9, 4.0, 64, "E4")])  # sorted, clipped, past the end gone
        self.assertEqual((m["note_count"], m["note_count_raw"]), (3, 3))
        self.assertEqual(out["warns"], [])
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=m, melody_notes=out["notes"])))
        self.assertIs(server.check_rhythm(v), v)  # what the worker writes passes the server's check

    def test_failures_keep_rmvpe_notes(self):
        self.assert_rmvpe(self.run_sing("fail"), "notes_failed")
        self.assert_rmvpe(self.run_sing("bad"), "notes_failed")
        self.assert_rmvpe(self.run_sing("empty"), "no_notes")  # only a note past the end: nothing left
        self.assert_rmvpe(self.run_sing(ckpt_sha="0" * 64), "models_invalid")
        self.assert_rmvpe(self.run_sing("sleep", sing_max=2), "timeout")
        self.assert_rmvpe(self.run_sing(spent=rw.SING_BUDGET), "no_time")

    def test_no_vocal(self):
        out = self.run_sing(demucs="fail")
        self.assertEqual(out["melody"]["singing"], {"status": "skipped", "reason": "no_vocal"})
        self.assertEqual(out["melody"]["method"], rw.MEL_METHOD)
        self.assertFalse(any(w.startswith("melody_singing_failed") for w in out["warns"]))

    def test_canonical(self):
        out = self.run_sing(source=None)
        self.assertEqual(out["melody"]["singing"], {"status": "skipped", "reason": "canonical_16k"})


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class TimeBudgetArgTest(unittest.TestCase):
    def test_bad_values_refused(self):
        for v in ("0", "-5", "abc", "1e3", "99999"):
            p = subprocess.run([ESSENTIA_PY, "-I", os.path.join(REPO, "rhythm_worker.py"), "--time-budget", v, "/x.wav"],
                               capture_output=True, timeout=60)
            self.assertEqual(p.returncode, 1, v)
            self.assertIn(b"invalid_argument", p.stderr, v)


class SingingPinTest(unittest.TestCase):
    def test_code_pin_matches_the_file(self):
        with open(os.path.join(REPO, "sing_notes.py"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), rw.SING_CODE_SHA256)

    def test_method_fits(self):
        self.assertLessEqual(len(rw.MEL_METHOD_SING), 200)


if __name__ == "__main__":
    unittest.main()
