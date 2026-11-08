"""Sections from All-In-One (worker 0.11.0): server wiring with a fake worker, the worker's fallbacks with fake Demucs and helper, and the row builder."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import PY, REPO, Client, Env, Harness, server
from test_rhythm import AR, ESSENTIA_PY, RHYTHM_OUT_V40, WAV, FakeWorker, RhythmBase, rw
from test_separation import FULL

PLAIN = RHYTHM_OUT_V40["sections"]
ROWS = [dict(r, function="verse") for r in PLAIN]
DONE = {"status": "done"}


def out(model=DONE, rows=ROWS):
    return json.loads(json.dumps(dict(RHYTHM_OUT_V40, sections_model=model, sections=rows)))


class SectionServerTest(RhythmBase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        paths = {k: os.path.join(self.env.dir, k) for k in ("demucs", "rmvpe", "allin1py", "fold0.pth")}
        for p in paths.values():
            with open(p, "w") as f:
                f.write("x")
            os.chmod(p, 0o700)
        self.paths = paths
        self.h = Harness(self.env, rhythm={"python": PY, "worker": self.fw.path,
                                           "separation": {"demucs": paths["demucs"], "rmvpe": paths["rmvpe"]},
                                           "sections": {"allin1_python": paths["allin1py"], "allin1_model": paths["fold0.pth"]}})
        self.c = Client(self.env, self.h.port)
        self.write_result()
        self.fw.write(stdout=json.dumps(out()))

    def post(self, body):
        return self.c.json("POST", AR, body)[1]["rhythm"]

    def store(self, **fields):
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(dict(RHYTHM_OUT_V40, **fields, **self.ids())))

    def test_flags_passed(self):
        self.assertEqual(self.post(WAV)["status"], "done")
        argv = self.fw.calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--sections-python") + 1], self.paths["allin1py"])
        self.assertEqual(argv[argv.index("--sections-model") + 1], self.paths["fold0.pth"])

    def test_status(self):
        self.assertTrue(self.c.json("GET", "/status")[1]["rhythm"]["sections_configured"])

    def test_summary(self):
        r = self.post(WAV)
        self.assertEqual((r["sections_model"], r["sections"]), (DONE, ROWS))

    def test_reuse(self):
        skipped = lambda reason: dict(sections_model={"status": "skipped", "reason": reason})
        for fields, body, hit in ((dict(sections_model=DONE, sections=ROWS), FULL, True),
                                  (skipped("band_limited"), FULL, True),  # final: no recompute on every request
                                  (skipped("too_many_sections"), FULL, True),  # same audio, same count
                                  (skipped("sections_failed"), FULL, False),
                                  (skipped("canonical_16k"), FULL, False),
                                  ({}, FULL, False),
                                  ({}, WAV, True),  # a 16 kHz upload cannot give stems
                                  (skipped("sections_failed"), WAV, True)):
            self.store(**fields)
            self.assertEqual(self.post(body)["cache_hit"], hit, (fields, body is FULL))


class SectionCheckTest(unittest.TestCase):
    def check(self, **fields):
        return server.check_rhythm(json.loads(json.dumps(dict(RHYTHM_OUT_V40, **fields))))

    def test_passes(self):
        self.assertIsNotNone(self.check(sections_model=DONE, sections=ROWS, sections_reliability="reference_candidate"))
        self.assertIsNotNone(self.check(sections_model={"status": "skipped", "reason": "too_many_sections"}))
        self.assertIsNotNone(self.check())

    def test_refused(self):
        for fields in (dict(sections=ROWS),  # a function with no model record
                       dict(sections_model=DONE),
                       dict(sections_model=DONE, sections=[dict(r, function="hook") for r in PLAIN]),
                       dict(sections_model={"status": "skipped", "reason": "sections_failed"}, sections=ROWS),
                       dict(sections_model={"status": "skipped", "reason": "nope"}),
                       dict(sections_model={"status": "done", "extra": 1}, sections=ROWS),
                       dict(sections_model="done")):
            self.assertIsNone(self.check(**fields), fields)


class SectionOverviewTest(unittest.TestCase):
    def overview(self, **fields):
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, **fields)))
        v["lyric_repeats"] = {"status": "unavailable", "reason": "no_words"}
        return server.rhythm_overview(v, None)

    def test_says_where_the_sections_came_from(self):
        o = self.overview(sections_model=DONE, sections=ROWS)
        self.assertEqual(o["status"], "done")
        self.assertIn("All-In-One", o["method"])
        self.assertNotIn("near chance", o["notes"])
        o = self.overview()
        self.assertIn("Foote", o["method"])
        self.assertIn("near chance", o["notes"])


class SectionConfigTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.paths = {k: os.path.join(self.env.dir, k) for k in ("worker", "demucs", "rmvpe", "py", "m")}
        for p in self.paths.values():
            with open(p, "w") as f:
                f.write("x")
            os.chmod(p, 0o700)
        self.good = {"allin1_python": self.paths["py"], "allin1_model": self.paths["m"]}

    def tearDown(self):
        self.env.cleanup()

    def load(self, sections, separation=True):
        rh = {"python": PY, "worker": self.paths["worker"], "sections": sections}
        if separation:
            rh["separation"] = {"demucs": self.paths["demucs"], "rmvpe": self.paths["rmvpe"]}
        return server.load_config(self.env.write_config(self.env.config(rhythm=rh), 0o600))

    def test_good(self):
        self.assertEqual(self.load(self.good)["rhythm"]["allin1_model"], self.paths["m"])

    def test_refused(self):
        for sections, separation in ((self.good, False), ({"allin1_python": self.paths["py"]}, True),
                                     (dict(self.good, allin1_model="/nonexistent"), True)):
            with self.assertRaises(server.ConfigError):
                self.load(sections, separation)


DRIVER = r'''
import json, os, sys, hashlib
sys.path.insert(0, sys.argv[1])
import numpy as np
import rhythm_worker as rw
cfg = json.loads(sys.argv[2])
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
rw.RMVPE_CODE, rw.RMVPE_CODE_SHA256 = cfg["rmvpe_code"], sha(cfg["rmvpe_code"])
rw.RMVPE_SHA256 = sha(cfg["sep"]["rmvpe"])
rw.DEMUCS_WEIGHTS_SHA256 = sha(os.path.expanduser(rw.DEMUCS_WEIGHTS))
root = os.path.dirname(cfg["sep"]["sections_model"])
rw.ALLIN1_WEIGHTS = tuple((n, cfg.get("weight_sha", sha(os.path.join(root, n)))) for n in cfg["weights"])
rw.ALLIN1_CODE, rw.ALLIN1_CODE_SHA256 = cfg["helper"], sha(cfg["helper"])
sep = cfg["sep"]
_, _, field = rw.melody_input(sep, 16000 * 2)
print(json.dumps({"separation": field, "sections": sep.get("sections"), "downbeats": sep.get("downbeats"), "beats": sep.get("model_beats"), "scratch_left": os.path.exists(sep["scratch"])}))
'''
FAKE_DEMUCS = r'''#!%s -I
import os, shutil, sys
a = sys.argv[1:]
stem = a[a.index("--stem") + 1] if "--stem" in a else "all"
with open("%s", "a") as f:
    f.write(stem + "\n")
if stem == "all":  # one run for all four stems; it fails when any stem is set to fail
    if "%s".strip(","):
        sys.exit(3)
    d = os.path.join(a[a.index("-o") + 1], "full")
    os.makedirs(d, exist_ok=True)
    for s in ("vocals", "bass", "drums", "other"):
        shutil.copy(a[-1], os.path.join(d, s + ".wav"))
    sys.exit(0)
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
FAKE_HELPER = r'''import json, os, sys
mode, model_dir, stems, out = "%s", sys.argv[1], sys.argv[2], sys.argv[3]
assert all(os.path.isfile(os.path.join(stems, s + ".wav")) for s in ("vocals", "bass", "drums", "other"))
if mode == "fail":
    sys.exit(4)

if mode == "bad":
    rows = [{"start": 0, "end": 1, "label": "hook"}]
elif mode == "many":
    rows = [{"start": i * 0.04, "end": i * 0.04 + 0.06, "label": "verse"} for i in range(45)]
else:
    rows = [{"start": 0, "end": 0, "label": "start"}, {"start": 0, "end": 0.9, "label": "intro"},
            {"start": 0.9, "end": 2.5, "label": "chorus"}, {"start": 5, "end": 6, "label": "end"}]
json.dump({"segments": rows, "downbeats": [0.5, 1.0, 1.5], "beats": [0.5, 0.75, 1.0, 9.0]}, open(out, "w"))
'''


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class SectionFallbackTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mlsec-")
        self.home = os.path.join(self.d, "home")
        os.makedirs(os.path.join(self.home, ".cache", "demucs-mlx"))
        with open(os.path.join(self.home, ".cache", "demucs-mlx", "htdemucs_ft.safetensors"), "w") as f:
            f.write("weights")
        self.weights = ["a0.pth", "a1.pth"]
        for name in ["rmvpe.pt"] + self.weights:
            with open(os.path.join(self.d, name), "w") as f:
                f.write(name)
        self.driver = os.path.join(self.d, "driver.py")
        with open(self.driver, "w") as f:
            f.write(DRIVER)
        self.log = os.path.join(self.d, "stems.log")
        self.noise = os.path.join(self.d, "noise.wav")
        gen = ("import sys, wave, numpy as np\n"
               "y = np.random.default_rng(0).normal(0, 0.1, 44100 * 2)\n"
               "w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)\n"
               "w.writeframes((np.clip(y, -1, 1) * 32767).astype('<i2').tobytes()); w.close()\n")
        subprocess.run([ESSENTIA_PY, "-I", "-c", gen, self.noise], check=True, timeout=60)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_it(self, demucs_fail="", helper="ok", source="noise", model="a0.pth", **cfg):
        files = {}
        for name, text in (("demucs", FAKE_DEMUCS % (ESSENTIA_PY, self.log, demucs_fail, demucs_fail)), ("rmvpe.py", FAKE_RMVPE),
                           ("helper.py", FAKE_HELPER % helper)):
            files[name] = os.path.join(self.d, name)
            with open(files[name], "w") as f:
                f.write(text)
            os.chmod(files[name], 0o700)
        sep = {"demucs": files["demucs"], "rmvpe": os.path.join(self.d, "rmvpe.pt"), "scratch": os.path.join(self.d, "scratch"),
               "sections_python": ESSENTIA_PY, "sections_model": os.path.join(self.d, model)}
        if source:
            sep["source"] = self.noise if source == "noise" else source
        p = subprocess.run([ESSENTIA_PY, "-I", self.driver, REPO, json.dumps(dict(
            cfg, sep=sep, rmvpe_code=files["rmvpe.py"], helper=files["helper.py"], weights=self.weights))],
            capture_output=True, timeout=120, env={"PATH": server.CHILD_ENV_PATH, "HOME": self.home, "LANG": "en_US.UTF-8"})
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        r = json.loads(p.stdout)
        self.assertFalse(r["scratch_left"])
        return r

    def assert_skipped(self, r, reason):
        self.assertEqual(r["sections"], [{"status": "skipped", "reason": reason}, []])

    def test_done(self):
        got = self.run_it()
        self.assertEqual(got["downbeats"], [0.5, 1.0, 1.5])
        self.assertEqual(got["beats"], [0.5, 0.75, 1.0])  # past the end dropped
        field, segs = got["sections"]
        self.assertEqual(field, {"status": "done"})
        # the empty start row goes, the gap joins the row before it, the last row past the end is gone
        self.assertEqual(segs, [[0.0, 0.9, "intro"], [0.9, 2.0, "chorus"]])
        with open(self.log) as f:
            self.assertEqual(f.read().split(), ["all"])  # one Demucs run gives every stage its stem

    def test_failures(self):
        self.assert_skipped(self.run_it(helper="fail"), "sections_failed")
        self.assert_skipped(self.run_it(helper="bad"), "sections_failed")
        self.assert_skipped(self.run_it(helper="many"), "too_many_sections")
        self.assert_skipped(self.run_it(demucs_fail="other"), "separation_failed")
        self.assert_skipped(self.run_it(model="a1.pth"), "models_invalid")
        self.assert_skipped(self.run_it(weight_sha="0" * 64), "models_invalid")
        self.assert_skipped(self.run_it(source="/nonexistent/upload.m4a"), "decode_failed")

    def test_vocal_failure_keeps_sections(self):
        r = self.run_it(demucs_fail="vocals")
        self.assertEqual(r["separation"], {"status": "skipped", "reason": "separation_failed"})
        self.assert_skipped(r, "separation_failed")

    def test_canonical(self):
        self.assert_skipped(self.run_it(source=None), "canonical_16k")


ROWS_DRIVER = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import numpy as np
import rhythm_worker as rw
n = 40
chroma = np.zeros((n, 12)); chroma[:20, 0] = 1; chroma[20:, 7] = 1
mfcc = np.zeros((n, 12)); mfcc[20:] = 1
times = [i * 0.5 for i in range(n)]
energy = [-20.0] * 16 + [-120.0] * 4
chords = [{"start": 0.0, "end": 8.0, "label": "C"}]
segs = [(0.0, 4.0, "verse"), (4.0, 8.0, "chorus"), (8.0, 16.0, "verse"), (16.0, 20.0, "end")]
print(json.dumps(rw.sections_from_segments(segs, chroma, mfcc, times, 20.0, energy, chords)))
'''


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class SectionRowsTest(unittest.TestCase):
    def test_rows(self):
        p = subprocess.run([ESSENTIA_PY, "-I", "-c", ROWS_DRIVER, REPO], capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        rows = json.loads(p.stdout)
        self.assertEqual([r["label"] for r in rows], ["A", "B", "A", "silence"])
        self.assertEqual([r["function"] for r in rows], ["verse", "chorus", "verse", "end"])
        self.assertEqual(rows[0]["similarity_to_first"], 1.0)
        self.assertEqual(rows[1]["similarity_to_first"], 1.0)
        self.assertLess(rows[2]["similarity_to_first"], 1.0)  # the later verse has other chroma and timbre
        self.assertIsNone(rows[3]["similarity_to_first"])
        self.assertEqual(rows[0]["top_chord"], "C")


class SectionPinTest(unittest.TestCase):
    def test_code_pin_matches_the_file(self):
        with open(os.path.join(REPO, "allin1_sections.py"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), rw.ALLIN1_CODE_SHA256)

    def test_helper_lists_the_pinned_weights(self):
        ns = {}
        with open(os.path.join(REPO, "allin1_sections.py")) as f:
            src = f.read()
        exec(src[src.index("CHECKPOINTS ="):src.index("def main")], ns)
        self.assertEqual(ns["CHECKPOINTS"], tuple(n for n, _ in rw.ALLIN1_WEIGHTS))

    def test_functions_agree(self):
        self.assertEqual(set(rw.SEC_FUNCTIONS), set(server.SECTION_FUNCTIONS))
        self.assertEqual(set(rw.SEC_MODEL_FAIL_REASONS), set(server.SECTIONS_MODEL_FAIL_REASONS))
