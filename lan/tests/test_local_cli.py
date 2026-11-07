"""local_cli.py: the server's analyze + rhythm layer, report, timeline and sheet on this Mac without the network.
Runs the CLI as a subprocess against the same temp root, fake ml and fake rhythm worker as the server tests, and
checks its stdout against the served bodies byte for byte."""
import fcntl
import json
import os
import subprocess
import unittest

from helpers import ANALYZE_OUT, PY, REPO, RID64, silent_wav_bytes
from test_rhythm import AR, RHYTHM_OUT, RHYTHM_OUT_V40, WAV, RhythmBase

CLI = os.path.join(REPO, "local_cli.py")


class CliBase(RhythmBase):
    def setUp(self):
        super().setUp()
        self.wav = os.path.join(self.env.dir, "song.wav")
        with open(self.wav, "wb") as f:
            f.write(WAV)

    def cli(self, *args, config=None):
        p = subprocess.run([PY, "-I", CLI, "--config", config or self.env.config_path] + list(args),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def ok(self, *args):
        rc, out, err = self.cli(*args)
        self.assertEqual((rc, err), (0, b""), err)
        self.assertTrue(out.endswith(b"\n"))
        return out[:-1]

    def err(self, rc_want, code, *args, **kw):
        rc, out, err = self.cli(*args, **kw)
        self.assertEqual((rc, out), (rc_want, b""), err)
        b = json.loads(err.decode("utf-8"))
        self.assertEqual(b["error"]["code"], code, b)
        return b

    def served(self, target):
        st, _h, body = self.c.send("GET", target)
        self.assertEqual(st, 200, body)
        return body

    def store(self):
        os.makedirs(os.path.dirname(self.rhythm_file()), 0o700, exist_ok=True)
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(dict(RHYTHM_OUT_V40, **self.ids())))


class CliAnalyzeTest(CliBase):
    def test_analyze_runs_ml_then_rhythm(self):
        b = json.loads(self.ok("analyze", self.wav, "--language", "en", "--emotion-mode", "voice"))
        for k, v in ANALYZE_OUT.items():
            self.assertEqual(b[k], v)
        self.assertRegex(b["request_id"], r"\A[0-9a-f]{32}\Z")
        self.assertEqual(b["name"], None)
        self.assertEqual((b["rhythm"]["status"], b["rhythm"]["cache_hit"], b["rhythm"]["stored"]), ("done", False, True))
        self.assertIn("read_next", b["rhythm"])
        argv = self.env.argv_log()
        self.assertEqual(argv, [["analyze", "api-%s.wav" % b["request_id"], "--language", "en", "--emotion-mode", "voice"]])
        calls = self.fw.calls()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["lock_held"], "team flock held while the worker runs")
        with open(self.rhythm_file()) as f:
            stored = json.load(f)
        self.assertEqual({k: stored[k] for k in ("worker_sha256", "words_sha256")}, self.ids())
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])
        # the server now reuses the file the CLI stored, and both summaries agree
        st, sb = self.c.json("POST", AR, WAV)
        self.assertEqual(st, 200, sb)
        self.assertEqual(sb["rhythm"], dict(b["rhythm"], cache_hit=True))
        self.assertEqual(len(self.fw.calls()), 1)

    def test_cache_hit_and_name(self):
        self.ok("analyze", self.wav, "--language", "ko")
        b = json.loads(self.ok("analyze", self.wav, "--language", "ko", "--name", "take 2"))
        self.assertEqual((b["name"], b["rhythm"]["cache_hit"]), ("take 2", True))
        self.assertEqual(len(self.fw.calls()), 1)

    def test_no_rhythm(self):
        b = json.loads(self.ok("analyze", self.wav, "--language", "ja", "--no-rhythm"))
        self.assertNotIn("rhythm", b)
        self.assertEqual(self.fw.calls(), [])

    def test_team_lock_busy(self):
        fd = os.open(os.path.join(self.env.root, ".analyze.lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            b = json.loads(self.ok("analyze", self.wav, "--language", "en"))
        finally:
            os.close(fd)
        self.assertEqual(b["rhythm"], {"status": "busy", "code": "busy"})
        self.assertEqual(self.fw.calls(), [])

    def test_rhythm_failure_keeps_analyze(self):
        self.fw.write(exit=1, stderr_lines=['{"error":{"code":"decode_failed"}}'])
        b = json.loads(self.ok("analyze", self.wav, "--language", "en"))
        self.assertEqual(b["rhythm"], {"status": "failed", "code": "decode_failed"})
        self.assertFalse(os.path.exists(self.rhythm_file()))

    def test_errors(self):
        self.err(1, "invalid_path", "analyze", os.path.join(self.env.dir, "missing.wav"), "--language", "en")
        self.err(1, "invalid_path", "analyze", self.env.dir, "--language", "en")
        junk = os.path.join(self.env.dir, "notes.txt")
        with open(junk, "wb") as f:
            f.write(b"not audio at all")
        self.err(1, "invalid_audio", "analyze", junk, "--language", "en")
        self.err(1, "invalid_argument", "analyze", self.wav, "--language", "en", "--name", "a\x01b")
        self.env.write_control(analyze={"exit": 1, "stderr_lines": ['{"error":{"code":"busy"}}']})
        b = self.err(1, "busy", "analyze", self.wav, "--language", "en")
        self.assertRegex(b["request_id"], r"\A[0-9a-f]{32}\Z")
        self.err(2, "bad_request", "analyze", self.wav, "--language", "de")
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])
        self.assertEqual(self.fw.calls(), [])

    def test_mp3_converts_like_the_server(self):
        # a non-canonical WAV (44.1 kHz) goes through limit_exec.py/afconvert exactly as an upload does
        src = os.path.join(self.env.dir, "hi.wav")
        with open(src, "wb") as f:
            f.write(silent_wav_bytes(0.5, rate=44100))
        b = json.loads(self.ok("analyze", src, "--language", "en", "--no-rhythm"))
        seen = self.env.seen_log()[-1]
        self.assertEqual((seen["name"], seen["exists"], seen["head"]), ("api-%s.wav" % b["request_id"], True, "RIFF"))
        self.assertEqual(self.env.uploads_entries(), [])

    def test_rhythm_unconfigured(self):
        cfg = self.env.config()
        p = os.path.join(self.env.dir, "norhythm.json")
        with open(p, "w") as f:
            json.dump(cfg, f)
        os.chmod(p, 0o600)
        self.err(1, "rhythm_unavailable", "analyze", self.wav, "--language", "en", config=p)
        self.assertEqual(self.env.argv_log(), [])
        b = json.loads(self.cli("analyze", self.wav, "--language", "en", "--no-rhythm", config=p)[1])
        self.assertNotIn("rhythm", b)

    def test_config_errors(self):
        os.chmod(self.env.config_path, 0o644)
        b = self.err(2, "server_misconfigured", "report", RID64)
        self.assertIn("0600", b["error"]["detail"])
        os.chmod(self.env.config_path, 0o600)
        self.err(1, "invalid_id", "report", "abc")


class CliReadTest(CliBase):
    def test_report_matches_server(self):
        self.assertEqual(self.ok("report", RID64), self.served("/report/" + RID64))  # ml bytes unchanged
        self.store()
        out = self.ok("report", RID64)
        self.assertEqual(out, self.served("/report/" + RID64))
        self.assertEqual(json.loads(out)["rhythm"]["bpm"], RHYTHM_OUT["bpm"])
        with open(self.rhythm_file(), "w") as f:
            f.write("{")
        self.assertEqual(json.loads(self.ok("report", RID64))["rhythm"], {"status": "failed", "code": "invalid_result"})

    def test_sheet_matches_server(self):
        self.err(1, "rhythm_not_found", "sheet", RID64)
        self.store()
        for args, q in (((), ""), (("--format", "json"), "?format=json"), (("--start", "0", "--end", "3.5"), "?start=0&end=3.5"),
                        (("--start", "1", "--end", "2", "--format", "json"), "?start=1&end=2&format=json")):
            self.assertEqual(self.ok("sheet", RID64, *args), self.served("/sheet/" + RID64 + q), args)
        out = json.loads(self.ok("sheet", RID64))
        self.assertTrue(out["text"].startswith("MUSIC LENS SHEET"))
        b = self.err(1, "bad_request", "sheet", RID64, "--start", "0", "--end", "30.5")
        self.assertIn("at most 30 s", b["error"]["detail"])
        self.err(1, "bad_request", "sheet", RID64, "--start", "0")
        self.err(2, "bad_request", "sheet", RID64, "--format", "xml")

    def test_timeline(self):
        self.store()
        for layer in ("beats", "chords", "melody"):
            self.assertEqual(self.ok("timeline", RID64, "--layer", layer, "--limit", "3"),
                             self.served("/timeline/%s?layer=%s&limit=3" % (RID64, layer)))
        self.assertEqual(self.ok("timeline", RID64, "--layer", "beats", "--start", "1", "--end", "2.5", "--cursor", "1"),
                         self.served("/timeline/%s?layer=beats&start=1&end=2.5&cursor=1" % RID64))
        self.err(1, "rhythm_not_found", "timeline", RID64, "--layer", "drums")
        self.err(1, "invalid_argument", "timeline", RID64, "--layer", "beats", "--start", "700")
        self.err(1, "bad_request", "timeline", RID64, "--layer", "beats", "--limit", "0")
        n = len(self.env.argv_log())
        self.assertEqual(self.ok("timeline", RID64, "--layer", "words", "--start", "1.5", "--limit", "5"),
                         self.served("/timeline/%s?layer=words&start=1.5&limit=5" % RID64))
        self.assertEqual(self.env.argv_log()[n], ["timeline", RID64, "--layer=words", "--start=1.5", "--limit=5"])
        self.assertEqual(self.ok("timeline", RID64), self.served("/timeline/" + RID64))


if __name__ == "__main__":
    unittest.main()
