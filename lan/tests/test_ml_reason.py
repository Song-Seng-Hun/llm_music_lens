"""ml's reason text in error bodies: only ml 0.2.6's fixed Failure messages (server.ML_REASONS) reach the client, as
error.detail; an analyze that ml fails as empty_output (no words, no emotion layer) also gets error.hint."""
import json
import os
import subprocess
import unittest

from helpers import PY, REPO, RID64, Base, server, silent_wav_bytes

REPORT = "/report/" + RID64
NO_LAYER = "No requested analysis layer succeeded; no result published"
NO_WORDS = "No timed words in structurally valid output; silence or unrecognized audio is not a successful transcript"


def line(code, message):
    return json.dumps({"error": {"code": code, "message": message}})


class ReasonTest(Base):
    def fail_with(self, cmd, code, message):
        self.env.write_control(**{cmd: {"exit": 1, "stderr_lines": ["diag /Users/x/secret", line(code, message)]}})

    def analyze(self, mode):
        a = "/analyze?language=en&emotion_mode=" + mode
        body = silent_wav_bytes(0.5)
        h = self.c.signed("POST", a, body)
        st, b = self.c.json("POST", a, body, headers=h)
        return st, b, h["X-ML-Nonce"]

    def test_empty_output_detail_and_hint(self):
        for mode in ("none", "voice"):  # ml analyze emits NO_LAYER at top level (NO_WORDS is caught inside)
            self.fail_with("analyze", "empty_output", NO_LAYER)
            st, b, nonce = self.analyze(mode)
            self.assertEqual(st, 500)
            self.assertEqual(b, {"error": {"code": "empty_output", "message": "Analyzer returned no output",
                                           "detail": NO_LAYER, "hint": server.NO_WORDS_HINT}, "request_id": nonce})
            r = self.c.json("GET", "/receipt/" + nonce)[1]
            self.assertEqual((r["state"], r["error_code"]), ("failed", "empty_output"))
        self.assertIn("emotion_mode=music", server.NO_WORDS_HINT)

    def test_music_mode_gets_no_hint(self):
        self.fail_with("analyze", "empty_output", NO_LAYER)
        b = self.analyze("music")[1]
        self.assertEqual(b["error"], {"code": "empty_output", "message": "Analyzer returned no output", "detail": NO_LAYER})

    def test_known_reason_on_reads(self):
        self.fail_with("report", "not_found", "Unknown result ID")
        st, b = self.c.json("GET", REPORT)
        self.assertEqual((st, b), (404, {"error": {"code": "not_found", "message": "Not found", "detail": "Unknown result ID"}}))

    def test_other_text_dropped(self):
        cases = [("not_found", "Unknown result ID /Users/x"),  # not an exact ml text
                 ("invalid_id", "Unknown result ID"),  # an ml text, but not this code's
                 ("configuration", "Configure both emotion Python and asset directory"),  # remapped: server_misconfigured
                 ("inference_failed", "Inference child exited 1; no result published"),
                 ("busy", None), ("busy", 7)]
        for code, message in cases:
            self.fail_with("report", code, message)
            st, raw_h, raw = self.c.send("GET", REPORT)
            self.assertEqual(set(json.loads(raw)["error"]), {"code", "message"}, (code, message))
            self.assertNotIn("/Users", raw.decode())
        self.env.write_control(report={"stdout": ""})  # the server's own empty_output: no detail
        self.assertEqual(set(self.c.json("GET", REPORT)[1]["error"]), {"code", "message"})

    def test_analyze_without_ml_reason_unchanged(self):
        self.fail_with("analyze", "empty_output", "something else")
        b = self.analyze("none")[1]
        self.assertEqual(set(b["error"]), {"code", "message"})


class CliReasonTest(Base):
    def test_cli_analyze_empty_output(self):
        wav = os.path.join(self.env.dir, "song.wav")
        with open(wav, "wb") as f:
            f.write(silent_wav_bytes(0.5))
        self.env.write_control(analyze={"exit": 1, "stderr_lines": [line("empty_output", NO_LAYER)]})
        p = subprocess.run([PY, "-I", os.path.join(REPO, "local_cli.py"), "--config", self.env.config_path, "analyze", wav,
                            "--language", "en", "--no-rhythm"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual((p.returncode, p.stdout), (1, b""), p.stderr)
        b = json.loads(p.stderr)
        self.assertEqual(b["error"], {"code": "empty_output", "message": "Analyzer returned no output", "detail": NO_LAYER,
                                      "hint": server.NO_WORDS_HINT})
        self.assertRegex(b["request_id"], r"\A[0-9a-f]{32}\Z")


if __name__ == "__main__":
    unittest.main()
