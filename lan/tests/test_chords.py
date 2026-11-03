"""BTC chords (worker 0.7.0): server wiring with a fake worker, and the worker's fallbacks with a fake BTC process."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import PY, REPO, Client, Env, Harness, server
from test_rhythm import AR, ESSENTIA_PY, RHYTHM_OUT_V40, WAV, FakeWorker, RhythmBase, rw


def with_chords(rows=None, **top):
    return json.loads(json.dumps(dict(RHYTHM_OUT_V40, chords=rows if rows is not None else RHYTHM_OUT_V40["chords"], **top)))


class ChordServerTest(RhythmBase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        self.model = os.path.join(self.env.dir, "btc.pt")
        with open(self.model, "w") as f:
            f.write("x")
        os.chmod(self.model, 0o600)
        self.h = Harness(self.env, rhythm={"python": PY, "worker": self.fw.path, "chord_model": self.model})
        self.c = Client(self.env, self.h.port)
        self.write_result()
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V40))

    def test_model_passed_to_the_worker(self):
        self.assertEqual(self.c.json("POST", AR, WAV)[1]["rhythm"]["status"], "done")
        argv = self.fw.calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--chord-model") + 1], self.model)
        self.assertNotIn("--demucs", argv)
        self.assertTrue(argv[-1].endswith(".wav"))

    def test_status(self):
        self.assertTrue(self.c.json("GET", "/status")[1]["rhythm"]["chord_model_configured"])


class ChordValidatorTest(unittest.TestCase):
    ROW = {"start": 0.0, "end": 0.5, "label": "A#mMaj7", "strength": None}

    def test_new_output_passes(self):
        v = with_chords([dict(self.ROW, one_beat=True)], chords_reliability="reference_candidate", chord_vocabulary=rw.CHORD_VOCABULARY)
        self.assertIs(server.check_rhythm(v), v)

    def test_old_output_passes(self):
        v = with_chords([dict(self.ROW, label="Am", strength=0.5)], chords_reliability="low_reference")
        self.assertIs(server.check_rhythm(v), v)

    def test_refused(self):
        for bad in (with_chords([dict(self.ROW, one_beat=False)]), with_chords([dict(self.ROW, one_beat="yes")]),
                    with_chords(chords_reliability="certain"), with_chords(chord_vocabulary="x" * 201)):
            self.assertIsNone(server.check_rhythm(bad))


class ChordConfigTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)

    def tearDown(self):
        self.env.cleanup()

    def load(self, path):
        rh = {"python": PY, "worker": self.fw.path, "chord_model": path}
        return server.load_config(self.env.write_config(self.env.config(rhythm=rh)))

    def test_valid(self):
        p = os.path.join(self.env.dir, "m.pt")
        with open(p, "w") as f:
            f.write("x")
        os.chmod(p, 0o600)
        self.assertEqual(self.load(p)["rhythm"]["chord_model"], p)

    def test_refused(self):
        wide = os.path.join(self.env.dir, "wide.pt")
        with open(wide, "w") as f:
            f.write("x")
        os.chmod(wide, 0o666)
        for path in ("rel.pt", "/nonexistent/m.pt", wide, self.env.dir):
            with self.assertRaises(server.ConfigError, msg=path):
                self.load(path)


class ChordPinTest(unittest.TestCase):
    def test_code_pin_matches_the_file(self):
        with open(os.path.join(REPO, "btc_model.py"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), rw.BTC_CODE_SHA256)

    def test_vocabulary_fits_the_validator(self):
        self.assertLessEqual(len(rw.CHORD_VOCABULARY), 200)

    def test_one_beat_marks_only_rows_without_a_beat_inside(self):
        rows = [{"start": 0.0, "end": 0.5}, {"start": 0.5, "end": 1.0}, {"start": 1.0, "end": 2.0}]
        rw.mark_one_beat(rows, [0.5, 1.0, 1.5])
        self.assertEqual([r.get("one_beat") for r in rows], [True, True, None])


DRIVER = r'''
import json, os, sys, hashlib
sys.path.insert(0, sys.argv[1])
import rhythm_worker as rw
cfg = json.loads(sys.argv[2])
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
rw.BTC_CODE, rw.BTC_CODE_SHA256 = cfg["code"], sha(cfg["code"])
rw.BTC_SHA256 = cfg.get("sha", sha(cfg["model"]))
frames, reason = rw.btc_frames(cfg["model"], "/nonexistent.wav", 1.0)
print(json.dumps({"frames": frames, "reason": reason}))
'''
FAKE = {
    "ok": 'import json, sys\njson.dump({"hop_s": 0.5, "labels": ["Am7", "N", "G"]}, open(sys.argv[3], "w"))\n',
    "exit": "import sys\nsys.exit(4)\n",
    "garbage": 'import sys\nopen(sys.argv[3], "w").write("not json")\n',
    "empty": 'import json, sys\njson.dump({"hop_s": 0.5, "labels": []}, open(sys.argv[3], "w"))\n',
    "nolabels": 'import json, sys\njson.dump({"hop_s": 0.5}, open(sys.argv[3], "w"))\n',
}


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class ChordFallbackTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mlchord-")
        self.model = os.path.join(self.d, "btc.pt")
        with open(self.model, "w") as f:
            f.write("model")
        self.driver = os.path.join(self.d, "driver.py")
        with open(self.driver, "w") as f:
            f.write(DRIVER)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_fake(self, kind, **cfg):
        code = os.path.join(self.d, kind + ".py")
        with open(code, "w") as f:
            f.write(FAKE[kind])
        p = subprocess.run([ESSENTIA_PY, "-I", self.driver, REPO, json.dumps(dict(cfg, code=code, model=self.model))],
                           capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return json.loads(p.stdout)

    def test_frames(self):
        out = self.run_fake("ok")
        self.assertIsNone(out["reason"])
        self.assertEqual(out["frames"], [[0.0, "Am7", None], [0.5, "N", None], [1.0, "G", None]])

    def test_models_invalid(self):
        self.assertEqual(self.run_fake("ok", sha="0" * 64), {"frames": None, "reason": "models_invalid"})

    def test_failures(self):
        for kind in ("exit", "garbage", "empty", "nolabels"):
            self.assertEqual(self.run_fake(kind), {"frames": None, "reason": "btc_failed"}, kind)


if __name__ == "__main__":
    unittest.main()
