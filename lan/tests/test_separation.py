"""Vocal melody (worker 0.6.0): server wiring with a fake worker, and the worker's fallbacks with fake Demucs and RMVPE."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import wave

from helpers import PY, REPO, Client, Env, Harness, server
from test_rhythm import AR, ESSENTIA_PY, MELODY, RHYTHM_OUT_V40, WAV, FakeWorker, RhythmBase, rw


def wav_44k(seconds=0.5):
    path = tempfile.mktemp(suffix=".wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * int(44100 * seconds))
    with open(path, "rb") as f:
        data = f.read()
    os.unlink(path)
    return data


FULL = wav_44k()


class SeparationServerTest(RhythmBase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        self.demucs = os.path.join(self.env.dir, "demucs-mlx")
        self.rmvpe = os.path.join(self.env.dir, "rmvpe.pt")
        for p, mode in ((self.demucs, 0o700), (self.rmvpe, 0o600)):
            with open(p, "w") as f:
                f.write("x")
            os.chmod(p, mode)
        self.h = Harness(self.env, rhythm={"python": PY, "worker": self.fw.path,
                                           "separation": {"demucs": self.demucs, "rmvpe": self.rmvpe}})
        self.c = Client(self.env, self.h.port)
        self.write_result()
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V40))

    def write_stored(self, v):
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(v))

    def with_separation(self, sp):
        return dict(RHYTHM_OUT_V40, melody=dict(MELODY, separation=sp), **self.ids())

    def post(self, body):
        return self.c.json("POST", AR, body)[1]["rhythm"]

    def test_canonical_upload_has_no_source(self):
        self.assertEqual(self.post(WAV)["status"], "done")
        argv = self.fw.calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--demucs") + 1], self.demucs)
        self.assertEqual(argv[argv.index("--rmvpe-model") + 1], self.rmvpe)
        self.assertTrue(argv[argv.index("--melody-scratch") + 1].endswith("/" + server.SEP_DIR))
        self.assertNotIn("--melody-source", argv)
        self.assertTrue(argv[-1].endswith(".wav"))

    def test_full_band_upload_passes_the_original(self):
        self.assertEqual(self.post(FULL)["status"], "done")
        argv = self.fw.calls()[-1]["argv"]
        self.assertTrue(argv[argv.index("--melody-source") + 1].endswith("/upload.wav"))

    def test_status(self):
        self.assertTrue(self.c.json("GET", "/status")[1]["rhythm"]["separation_configured"])

    def test_stored_mix_melody_recomputed_only_when_it_can_improve(self):
        for reason in ("canonical_16k", "not_configured"):
            self.write_stored(self.with_separation({"status": "skipped", "reason": reason}))
            self.assertTrue(self.post(WAV)["cache_hit"], reason)
            self.write_stored(self.with_separation({"status": "skipped", "reason": reason}))
            self.assertFalse(self.post(FULL)["cache_hit"], reason)
        self.write_stored(self.with_separation({"status": "skipped", "reason": "band_limited"}))
        self.assertTrue(self.post(FULL)["cache_hit"])
        self.write_stored(self.with_separation({"status": "done"}))
        self.assertTrue(self.post(WAV)["cache_hit"])  # the better melody stays for a 16 kHz upload of the same audio
        self.assertTrue(self.post(FULL)["cache_hit"])

    def test_16k_upload_keeps_stems_when_the_worker_changed(self):
        kept = dict(self.with_separation({"status": "done"}), worker_sha256="0" * 64)
        self.write_stored(kept)
        calls = len(self.fw.calls())
        self.assertTrue(self.post(WAV)["cache_hit"])
        self.assertEqual(len(self.fw.calls()), calls)
        with open(self.rhythm_file()) as f:
            self.assertEqual(json.load(f), kept)
        self.assertFalse(self.post(FULL)["cache_hit"])  # a full-band upload still brings it up to date

    def test_16k_upload_keeps_stems_when_the_words_changed(self):
        self.write_stored(dict(self.with_separation({"status": "done"}), words_sha256="0" * 64))
        self.assertTrue(self.post(WAV)["cache_hit"])

    def test_16k_upload_recomputes_a_result_without_stems(self):
        self.write_stored(dict(self.with_separation({"status": "skipped", "reason": "canonical_16k"}), worker_sha256="0" * 64))
        self.assertFalse(self.post(WAV)["cache_hit"])

    def test_separated_layers(self):
        done, skipped = {"status": "done"}, {"status": "skipped", "reason": "canonical_16k"}
        for k in ("bass", "other", "drums", "sections_model", "parts"):
            self.assertTrue(server._has_separated_layer({k: done}), k)
            self.assertFalse(server._has_separated_layer({k: skipped}), k)
        self.assertFalse(server._has_separated_layer({"melody": dict(MELODY, separation=skipped)}))
        self.assertFalse(server._has_separated_layer({"melody": {"status": "failed"}}))

    def test_failed_separation_retried(self):
        for reason in sorted(server.SEP_RETRY_REASONS):
            self.write_stored(self.with_separation({"status": "skipped", "reason": reason}))
            self.assertFalse(self.post(WAV)["cache_hit"], reason)

    def test_method_texts_pass_the_validator(self):
        for method, sp in ((rw.MEL_METHOD_VOCAL, {"status": "done"}),
                           (rw.MEL_METHOD, {"status": "skipped", "reason": "not_configured"})):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY, method=method, separation=sp))))
            self.assertIs(server.check_rhythm(v), v, method)

    def test_separation_field_checked(self):
        good = ({"status": "done"}, {"status": "skipped", "reason": "band_limited"})
        for sp in good:
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY, separation=sp))))
            self.assertIs(server.check_rhythm(v), v, sp)
        for sp in (None, "done", {}, {"status": "done", "reason": "x"}, {"status": "skipped"},
                   {"status": "skipped", "reason": "other"}, {"status": "failed", "reason": "band_limited"}):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY, separation=sp))))
            self.assertIsNone(server.check_rhythm(v), sp)

    def test_cleanup_removes_a_left_scratch(self):
        d = os.path.join(self.h.app.uploads, "op-x", server.SEP_DIR)
        os.makedirs(d)
        with open(os.path.join(d, "stem16.wav"), "w") as f:
            f.write("x")
        self.h.app.cleanup("op-x", False)
        self.assertFalse(os.path.exists(os.path.dirname(d)))


class SeparationConfigTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        self.exe = os.path.join(self.env.dir, "exe")
        with open(self.exe, "w") as f:
            f.write("x")
        os.chmod(self.exe, 0o700)

    def tearDown(self):
        self.env.cleanup()

    def load(self, sp):
        rh = {"python": PY, "worker": self.fw.path, "separation": sp}
        return server.load_config(self.env.write_config(self.env.config(rhythm=rh)))

    def test_valid(self):
        rh = self.load({"demucs": self.exe, "rmvpe": self.exe})["rhythm"]
        self.assertEqual((rh["demucs"], rh["rmvpe"]), (self.exe, self.exe))

    def test_refused(self):
        plain = os.path.join(self.env.dir, "plain")
        with open(plain, "w") as f:
            f.write("x")
        os.chmod(plain, 0o600)
        for sp in (None, {}, {"demucs": self.exe}, {"demucs": self.exe, "rmvpe": self.exe, "x": 1},
                   {"demucs": "rel", "rmvpe": self.exe}, {"demucs": self.exe, "rmvpe": "/nonexistent"},
                   {"demucs": plain, "rmvpe": plain}):
            with self.assertRaises(server.ConfigError, msg=str(sp)):
                self.load(sp)


class PinTest(unittest.TestCase):
    def test_rmvpe_code_pin_matches_the_file(self):
        with open(os.path.join(REPO, "rmvpe_model.py"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), rw.RMVPE_CODE_SHA256)


DRIVER = r'''
import json, os, sys, wave, hashlib
sys.path.insert(0, sys.argv[1])
import rhythm_worker as rw
import numpy as np
cfg = json.loads(sys.argv[2])
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
rw.RMVPE_CODE = cfg["rmvpe_code"]
rw.RMVPE_CODE_SHA256 = sha(cfg["rmvpe_code"])
rw.RMVPE_SHA256 = cfg.get("rmvpe_sha", sha(cfg["sep"]["rmvpe"])) if cfg["sep"] else ""
rw.DEMUCS_WEIGHTS_SHA256 = sha(os.path.expanduser(rw.DEMUCS_WEIGHTS))
t = np.arange(16000 * 4) / 16000.0
x = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
m, notes, warns = rw.melody(x, 4.0, "A", "minor", [], cfg["sep"])
print(json.dumps({"melody": m, "notes": notes, "warns": warns}))
'''
FAKE_DEMUCS = r'''#!%s -I
import shutil, sys, os
a = sys.argv[1:]
if "%s" == "fail":
    sys.exit(3)
d = a[a.index("-o") + 1]
os.makedirs(os.path.join(d, "full"))
shutil.copy(a[-1], os.path.join(d, "full", "vocals.wav"))
'''
FAKE_RMVPE = r'''import sys, wave
import numpy as np
if "%s" == "fail":
    sys.exit(4)
with wave.open(sys.argv[2]) as w:
    n = w.getnframes() // 160 + 1
np.save(sys.argv[3], np.full(n, 440.0))
'''


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class WorkerFallbackTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mlsep-")
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
        self.noise, self.tone = os.path.join(self.d, "noise.wav"), os.path.join(self.d, "tone.wav")
        gen = ("import sys, wave, numpy as np\n"
               "t = np.arange(44100 * 4) / 44100.0\n"
               "s = 0.2 * np.sin(2 * np.pi * 440.0 * t)\n"
               "r = np.random.default_rng(0).normal(0, 0.1, len(t))\n"
               "for p, y in ((sys.argv[1], s + r), (sys.argv[2], s)):\n"
               "    w = wave.open(p, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)\n"
               "    w.writeframes((np.clip(y, -1, 1) * 32767).astype('<i2').tobytes()); w.close()\n")
        subprocess.run([ESSENTIA_PY, "-I", "-c", gen, self.noise, self.tone], check=True, timeout=60)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_melody(self, source="noise", demucs="ok", rmvpe="ok", configured=True, **cfg):
        dem, code = os.path.join(self.d, "demucs-" + demucs), os.path.join(self.d, "rmvpe-" + rmvpe + ".py")
        with open(dem, "w") as f:
            f.write(FAKE_DEMUCS % (ESSENTIA_PY, demucs))
        os.chmod(dem, 0o700)
        with open(code, "w") as f:
            f.write(FAKE_RMVPE % rmvpe)
        self.scratch = os.path.join(self.d, "scratch")
        sep = {"demucs": dem, "rmvpe": self.model, "scratch": self.scratch}
        if source:
            sep["source"] = {"noise": self.noise, "tone": self.tone}.get(source, source)
        arg = dict(cfg, sep=sep if configured else None, rmvpe_code=code)
        p = subprocess.run([ESSENTIA_PY, "-I", self.driver, REPO, json.dumps(arg)], capture_output=True, timeout=120,
                           env={"PATH": server.CHILD_ENV_PATH, "HOME": self.home, "LANG": "en_US.UTF-8"})
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout)

    def assert_mix(self, out, reason, warned):
        m = out["melody"]
        self.assertEqual((m["status"], m["separation"]), ("done", {"status": "skipped", "reason": reason}))
        self.assertEqual(m["method"], rw.MEL_METHOD)
        self.assertEqual({n["midi"] for n in out["notes"]}, {57})  # the 220 Hz mix, not the fake 440 Hz vocal
        self.assertEqual(any(w.startswith("melody_separation_failed") for w in out["warns"]), warned)
        self.assertFalse(os.path.exists(self.scratch))

    def test_done_uses_the_vocal(self):
        out = self.run_melody()
        self.assertEqual(out["melody"]["separation"], {"status": "done"})
        self.assertEqual(out["melody"]["method"], rw.MEL_METHOD_VOCAL)
        self.assertEqual({n["midi"] for n in out["notes"]}, {69})
        self.assertEqual(out["warns"], [])
        self.assertFalse(os.path.exists(self.scratch))

    def test_not_configured(self):
        self.assert_mix(self.run_melody(configured=False), "not_configured", False)

    def test_canonical(self):
        self.assert_mix(self.run_melody(source=None), "canonical_16k", False)

    def test_band_limited(self):
        self.assert_mix(self.run_melody(source="tone"), "band_limited", False)

    def test_models_invalid(self):
        self.assert_mix(self.run_melody(rmvpe_sha="0" * 64), "models_invalid", True)

    def test_decode_failed(self):
        self.assert_mix(self.run_melody(source="/nonexistent/upload.m4a"), "decode_failed", True)

    def test_separation_failed(self):
        self.assert_mix(self.run_melody(demucs="fail"), "separation_failed", True)

    def test_rmvpe_failed(self):
        self.assert_mix(self.run_melody(rmvpe="fail"), "rmvpe_failed", True)


if __name__ == "__main__":
    unittest.main()
