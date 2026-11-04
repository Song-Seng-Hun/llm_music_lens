"""Accompaniment layer (worker 0.12.0): server wiring with a fake worker, and the worker's handling with fake Demucs and Basic Pitch."""
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

DONE = {"status": "done", "method": rw.OTHER_METHOD, "reliability": "low_reference", "note_count": 1}
NOTE = {"start": 0.1, "end": 0.5, "midi": 64, "note": "E4"}
BASS = {"status": "done", "method": rw.BASS_METHOD, "reliability": "low_reference", "note_count": 0}


def out(other=DONE, notes=(NOTE,)):
    return json.loads(json.dumps(dict(RHYTHM_OUT_V40, bass=BASS, bass_notes=[], other=other, other_notes=list(notes))))


class OtherServerTest(RhythmBase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        paths = {k: os.path.join(self.env.dir, k) for k in ("demucs", "rmvpe", "bp")}
        for p in paths.values():
            with open(p, "w") as f:
                f.write("x")
            os.chmod(p, 0o700)
        self.h = Harness(self.env, rhythm={"python": PY, "worker": self.fw.path,
                                           "separation": {"demucs": paths["demucs"], "rmvpe": paths["rmvpe"]},
                                           "bass": {"basic_pitch_python": paths["bp"]}})
        self.c = Client(self.env, self.h.port)
        self.write_result()
        self.fw.write(stdout=json.dumps(out()))

    def post(self, body):
        return self.c.json("POST", AR, body)[1]["rhythm"]

    def store(self, other):
        fields = dict(other=other, other_notes=[NOTE] if other.get("status") == "done" else []) if other else {}
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(dict(RHYTHM_OUT_V40, bass=BASS, bass_notes=[], **fields, **self.ids())))

    def test_summary_and_timeline(self):
        self.assertEqual(self.post(WAV)["other"], DONE)
        st, b = self.c.json("GET", "/timeline/%s?layer=other" % RID64)
        self.assertEqual((st, b["other"], b["reliability"], b["layer_status"]), (200, [NOTE], "low_reference", "done"))

    def test_not_stored(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V40))
        self.assertIsNone(self.post(WAV)["other"])
        self.assertEqual(self.c.json("GET", "/timeline/%s?layer=other" % RID64)[1]["error"]["code"], "rhythm_not_found")

    def test_reuse(self):
        self.store(DONE)
        self.assertTrue(self.post(FULL)["cache_hit"])
        for other, body, hit in (({"status": "skipped", "reason": "too_many_notes"}, FULL, True),
                                 ({"status": "skipped", "reason": "band_limited"}, FULL, True),
                                 ({"status": "skipped", "reason": "notes_failed"}, WAV, True),
                                 (None, WAV, True), (None, FULL, False),
                                 ({"status": "skipped", "reason": "notes_failed"}, FULL, False),
                                 ({"status": "skipped", "reason": "canonical_16k"}, FULL, False)):
            self.store(other)
            self.assertEqual(self.post(body)["cache_hit"], hit, (other, body is FULL))


class OtherValidatorTest(unittest.TestCase):
    def test_passes(self):
        for v in (out(), out({"status": "skipped", "reason": "too_many_notes"}, ()), json.loads(json.dumps(RHYTHM_OUT_V40))):
            self.assertIs(server.check_rhythm(v), v)

    def test_method_fits(self):
        self.assertLessEqual(len(rw.OTHER_METHOD), 200)

    def test_caps_agree(self):
        self.assertEqual(server.OTHER_MAX_NOTES, rw.OTHER_MAX_NOTES)

    def test_refused(self):
        lone = json.loads(json.dumps(dict(RHYTHM_OUT_V40, other=DONE)))
        for v in (lone, out(dict(DONE, note_count=2)), out(dict(DONE, reliability="high")),
                  out({"status": "skipped", "reason": "rmvpe_failed"}, ()), out({"status": "skipped", "reason": "band_limited"}),
                  out(notes=(dict(NOTE, note="C4"),)), out(notes=(dict(NOTE, end=0.1),))):
            self.assertIsNone(server.check_rhythm(v))


DRIVER = r'''
import json, os, sys, hashlib
sys.path.insert(0, sys.argv[1])
import rhythm_worker as rw
cfg = json.loads(sys.argv[2])
rw.OTHER_NOTES_CODE, rw.OTHER_NOTES_SHA256 = cfg["code"], cfg.get("code_sha") or hashlib.sha256(open(cfg["code"], "rb").read()).hexdigest()
if "cap" in cfg:
    rw.OTHER_MAX_NOTES = cfg["cap"]
sep = cfg["sep"]
rw.other_input(sep, 16000 * 2, None)
print(json.dumps(sep["other"]))
'''
FAKE_DEMUCS = r'''#!/bin/sh
[ "%s" = fail ] && exit 3
echo run >> "%s"
mkdir -p "$(dirname "$0")/scratch/full"
: > "$(dirname "$0")/scratch/full/other.wav"
'''
FAKE_NOTES = r'''import json, sys
rows = json.loads(%r)
if rows == "fail":
    sys.exit(5)
json.dump(rows, open(sys.argv[2], "w"))
'''


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class OtherWorkerTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mlother-")
        self.driver = os.path.join(self.d, "driver.py")
        with open(self.driver, "w") as f:
            f.write(DRIVER)
        self.calls = os.path.join(self.d, "demucs-calls")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_other(self, rows, demucs="ok", full=True, stem=False, **cfg):
        scratch = os.path.join(self.d, "scratch")
        shutil.rmtree(scratch, ignore_errors=True)
        os.makedirs(os.path.join(scratch, "full"))
        if full:
            open(os.path.join(scratch, "full.wav"), "w").close()
        if stem:
            open(os.path.join(scratch, "full", "other.wav"), "w").close()
        paths = {n: os.path.join(self.d, n) for n in ("demucs", "bp", "code.py")}
        for name, text in (("demucs", FAKE_DEMUCS % (demucs, self.calls)), ("bp", '#!/bin/sh\nexec %s "$@"\n' % ESSENTIA_PY),
                           ("code.py", FAKE_NOTES % json.dumps(rows))):
            with open(paths[name], "w") as f:
                f.write(text)
            os.chmod(paths[name], 0o700)
        sep = {"demucs": paths["demucs"], "basic_pitch": paths["bp"], "scratch": scratch}
        p = subprocess.run([ESSENTIA_PY, "-I", self.driver, REPO, json.dumps(dict(cfg, sep=sep, code=paths["code.py"]))],
                           capture_output=True, timeout=120, env={"PATH": server.CHILD_ENV_PATH, "HOME": self.d, "LANG": "en_US.UTF-8"})
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout)

    def test_rows_sorted_clipped_named(self):
        field, rows = self.run_other([[1.0, 1.5, 60], [0.2, 0.7, 67], [0.2, 0.7, 64], [1.9, 9.0, 72], [3.0, 4.0, 60]])
        self.assertEqual((field["status"], field["note_count"], field["reliability"]), ("done", 4, "low_reference"))
        self.assertEqual(rows, [{"start": 0.2, "end": 0.7, "midi": 64, "note": "E4"}, {"start": 0.2, "end": 0.7, "midi": 67, "note": "G4"},
                                {"start": 1.0, "end": 1.5, "midi": 60, "note": "C4"}, {"start": 1.9, "end": 2.0, "midi": 72, "note": "C5"}])

    def test_existing_stem_is_reused(self):
        self.assertEqual(self.run_other([[0.1, 0.5, 60]], stem=True)[0]["status"], "done")
        self.assertFalse(os.path.exists(self.calls))
        self.assertEqual(self.run_other([[0.1, 0.5, 60]])[0]["status"], "done")
        self.assertTrue(os.path.exists(self.calls))

    def test_failures(self):
        for kw, reason in ((dict(rows="fail"), "notes_failed"), (dict(rows=[[0.0, "x", 60]]), "notes_failed"), (dict(rows=[[0.0, 1.0, 200]]), "notes_failed"),
                           (dict(rows={"a": 1}), "notes_failed"), (dict(rows=[], demucs="fail"), "separation_failed"),
                           (dict(rows=[], code_sha="0" * 64), "models_invalid"), (dict(rows=[], full=False), "separation_failed"),
                           (dict(rows=[[0.1, 0.5, 60], [0.2, 0.5, 61]], cap=1), "too_many_notes")):
            self.assertEqual(self.run_other(**kw), [{"status": "skipped", "reason": reason}, []], kw)


class OtherPinTest(unittest.TestCase):
    def test_code_pin_matches_the_file(self):
        with open(os.path.join(REPO, "other_notes.py"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), rw.OTHER_NOTES_SHA256)


if __name__ == "__main__":
    unittest.main()
