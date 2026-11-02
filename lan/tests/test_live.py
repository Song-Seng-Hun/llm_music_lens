"""One live end-to-end run against the REAL wrapper. Opt-in: MUSIC_LENS_LIVE=1.

Server state (api/, uploads/, logs/) lives in a temp root; ml itself always uses ~/AI-Listen/inbox and results.
Only the request-owned inbox files (api-<op>.wav) are created there, and the server deletes them after each job.
"""
import json
import os
import subprocess
import time
import unittest

from helpers import Client, Env, SubprocessServer

LIVE = os.environ.get("MUSIC_LENS_LIVE") == "1"
ML = os.path.expanduser("~/tools/music-lens/ml")
INBOX = os.path.expanduser("~/AI-Listen/inbox")
RESULTS = os.path.expanduser("~/AI-Listen/results")
FIXTURE = os.environ.get("MUSIC_LENS_FIXTURE_WAV", "")
A = "/analyze?language=en&emotion_mode=voice&name=fixture-en"


@unittest.skipUnless(LIVE and FIXTURE, "set MUSIC_LENS_LIVE=1 and MUSIC_LENS_FIXTURE_WAV for the single live run")
class LiveTest(unittest.TestCase):
    def test_live_end_to_end(self):
        env = Env()
        evidence = {}
        before_inbox = sorted(os.listdir(INBOX))
        before_results = sorted(os.listdir(RESULTS))
        srv = None
        try:
            srv = SubprocessServer(env, ml=ML, inbox=INBOX)
            c = Client(env, srv.port)
            if os.environ.get("MUSIC_LENS_LIVE_ONLY") == "m4a":
                return self._m4a(env, c, evidence)
            st, status = c.json("GET", "/status")
            self.assertEqual(st, 200)
            evidence["status"] = {k: status.get(k) for k in ("tool_version", "engine", "emotion_configured")}
            with open(FIXTURE, "rb") as f:
                wav = f.read()
            h = c.signed("POST", A, wav)
            t0 = time.time()
            st, b = c.json("POST", A, wav, headers=h, timeout=700)
            self.assertEqual(st, 200, b)
            rid = b["result_id"]
            evidence["wav"] = {"http": st, "state": b["state"], "word_count": b["word_count"],
                               "windows": b["emotion"]["window_count"], "emotion_status": b["emotion"]["status"],
                               "cache_hit": b.get("cache_hit"), "result_id": rid, "secs": round(time.time() - t0, 1),
                               "request_id": b["request_id"], "upload_sha256": b["upload_sha256"]}
            self.assertEqual((b["state"], b["word_count"], b["emotion"]["window_count"]), ("complete", 11, 2))
            st, rep = c.json("GET", "/report/" + rid)
            self.assertEqual((st, rep["word_count"]), (200, 11))
            st, tw = c.json("GET", "/timeline/" + rid + "?layer=words&limit=200")
            self.assertEqual((st, len(tw["words"])), (200, 11))
            st, te = c.json("GET", "/timeline/" + rid + "?layer=emotion")
            self.assertEqual((st, len(te["windows"])), (200, 2))
            st, r = c.json("GET", "/receipt/" + h["X-ML-Nonce"])
            self.assertEqual((st, r["state"], r["result_id"]), (200, "done", rid))
            evidence["report_words"], evidence["timeline_words"] = rep["word_count"], len(tw["words"])
            evidence["timeline_windows"] = len(te["windows"])
            evidence["receipt"] = r
            self.assertEqual(c.json("POST", A, wav, headers=h)[0], 401)  # replay
            evidence["replay_status"] = 401
            self._m4a(env, c, evidence)
        finally:
            if srv:
                srv.terminate()
            after_inbox = sorted(os.listdir(INBOX))
            evidence["inbox_unchanged"] = after_inbox == before_inbox
            evidence["inbox_leftover_api"] = [n for n in after_inbox if n.startswith("api-")]
            evidence["new_results"] = sorted(set(os.listdir(RESULTS)) - set(before_results))
            evidence["temp_uploads_left"] = env.uploads_entries()
            print("\nLIVE-EVIDENCE " + json.dumps(evidence, ensure_ascii=False))
            env.cleanup()
        self.assertTrue(evidence["inbox_unchanged"])

    def _m4a(self, env, c, evidence):
        # conversion path: M4A made from the fixture
        A = "/analyze?language=en&emotion_mode=voice&name=fixture-en-m4a"
        m4a = os.path.join(env.dir, "fixture-en.m4a")
        subprocess.run(["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", FIXTURE, m4a], check=True)
        with open(m4a, "rb") as f:
            body = f.read()
        h2 = c.signed("POST", A, body)
        st, b2 = c.json("POST", A, body, headers=h2, timeout=700)
        self.assertEqual(st, 200, b2)
        conv = [e for e in env.log_events() if e.get("event") == "convert_exit"]
        evidence["m4a"] = {"http": st, "state": b2["state"], "word_count": b2["word_count"],
                           "windows": b2["emotion"]["window_count"], "cache_hit": b2.get("cache_hit"),
                           "convert_rc": conv[-1]["rc"], "bytes": len(body)}
        r2 = c.json("GET", "/receipt/" + h2["X-ML-Nonce"])[1]
        evidence["m4a"]["receipt_state"] = r2["state"]
        self.assertEqual(conv[-1]["rc"], 0)
        self.assertEqual(r2["state"], "done")


if __name__ == "__main__":
    unittest.main()
