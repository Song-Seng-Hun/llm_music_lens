"""Drum layer (worker 0.9.0): server wiring with a fake worker, the worker's fallbacks with fake Demucs and model, and adtof_model's own parts."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import PY, REPO, Client, Env, Harness, server
from test_rhythm import AR, ESSENTIA_PY, RHYTHM_OUT_V40, WAV, FakeWorker, RhythmBase, RID64, rw
from test_separation import FULL

COUNTS = {"kick": 1, "snare": 1, "tom": 0, "hihat": 0, "cymbal": 0}
DONE = {"status": "done", "method": rw.DRUM_METHOD, "reliability": "reference_candidate", "hit_counts": COUNTS}
HITS = [{"time": 0.1, "class": "kick"}, {"time": 0.5, "class": "snare"}]


def out(drums=DONE, hits=HITS):
    return json.loads(json.dumps(dict(RHYTHM_OUT_V40, drums=drums, drum_hits=list(hits))))


class DrumServerTest(RhythmBase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        paths = {k: os.path.join(self.env.dir, k) for k in ("demucs", "rmvpe", "adtof.index")}
        for p in paths.values():
            with open(p, "w") as f:
                f.write("x")
            os.chmod(p, 0o700)
        self.model = paths["adtof.index"]
        self.h = Harness(self.env, rhythm={"python": PY, "worker": self.fw.path, "drum_model": self.model,
                                           "separation": {"demucs": paths["demucs"], "rmvpe": paths["rmvpe"]}})
        self.c = Client(self.env, self.h.port)
        self.write_result()
        self.fw.write(stdout=json.dumps(out()))

    def post(self, body):
        return self.c.json("POST", AR, body)[1]["rhythm"]

    def store(self, drums):
        with open(self.rhythm_file(), "w") as f:
            hits = HITS if drums and drums.get("status") == "done" else []
            f.write(json.dumps(dict(RHYTHM_OUT_V40, **(dict(drums=drums, drum_hits=hits) if drums else {}), **self.ids())))

    def test_flag_passed(self):
        self.assertEqual(self.post(WAV)["status"], "done")
        argv = self.fw.calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--drum-model") + 1], self.model)

    def test_status(self):
        self.assertTrue(self.c.json("GET", "/status")[1]["rhythm"]["drums_configured"])

    def test_summary_and_timeline(self):
        self.assertEqual(self.post(WAV)["drums"], DONE)
        st, b = self.c.json("GET", "/timeline/%s?layer=drums" % RID64)
        self.assertEqual((st, b["drums"], b["reliability"], b["layer_status"]), (200, HITS, "reference_candidate", "done"))
        st, b = self.c.json("GET", "/timeline/%s?layer=drums&start=0.3&end=1" % RID64)
        self.assertEqual((st, b["drums"]), (200, HITS[1:]))

    def test_no_layer_stored(self):
        self.store(None)
        self.assertEqual(self.c.json("GET", "/timeline/%s?layer=drums" % RID64)[0], 404)

    def test_reuse(self):
        self.store(DONE)
        self.assertTrue(self.post(WAV)["cache_hit"])
        self.store({"status": "skipped", "reason": "band_limited"})
        self.assertTrue(self.post(FULL)["cache_hit"])
        for drums, body, hit in ((None, WAV, True), ({"status": "skipped", "reason": "adtof_failed"}, WAV, True),
                                 ({"status": "skipped", "reason": "canonical_16k"}, WAV, True),
                                 (None, FULL, False), ({"status": "skipped", "reason": "adtof_failed"}, FULL, False),
                                 ({"status": "skipped", "reason": "canonical_16k"}, FULL, False)):
            self.store(drums)
            self.assertEqual(self.post(body)["cache_hit"], hit, (drums, hit))


class DrumValidatorTest(unittest.TestCase):
    def test_passes(self):
        for v in (out(), out({"status": "skipped", "reason": "canonical_16k"}, ()), json.loads(json.dumps(RHYTHM_OUT_V40))):
            self.assertIs(server.check_rhythm(v), v)

    def test_method_fits(self):
        self.assertLessEqual(len(rw.DRUM_METHOD), 200)

    def test_refused(self):
        lone = json.loads(json.dumps(dict(RHYTHM_OUT_V40, drums=DONE)))
        for v in (lone, out(dict(DONE, hit_counts=dict(COUNTS, kick=2))), out(dict(DONE, reliability="high")),
                  out({"status": "skipped", "reason": "other"}, ()), out({"status": "skipped", "reason": "band_limited"}),
                  out(hits=[dict(HITS[0], **{"class": "cowbell"})]), out(hits=HITS[::-1]), out(hits=[dict(HITS[0], time=-1)])):
            self.assertIsNone(server.check_rhythm(v))


class DrumConfigTest(unittest.TestCase):
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
        self.assertEqual(self.load(separation=self.sp, drum_model=self.exe)["rhythm"]["drum_model"], self.exe)

    def test_refused(self):
        for extra in ({"drum_model": self.exe}, dict(separation=self.sp, drum_model="rel"),
                      dict(separation=self.sp, drum_model=os.path.join(self.env.dir, "missing"))):
            with self.assertRaises(server.ConfigError, msg=str(extra)):
                self.load(**extra)


DRIVER = r'''
import json, os, sys, hashlib
sys.path.insert(0, sys.argv[1])
import rhythm_worker as rw
cfg = json.loads(sys.argv[2])
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
rw.RMVPE_CODE, rw.RMVPE_CODE_SHA256 = cfg["rmvpe_code"], sha(cfg["rmvpe_code"])
rw.RMVPE_SHA256 = sha(cfg["sep"]["rmvpe"])
rw.DEMUCS_WEIGHTS_SHA256 = sha(os.path.expanduser(rw.DEMUCS_WEIGHTS))
index = cfg["sep"]["drum_model"]
rw.ADTOF_CODE, rw.ADTOF_CODE_SHA256 = cfg["adtof"], sha(cfg["adtof"])
rw.ADTOF_INDEX_SHA256 = cfg.get("index_sha", sha(index)) if os.path.exists(index) else "0" * 64
rw.ADTOF_DATA_SHA256 = sha(index[:-len(".index")] + ".data-00000-of-00001") if index.endswith(".index") else "0" * 64
sep = cfg["sep"]
_, _, field = rw.melody_input(sep, 16000 * 2)
print(json.dumps({"separation": field, "drums": sep.get("drums"), "scratch_left": os.path.exists(sep["scratch"])}))
'''
FAKE_DEMUCS = r'''#!%s -I
import os, shutil, sys
a = sys.argv[1:]
if "--stem" not in a:  # one run for all four stems; it fails when any stem is set to fail
    if "%s".strip(","):
        sys.exit(3)
    d = os.path.join(a[a.index("-o") + 1], "full")
    os.makedirs(d, exist_ok=True)
    for s in ("vocals", "bass", "drums", "other"):
        shutil.copy(a[-1], os.path.join(d, s + ".wav"))
    sys.exit(0)
stem = a[a.index("--stem") + 1]
if stem in "%s".split(","):
    sys.exit(3)
d = os.path.join(a[a.index("-o") + 1], "full")
os.makedirs(d, exist_ok=True)
shutil.copy(a[-1], os.path.join(d, stem + ".wav"))
'''
FAKE_RMVPE = r'''import sys, wave
import numpy as np
with wave.open(sys.argv[2]) as w:
    n = w.getnframes() // 160 + 1
np.save(sys.argv[3], np.full(n, 440.0))
'''
FAKE_ADTOF = r'''import json, sys
if "%s" == "fail":
    sys.exit(4)
if "%s" == "bad":
    open(sys.argv[3], "w").write('{"kick": [null]}')
else:
    json.dump({"kick": [0.5, 99.0], "snare": [0.5, 0.25], "tom": [], "hihat": [1.0], "cymbal": []}, open(sys.argv[3], "w"))
'''


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class DrumFallbackTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mldrum-")
        self.home = os.path.join(self.d, "home")
        os.makedirs(os.path.join(self.home, ".cache", "demucs-mlx"))
        with open(os.path.join(self.home, ".cache", "demucs-mlx", "htdemucs_ft.safetensors"), "w") as f:
            f.write("weights")
        for name in ("rmvpe.pt", "a.index", "a.data-00000-of-00001"):
            with open(os.path.join(self.d, name), "w") as f:
                f.write(name)
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

    def run_drums(self, demucs_fail="", adtof="ok", source="noise", index="a.index", **cfg):
        files = {}
        for name, text in (("demucs", FAKE_DEMUCS % (ESSENTIA_PY, demucs_fail, demucs_fail)), ("rmvpe.py", FAKE_RMVPE), ("adtof.py", FAKE_ADTOF % (adtof, adtof))):
            files[name] = os.path.join(self.d, name)
            with open(files[name], "w") as f:
                f.write(text)
            os.chmod(files[name], 0o700)
        sep = {"demucs": files["demucs"], "rmvpe": os.path.join(self.d, "rmvpe.pt"), "scratch": os.path.join(self.d, "scratch"),
               "drum_model": os.path.join(self.d, index)}
        if source:
            sep["source"] = self.noise if source == "noise" else source
        p = subprocess.run([ESSENTIA_PY, "-I", self.driver, REPO, json.dumps(dict(cfg, sep=sep, rmvpe_code=files["rmvpe.py"], adtof=files["adtof.py"]))],
                           capture_output=True, timeout=120, env={"PATH": server.CHILD_ENV_PATH, "HOME": self.home, "LANG": "en_US.UTF-8"})
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        out_ = json.loads(p.stdout)
        self.assertFalse(out_["scratch_left"])
        return out_

    def assert_skipped(self, out_, reason):
        self.assertEqual(out_["drums"], [{"status": "skipped", "reason": reason}, []])

    def test_done(self):
        field, rows = self.run_drums()["drums"]
        self.assertEqual((field["status"], field["reliability"]), ("done", "reference_candidate"))
        self.assertEqual(rows, [{"time": 0.25, "class": "snare"}, {"time": 0.5, "class": "kick"}, {"time": 0.5, "class": "snare"},
                                {"time": 1.0, "class": "hihat"}])  # sorted; the hit past the end is gone
        self.assertEqual(field["hit_counts"], {"kick": 1, "snare": 2, "tom": 0, "hihat": 1, "cymbal": 0})

    def test_failed_run_skips_vocal_and_drums(self):
        out_ = self.run_drums(demucs_fail="vocals")  # one Demucs run serves both, so both go
        self.assertEqual(out_["separation"], {"status": "skipped", "reason": "separation_failed"})
        self.assert_skipped(out_, "separation_failed")

    def test_failures(self):
        self.assert_skipped(self.run_drums(demucs_fail="drums"), "separation_failed")
        self.assert_skipped(self.run_drums(adtof="fail"), "adtof_failed")
        self.assert_skipped(self.run_drums(adtof="bad"), "adtof_failed")
        self.assert_skipped(self.run_drums(index_sha="0" * 64), "models_invalid")
        self.assert_skipped(self.run_drums(index="rmvpe.pt"), "models_invalid")
        self.assert_skipped(self.run_drums(source="/nonexistent/upload.m4a"), "decode_failed")

    def test_canonical(self):
        self.assert_skipped(self.run_drums(source=None), "canonical_16k")


PARTS = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import numpy as np, torch, adtof_model as a
active = np.zeros(200, np.float32); active[[50, 51, 120]] = [0.9, 0.5, 0.2]
wide = np.zeros(100); wide[[40, 42]] = [0.9, 0.9]
net = a.Network()
with torch.no_grad():
    shape = list(net.eval()(torch.zeros(1, 30, 84)).shape)
tone = np.sin(2 * np.pi * 440 * np.arange(44100) / 44100).astype(np.float32)
spec = a.spectrogram(tone)
print(json.dumps({"bands": a.filterbank().shape[1], "sums": bool(abs(a.filterbank().sum(0) - 1).max() < 1e-5),
                  "peaks": a.pick(active, 0.3), "merged": a.pick(wide, 0.3), "shape": shape, "frames": spec.shape[0],
                  "empty": a.spectrogram(np.zeros(0, np.float32)).shape[0]}))
'''


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class AdtofPartsTest(unittest.TestCase):
    def test_parts(self):
        p = subprocess.run([ESSENTIA_PY, "-I", "-W", "ignore", "-c", PARTS, REPO], capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        v = json.loads(p.stdout)
        self.assertEqual((v["bands"], v["sums"], v["shape"], v["frames"], v["empty"]), (84, True, [1, 30, 5], 100, 0))
        self.assertEqual(v["peaks"], [0.5])  # the frame after a peak and the weak one are not hits
        self.assertEqual(v["merged"], [0.4])  # within 20 ms of the last hit


class DrumPinTest(unittest.TestCase):
    def test_code_pin_matches_the_file(self):
        with open(os.path.join(REPO, "adtof_model.py"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), rw.ADTOF_CODE_SHA256)


if __name__ == "__main__":
    unittest.main()
