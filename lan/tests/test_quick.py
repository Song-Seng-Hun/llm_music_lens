"""rhythm=quick (POST /analyze) and local_cli analyze --quick: the worker without stems, stored with profile quick,
replaced by a later full run and never replacing a stored full result."""
import json
import os
import subprocess
import unittest

from helpers import PY, REPO, RID64, Client, Env, Harness, server
from test_rhythm import MELODY, RHYTHM_OUT_V40, WAV, FakeWorker, RhythmBase
from test_separation import FULL

A = "/analyze?language=en&emotion_mode=none"
AQ = A + "&rhythm=quick"
AF = A + "&rhythm=1"
SKIPPED = dict(RHYTHM_OUT_V40, melody=dict(MELODY, separation={"status": "skipped", "reason": "canonical_16k"}))


class QuickBase(RhythmBase):
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
        self.fw.write(stdout=json.dumps(SKIPPED))

    def post(self, target, body=FULL):
        st, b = self.c.json("POST", target, body)
        self.assertEqual(st, 200, b)
        return b["rhythm"]

    def stored(self):
        with open(self.rhythm_file()) as f:
            return json.load(f)

    def argv(self):
        return self.fw.calls()[-1]["argv"]


class QuickServerTest(QuickBase):
    def test_quick_skips_stems_and_is_marked(self):
        r = self.post(AQ)
        self.assertEqual((r["status"], r["cache_hit"], r["stored"], r["profile"]), ("done", False, True, "quick"))
        self.assertIn("not measured", r["profile_note"])
        argv = self.argv()
        self.assertIn("--demucs", argv)
        self.assertNotIn("--melody-source", argv)  # the worker's no-stem path, as for a 16 kHz upload
        self.assertEqual(self.stored()["profile"], "quick")
        rep = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual(rep["profile"], "quick")
        sh = self.c.json("GET", "/sheet/%s?format=json" % RID64)[1]["sheet"]
        self.assertEqual(sh["profile"]["name"], "quick")
        self.assertIn("bars", sh["profile"]["not_measured"])
        text = self.c.json("GET", "/sheet/" + RID64)[1]["text"]
        self.assertIn("\nprofile quick: bass, other, drums, parts, bars", text)
        for line in text.splitlines():
            if line.startswith(("  parts", "  drums")):
                self.assertTrue(line.split()[1] in ("unavailable", "failed"), line)  # never "empty"
        z = self.c.json("GET", "/sheet/%s?format=json&start=0&end=6" % RID64)[1]["zoom"]
        self.assertEqual(z["profile"], sh["profile"])
        zt = self.c.json("GET", "/sheet/%s?start=0&end=6" % RID64)[1]["text"]
        self.assertIn("\nprofile quick: bass, other, drums, parts, bars", zt)

    def test_full_run_replaces_quick(self):
        self.post(AQ)
        self.fw.write(stdout=json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY, separation={"status": "done"}))))
        r = self.post(AF)
        self.assertEqual((r["cache_hit"], r.get("profile")), (False, None))
        self.assertIn("--melody-source", self.argv())
        self.assertNotIn("profile", self.stored())
        self.assertNotIn("profile", self.c.json("GET", "/report/" + RID64)[1]["rhythm"])
        self.assertNotIn("profile", self.c.json("GET", "/sheet/%s?format=json&start=0&end=6" % RID64)[1]["zoom"])
        self.assertNotIn("\nprofile ", self.c.json("GET", "/sheet/%s?start=0&end=6" % RID64)[1]["text"])

    def test_quick_reuses_quick_and_full(self):
        self.post(AQ)
        n = len(self.fw.calls())
        self.assertEqual((self.post(AQ)["cache_hit"], len(self.fw.calls())), (True, n))
        full = dict(RHYTHM_OUT_V40, melody=dict(MELODY, separation={"status": "done"}), **self.ids())
        with open(self.rhythm_file(), "w") as f:
            json.dump(full, f)
        r = self.post(AQ)
        self.assertEqual((r["cache_hit"], r.get("profile"), len(self.fw.calls())), (True, None, n))
        self.assertEqual(self.stored(), full)  # a quick request never replaces a stored result with a stem-made layer

    def test_quick_without_droppable_stems_is_not_marked(self):
        r = self.post(AQ, WAV)  # canonical 16 kHz: no stems either way
        self.assertNotIn("profile", r)
        self.assertNotIn("profile", self.stored())

    def test_model_beats_alone_protect_full_cache(self):
        full = dict(SKIPPED, all_in_one_beats=[0.2, 0.7, 1.2],
                    sections_model={"status": "skipped", "reason": "too_many_sections"},
                    **dict(self.ids(), worker_sha256="0" * 64))
        with open(self.rhythm_file(), "w") as f:
            json.dump(full, f)
        for target, body in ((AQ, FULL), (AF, WAV)):
            r = self.post(target, body)
            self.assertTrue(r["cache_hit"])
            self.assertEqual(r["all_in_one_beats"]["last_beat_s"], 1.2)
            self.assertEqual(self.stored(), full)
        self.assertEqual(self.fw.calls(), [])

    def test_default_unchanged_and_bad_values(self):
        r = self.post(AF)
        self.assertNotIn("profile", r)
        self.assertNotIn("profile_note", r)
        for bad in ("fast", "2", "QUICK", ""):
            st, b = self.c.json("POST", A + "&rhythm=" + bad, FULL)
            self.assertEqual((st, b["error"]["code"]), (400, "bad_request"), bad)

    def test_worker_cannot_set_profile(self):
        self.fw.write(stdout=json.dumps(dict(RHYTHM_OUT_V40, profile="quick")))
        self.assertNotIn("profile", self.post(AF))
        self.assertNotIn("profile", self.stored())

    def test_profile_validated(self):
        v = json.loads(json.dumps(RHYTHM_OUT_V40))
        self.assertIsNotNone(server.check_rhythm(dict(v, profile="quick")))
        for bad in ("full", None, 1, ""):
            self.assertIsNone(server.check_rhythm(dict(v, profile=bad)), bad)


class QuickNoSeparationTest(RhythmBase):
    def test_quick_without_separation_configured(self):
        st, b = self.c.json("POST", "/analyze?language=en&rhythm=quick", FULL)
        self.assertEqual((st, b["rhythm"]["status"]), (200, "done"))
        self.assertNotIn("profile", b["rhythm"])


class QuickCliTest(QuickBase):
    def cli(self, *args):
        p = subprocess.run([PY, "-I", os.path.join(REPO, "local_cli.py"), "--config", self.env.config_path] + list(args),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def test_cli_quick(self):
        path = os.path.join(self.env.dir, "song.wav")
        with open(path, "wb") as f:
            f.write(FULL)
        rc, out, err = self.cli("analyze", path, "--language", "en", "--quick")
        self.assertEqual(rc, 0, err)
        r = json.loads(out)["rhythm"]
        self.assertEqual(r["profile"], "quick")
        self.assertNotIn("--melody-source", self.argv())
        rc, out, err = self.cli("analyze", path, "--language", "en", "--quick", "--no-rhythm")
        self.assertEqual((rc, out), (2, b""))
        self.assertEqual(json.loads(err)["error"]["code"], "bad_request")


if __name__ == "__main__":
    unittest.main()
