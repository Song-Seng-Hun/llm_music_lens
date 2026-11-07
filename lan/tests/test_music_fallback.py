"""Music fallback: when ml fails empty_output (its own reason: no words, no emotion layer) on an analyze with the
rhythm layer requested and emotion_mode none/voice, ml runs once more with emotion_mode music; the summary says
emotion_fallback "music". No loop: a second failure is the error body."""
import json
import os
import subprocess
import unittest

from helpers import ANALYZE_OUT, PY, REPO, server
from test_ml_reason import NO_LAYER, line
from test_rhythm import WAV, RhythmBase

A = "/analyze?language=en&emotion_mode="
FAIL = {"exit": 1, "stderr_lines": [line("empty_output", NO_LAYER)]}
OK = {"stdout": json.dumps(ANALYZE_OUT) + "\n"}


class FallbackTest(RhythmBase):
    def ml(self, **by_mode):
        self.env.write_control(analyze=dict(OK, by_mode=by_mode))

    def modes(self):
        return [a[a.index("--emotion-mode") + 1] for a in self.env.argv_log() if a[0] == "analyze"]

    def test_fallback_runs_rhythm(self):
        for mode in ("none", "voice"):
            self.ml(**{mode: FAIL})
            st, b = self.c.json("POST", A + mode + "&rhythm=1", WAV)
            self.assertEqual(st, 200, b)
            self.assertEqual(b["emotion_fallback"], "music")
            self.assertEqual(b["result_id"], ANALYZE_OUT["result_id"])
            self.assertEqual(b["rhythm"]["status"], "done")
            self.assertEqual(self.modes()[-2:], [mode, "music"])
        self.assertEqual(len(self.fw.calls()), 1)  # the second request reuses the stored rhythm file
        self.assertEqual(b["rhythm"]["cache_hit"], True)

    def test_quick_falls_back_too(self):
        self.ml(none=FAIL)
        st, b = self.c.json("POST", A + "none&rhythm=quick", WAV)
        self.assertEqual((st, b["emotion_fallback"], self.modes()), (200, "music", ["none", "music"]))

    def test_second_failure_is_the_error_body(self):
        self.ml(none=FAIL, music=FAIL)
        st, b = self.c.json("POST", A + "none&rhythm=1", WAV)
        self.assertEqual(st, 500)
        self.assertEqual(b["error"], {"code": "empty_output", "message": "Analyzer returned no output", "detail": NO_LAYER})
        self.assertEqual(self.modes(), ["none", "music"])  # once, no loop
        self.assertEqual(self.fw.calls(), [])

    def test_no_fallback_cases(self):
        cases = [(A + "none", {"none": FAIL}, "hint"),  # rhythm not requested: the Fix-1 body with the hint
                 (A + "music&rhythm=1", {"music": FAIL}, None),  # already music
                 (A + "none&rhythm=1", {"none": dict(FAIL, stderr_lines=[line("empty_output", "other")])}, None),
                 (A + "none&rhythm=1", {"none": {"exit": 1, "stderr_lines": [line("invalid_audio", "Malformed PCM WAV")]}}, None)]
        for target, by_mode, hint in cases:
            n = len(self.modes())
            self.ml(**by_mode)
            st, b = self.c.json("POST", target, WAV)
            self.assertEqual(len(self.modes()), n + 1, target)
            self.assertNotEqual(st, 200, target)
            self.assertEqual("hint" in b["error"], hint is not None, target)
            self.assertNotIn("emotion_fallback", b)
        self.assertEqual(b["error"]["code"], "invalid_audio")

    def test_words_audio_unchanged(self):
        st, b = self.c.json("POST", A + "none&rhythm=1", WAV)
        self.assertEqual(st, 200, b)
        self.assertNotIn("emotion_fallback", b)
        self.assertEqual(self.modes(), ["none"])

    def test_cli_fallback(self):
        wav = os.path.join(self.env.dir, "song.wav")
        with open(wav, "wb") as f:
            f.write(WAV)
        self.ml(none=FAIL)
        p = subprocess.run([PY, "-I", os.path.join(REPO, "local_cli.py"), "--config", self.env.config_path, "analyze", wav,
                            "--language", "en"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        b = json.loads(p.stdout)
        self.assertEqual((b["emotion_fallback"], b["rhythm"]["status"]), ("music", "done"))
        self.assertEqual(self.modes(), ["none", "music"])
        self.assertIn("emotion_mode=music with rhythm=1", server.NO_WORDS_HINT)
        self.assertIn("without --no-rhythm", server.NO_WORDS_HINT)


if __name__ == "__main__":
    unittest.main()
