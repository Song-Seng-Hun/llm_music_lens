"""Optional rhythm layer: config, POST /analyze?rhythm=1, stored file, /report merge, /timeline beats|chords,
/status, plus the real Essentia worker on synthetic audio (skipped when the Essentia python is missing)."""
import fcntl
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

from helpers import ANALYZE_OUT, PY, REPO, RID64, Base, Client, Env, Harness, server, sha, silent_wav_bytes

import rhythm_worker as rw  # noqa: E402  (stdlib-only at import time; numpy/essentia load inside analyze)

A = "/analyze?language=en&emotion_mode=voice"
AR = A + "&rhythm=1"
WAV = silent_wav_bytes(0.5)
ESSENTIA_PY = ("/Users/songseunghun/Documents/Codex/2026-09-27-window-pc-pc/outputs/"
               "music-lens-emotion-pilot-20261025/runtime/bin/python")
WORKER = os.path.join(REPO, "rhythm_worker.py")
SYNTH = os.path.join(REPO, "tests", "synth_audio.py")

FAKE_WORKER = r'''import fcntl, json, os, sys, time
HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "control.json")) as f:
    ctl = json.load(f)
fd = os.open(ctl["lock"], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    held = False
except BlockingIOError:
    held = True
os.close(fd)
wav = sys.argv[1]
with open(os.path.join(HERE, "calls.log"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "exists": os.path.isfile(wav), "lock_held": held,
                        "path_env": os.environ.get("PATH"), "env_keys": sorted(os.environ),
                        "pid": os.getpid()}) + "\n")
time.sleep(ctl.get("sleep", 0))
for line in ctl.get("stderr_lines", []):
    sys.stderr.write(line + "\n")
if "stdout" in ctl:
    sys.stdout.write(ctl["stdout"])
sys.stdout.flush()
sys.exit(ctl.get("exit", 0))
'''

RHYTHM_OUT = {
    "bpm": 120.0, "bpm_confidence": 3.9, "bpm_confidence_scale": "native 0..~5.32",
    "bpm_estimates": [{"bpm": 120.2, "share": 0.9}],
    "beats": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    "downbeats": {"status": "not_provided"},
    "key": {"tonic": "C", "scale": "major", "strength": 0.9, "profile": "bgate"},
    "chords": [{"start": 0.0, "end": 2.0, "label": "C", "strength": 0.9},
               {"start": 2.0, "end": 4.0, "label": "Am", "strength": 0.8},
               {"start": 4.0, "end": 6.0, "label": "F", "strength": 0.7}],
    "chords_reliability": "low_reference", "sample_rate": 44100, "duration": 6.0,
    "tool": {"name": "fake", "version": "0", "essentia_version": "x"}, "warnings": [],
}


V2_FIELDS = {
    "bpm_alternatives": [60.0, 240.0], "bpm_alternatives_note": "metrical level is ambiguous",
    "key_sections": [{"start": 0.0, "end": 4.0, "tonic": "C", "scale": "major", "strength": 0.9},
                     {"start": 4.0, "end": 6.0, "tonic": "D", "scale": "major", "strength": None}],
    "key_change_count": 1, "key_section_window": 20.0, "chords_grid": "beat", "chord_frame_segment_count": 9,
}
RHYTHM_OUT_V2 = dict(RHYTHM_OUT, warnings=["key_changes: 2 sections on 20 s windows (DSP estimate)"], **V2_FIELDS)
V2_SUMMARY_KEYS = ("bpm_alternatives", "bpm_alternatives_note", "key_sections", "key_change_count", "chords_grid")


def bad_v2():
    """Worker-0.2.0 outputs that must be refused (invalid_output from the worker, invalid_result when stored)."""
    ks = V2_FIELDS["key_sections"]
    out = []
    for over in ({"bpm_alternatives": [float("nan")]}, {"bpm_alternatives": [60, 1e9]}, {"bpm_alternatives": 60},
                 {"bpm_alternatives": [0]}, {"bpm_alternatives": [60] * 5}, {"bpm_alternatives_note": "x" * 201},
                 {"bpm_alternatives_note": None}, {"key_sections": [dict(ks[0], start=5.0, end=4.0)]},
                 {"key_sections": [dict(ks[0], end=60.0)], "key_change_count": 0},
                 {"key_sections": [dict(ks[0], strength=float("inf"))]},
                 {"key_sections": [dict(ks[0], strength="x")]}, {"key_sections": [dict(ks[0], tonic="")]},
                 {"key_sections": [dict(ks[0], tonic="C\n")]}, {"key_sections": [ks[1], ks[0]]},
                 {"key_sections": [ks[0]] * 65, "key_change_count": 0}, {"key_sections": {"x": 1}},
                 {"key_change_count": 0}, {"key_change_count": True}, {"key_change_count": -1},
                 {"key_change_count": 1.0}, {"chords_grid": "bar"}, {"chords_grid": None},
                 {"chord_frame_segment_count": -1}, {"key_section_window": 0}):
        out.append(json.dumps(dict(RHYTHM_OUT_V2, **over)))
    return out


DTE = {"method": "spectral_flux_l1_halfrect_hann1024_hop512_maxnorm", "window_ms": 30.0, "beat_onset_median": 0.82,
       "mid_onset_median": 0.83, "mid_to_beat_ratio": 1.011, "mid_strong_fraction": 1.0, "mid_strong_threshold": 0.5,
       "pairs": 48}
DTE_NULL = dict(DTE, beat_onset_median=None, mid_onset_median=None, mid_to_beat_ratio=None, mid_strong_fraction=None,
                pairs=2)
DTE_WARN = "double_tempo_evidence_unavailable: fewer than 7 usable beat pairs"
RHYTHM_OUT_V21 = dict(RHYTHM_OUT_V2, double_tempo_evidence=DTE, double_tempo_evidence_note="diagnostic only")
V21_SUMMARY_KEYS = ("double_tempo_evidence", "double_tempo_evidence_note")


def bad_v21():
    """Worker-0.2.1 outputs that must be refused."""
    out = []
    for over in ({"mid_to_beat_ratio": float("nan")}, {"mid_to_beat_ratio": 1001}, {"mid_to_beat_ratio": -0.1},
                 {"mid_to_beat_ratio": "1"}, {"mid_strong_fraction": 1.5}, {"beat_onset_median": 2},
                 {"mid_onset_median": float("inf")}, {"pairs": -1}, {"pairs": 1.0}, {"pairs": True},
                 {"pairs": 10 ** 6}, {"window_ms": 0}, {"method": ""}, {"method": "x" * 65}, {"method": "a\nb"},
                 {"mid_strong_threshold": 2}, {"extra": 1}, {"mid_strong_fraction": None},
                 {"mid_to_beat_ratio": None, "mid_strong_fraction": None}, {"beat_onset_median": None}):
        out.append(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=dict(DTE, **over))))
    d = dict(DTE)
    del d["pairs"]
    out.append(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=d)))
    for over in ({"double_tempo_evidence": [1]}, {"double_tempo_evidence": None},
                 {"double_tempo_evidence_note": ""}, {"double_tempo_evidence_note": "x" * 201},
                 {"double_tempo_evidence": DTE_NULL}):  # null ratio without the warning
        out.append(json.dumps(dict(RHYTHM_OUT_V21, **over)))
    return out


class FakeWorker:
    def __init__(self, env):
        self.dir = os.path.join(env.dir, "fakerhythm")
        os.mkdir(self.dir)
        self.path = os.path.join(self.dir, "worker.py")
        with open(self.path, "w") as f:
            f.write(FAKE_WORKER)
        os.chmod(self.path, 0o600)
        self.control = {"lock": os.path.join(env.root, ".analyze.lock"), "stdout": json.dumps(RHYTHM_OUT) + "\n"}
        self.write()

    def write(self, **over):
        self.control.update(over)
        with open(os.path.join(self.dir, "control.json"), "w") as f:
            json.dump(self.control, f)

    def calls(self):
        p = os.path.join(self.dir, "calls.log")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(x) for x in f if x.strip()]


class RhythmBase(Base):
    limits = None

    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        self.h = Harness(self.env, limits=self.limits, rhythm={"python": PY, "worker": self.fw.path})
        self.c = Client(self.env, self.h.port)

    def rhythm_file(self, rid=RID64):
        return os.path.join(self.env.root, "results-rhythm", rid + ".json")

    def assert_rhythm_failed(self, status, code):
        h = self.c.signed("POST", AR, WAV)
        st, b = self.c.json("POST", AR, WAV, headers=h)
        self.assertEqual(st, 200, b)
        for k, v in ANALYZE_OUT.items():
            self.assertEqual(b[k], v)
        self.assertEqual(b["rhythm"], {"status": status, "code": code})
        self.assertFalse(os.path.exists(self.rhythm_file()))
        rec = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]
        self.assertEqual((rec["state"], rec["http_status"]), ("done", 200))
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])
        return b


class RhythmAnalyzeTest(RhythmBase):
    def test_flag_off_unchanged(self):
        for target in (A, A + "&rhythm=0"):
            h = self.c.signed("POST", target, WAV)
            st, b = self.c.json("POST", target, WAV, headers=h)
            self.assertEqual(st, 200)
            expect = dict(ANALYZE_OUT, request_id=h["X-ML-Nonce"], upload_sha256=sha(WAV), name=None)
            self.assertEqual(b, expect)
        self.assertEqual(self.fw.calls(), [])
        self.assertFalse(os.path.exists(self.rhythm_file()))

    def test_success_stores_and_cleans_up(self):
        os.environ["ML_LEAK_PROBE"] = "1"
        try:
            h = self.c.signed("POST", AR, WAV)
            st, b = self.c.json("POST", AR, WAV, headers=h)
        finally:
            del os.environ["ML_LEAK_PROBE"]
        self.assertEqual(st, 200, b)
        for k, v in ANALYZE_OUT.items():
            self.assertEqual(b[k], v)  # ASR result untouched
        r = b["rhythm"]
        self.assertEqual((r["status"], r["cache_hit"], r["stored"]), ("done", False, True))
        self.assertEqual((r["bpm"], r["key"]["tonic"], r["beat_count"], r["chord_segment_count"]), (120.0, "C", 7, 3))
        self.assertEqual((r["bpm_confidence_band"], r["bpm_estimates"]), ("excellent", RHYTHM_OUT["bpm_estimates"]))
        self.assertNotIn("beats", r)  # rows only via /timeline
        self.assertNotIn("chords", r)
        for k in V2_SUMMARY_KEYS + V21_SUMMARY_KEYS:  # the default fake output has the worker-0.1.0 shape
            self.assertIsNone(r[k], k)
        calls = self.fw.calls()
        self.assertEqual(len(calls), 1)
        inbox_name = self.env.argv_log()[-1][1]
        self.assertEqual(calls[0]["argv"], [os.path.join(self.env.inbox, inbox_name)])  # same promoted WAV, argv
        self.assertTrue(calls[0]["exists"])
        self.assertTrue(calls[0]["lock_held"], "team flock held while the worker runs")
        self.assertEqual(calls[0]["path_env"], server.CHILD_ENV_PATH)
        # child env is built from scratch (the /usr/bin/python3 shim adds its own developer-tool variables)
        self.assertTrue({"HOME", "LANG", "PATH", "TMPDIR"} <= set(calls[0]["env_keys"]))
        self.assertNotIn("ML_LEAK_PROBE", calls[0]["env_keys"])
        p = self.rhythm_file()
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode), 0o700)
        with open(p) as f:
            self.assertEqual(json.load(f), RHYTHM_OUT)
        self.assertEqual(os.listdir(os.path.dirname(p)), [RID64 + ".json"])  # no tmp left
        rec = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]
        self.assertEqual((rec["state"], rec["result_id"], rec["error_code"]), ("done", RID64, None))
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])
        # lock released afterwards
        fd = os.open(os.path.join(self.env.root, ".analyze.lock"), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_cache_hit_reuses_stored(self):
        self.assertEqual(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"], False)
        self.env.write_control(analyze={"stdout": json.dumps(dict(ANALYZE_OUT, cache_hit=True))})
        st, b = self.c.json("POST", AR, WAV)
        self.assertEqual(st, 200)
        self.assertEqual((b["cache_hit"], b["rhythm"]["status"], b["rhythm"]["cache_hit"]), (True, "done", True))
        self.assertEqual(b["rhythm"]["bpm"], 120.0)
        self.assertNotIn("beats", b["rhythm"])
        self.assertEqual(len(self.fw.calls()), 1)  # not recomputed

    def test_worker_failure_keeps_200(self):
        self.fw.write(exit=1, stdout="", stderr_lines=['{"error":{"code":"analysis_failed","message":"x"}}'])
        self.assert_rhythm_failed("failed", "analysis_failed")
        self.fw.write(exit=1, stdout="", stderr_lines=["Traceback: boom"])
        self.assert_rhythm_failed("failed", "internal_error")

    def test_worker_invalid_output(self):
        for out in ("not json", json.dumps(dict(RHYTHM_OUT, beats=[3.0, 1.0])), json.dumps([1]),
                    json.dumps(dict(RHYTHM_OUT, bpm=float("nan"))),
                    json.dumps(dict(RHYTHM_OUT, bpm_estimates=[{"bpm": float("nan"), "share": 1}])),
                    json.dumps(dict(RHYTHM_OUT, bpm_estimates=[{"bpm": 120, "share": float("inf")}])),
                    json.dumps(dict(RHYTHM_OUT, bpm_estimates=[5])),
                    json.dumps(dict(RHYTHM_OUT, tool={"name": {"x": float("nan")}})),
                    json.dumps(dict(RHYTHM_OUT, extra=[float("-inf")])),
                    json.dumps(dict(RHYTHM_OUT, extra=1e400)),
                    json.dumps(RHYTHM_OUT).replace('"duration": 6.0', '"duration": 1' + "0" * 400),
                    json.dumps(RHYTHM_OUT).replace('"sample_rate": 44100', '"sample_rate": 1' + "0" * 400)):
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v2_fields_in_summary(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V2))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual(r["status"], "done", r)
        for k in V2_SUMMARY_KEYS:
            self.assertEqual(r[k], RHYTHM_OUT_V2[k], k)
        self.assertNotIn("chords", r)
        self.assertNotIn("chord_frame_segment_count", r)  # stored file only
        with open(self.rhythm_file()) as f:
            self.assertEqual(json.load(f), RHYTHM_OUT_V2)
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual((r["key_change_count"], r["key_sections"][1]["strength"]), (1, None))

    def test_v2_invalid_output(self):
        for out in bad_v2():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v21_fields_in_summary(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V21))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual(r["status"], "done", r)
        for k in V2_SUMMARY_KEYS + V21_SUMMARY_KEYS:
            self.assertEqual(r[k], RHYTHM_OUT_V21[k], k)
        self.assertEqual((r["bpm"], r["bpm_alternatives"]), (120.0, [60.0, 240.0]))  # no tempo decision
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual(r["double_tempo_evidence"], DTE)

    def test_v21_invalid_output(self):
        for out in bad_v21():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v21_null_ratio_needs_warning(self):
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=DTE_NULL, warnings=[DTE_WARN])))
        self.assertIs(server.check_rhythm(v), v)
        v = json.loads(json.dumps(dict(v, double_tempo_evidence=dict(DTE_NULL, pairs=0))))
        self.assertIs(server.check_rhythm(v), v)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=dict(DTE, mid_to_beat_ratio=0))))
        self.assertIs(server.check_rhythm(v), v)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=dict(DTE, pairs=0))))
        self.assertIsNone(server.check_rhythm(v))  # a ratio needs pairs
        for old in (RHYTHM_OUT, RHYTHM_OUT_V2):  # 0.1.0 / 0.2.0 shapes stay valid
            v = json.loads(json.dumps(old))
            self.assertIs(server.check_rhythm(v), v)

    def test_v2_single_section_and_empty_alternatives_valid(self):
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V2, bpm_alternatives=[], key_change_count=0, chords_grid="frame",
                                       key_sections=[V2_FIELDS["key_sections"][0]])))
        self.assertIs(server.check_rhythm(v), v)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V2, key_sections=[])))
        self.assertIsNone(server.check_rhythm(v))  # count 1 != max(0, 0 - 1)
        v0 = dict(v, key_change_count=0)
        self.assertIs(server.check_rhythm(v0), v0)

    def test_confidence_band_edges(self):
        self.assertEqual([server.confidence_band(c) for c in (0, 0.99, 1, 1.5, 1.51, 3.5, 3.51, 5.32)],
                         ["very_low", "very_low", "low", "low", "good", "good", "excellent", "excellent"])

    def test_null_chord_strength_accepted(self):
        v = json.loads(json.dumps(RHYTHM_OUT))
        v["chords"][0]["strength"] = None
        v["warnings"] = ["chord_strength_unavailable: non-finite native strength; segment strength is null"]
        self.assertIs(server.check_rhythm(v), v)
        self.assertIsNone(server.check_rhythm(dict(v, chords=[dict(v["chords"][0], strength="x")])))

    def test_busy_when_team_lock_held(self):
        fd = os.open(os.path.join(self.env.root, ".analyze.lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            t0 = time.time()
            self.assert_rhythm_failed("busy", "busy")
            self.assertLess(time.time() - t0, 5)  # never waits for the lock
        finally:
            os.close(fd)
        self.assertEqual(self.fw.calls(), [])

    def test_unavailable_bad_flag_and_signed_target(self):
        st, b = self.c.json("POST", A + "&rhythm=2", WAV)
        self.assertEqual((st, b["error"]["code"]), (400, "bad_request"))
        h = self.c.signed("POST", A, WAV)
        st, b = self.c.json("POST", AR, WAV, headers=h)  # the flag is part of the signed TARGET
        self.assertEqual(st, 401)
        self.assertEqual(self.env.argv_log(), [])


class RhythmUnconfiguredTest(Base):
    def test_flag_refused_400_nothing_staged(self):
        h = self.c.signed("POST", AR, WAV)
        st, b = self.c.json("POST", AR, WAV, headers=h)
        self.assertEqual((st, b["error"]["code"]), (400, "rhythm_unavailable"))
        self.assertEqual(self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[0], 404)
        self.assertEqual(self.env.argv_log(), [])
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertFalse(os.path.exists(os.path.join(self.env.root, "results-rhythm")))
        self.assertEqual(self.c.json("POST", A + "&rhythm=0", WAV)[0], 200)

    def test_status_and_report_bytes_unchanged(self):
        st, h, raw = self.c.send("GET", "/status")
        self.assertEqual(raw, b'{"tool_version":"fake","engine":"x"}')
        st, h, raw = self.c.send("GET", "/report/" + RID64)
        self.assertEqual(raw, ('{"result_id":"%s","state":"complete", "x" : 1.50}' % RID64).encode())
        st, b = self.c.json("GET", "/timeline/" + RID64 + "?layer=beats")
        self.assertEqual((st, b["error"]["code"]), (404, "rhythm_not_found"))


class RhythmTimeoutTest(RhythmBase):
    limits = {"rhythm_timeout": 1.0}

    def test_timeout_kills_worker_keeps_200(self):
        self.fw.write(sleep=30)
        t0 = time.time()
        self.assert_rhythm_failed("timeout", "timeout")
        self.assertLess(time.time() - t0, 10)
        pid = self.fw.calls()[-1]["pid"]
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class RhythmBudgetTest(RhythmBase):
    limits = {"handler_total": 4.0}

    def test_no_time_left_skips_worker(self):
        st, b = self.c.json("POST", AR, WAV)
        self.assertEqual((st, b["rhythm"]), (200, {"status": "timeout", "code": "no_time_left"}))
        self.assertEqual(self.fw.calls(), [])


class RhythmReadTest(RhythmBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.c.json("POST", AR, WAV)[1]["rhythm"]["status"], "done")

    def tl(self, q):
        return self.c.json("GET", "/timeline/" + RID64 + "?" + q)

    def test_report_merge(self):
        st, b = self.c.json("GET", "/report/" + RID64)
        self.assertEqual(st, 200)
        self.assertEqual((b["result_id"], b["x"]), (RID64, 1.5))  # native fields kept
        r = b["rhythm"]
        self.assertEqual((r["status"], r["bpm"], r["bpm_confidence"], r["beat_count"], r["chord_segment_count"]),
                         ("done", 120.0, 3.9, 7, 3))
        self.assertEqual(r["key"]["tonic"], "C")
        self.assertEqual(r["downbeats"], {"status": "not_provided"})
        self.assertNotIn("beats", r)
        # another result id without a stored file: native bytes unchanged
        other = "cd" * 32
        st, h, raw = self.c.send("GET", "/report/" + other)
        self.assertEqual(raw, ('{"result_id":"%s","state":"complete", "x" : 1.50}' % RID64).encode())
        # a damaged stored file is reported, never served
        with open(self.rhythm_file(), "w") as f:
            f.write("{")
        self.assertEqual(self.c.json("GET", "/report/" + RID64)[1]["rhythm"],
                         {"status": "failed", "code": "invalid_result"})
        self.assertEqual(self.tl("layer=beats")[1]["error"]["code"], "invalid_result")
        # valid JSON that is not a valid rhythm result: ASR report stays 200, rhythm marked invalid
        for bad in (json.dumps(RHYTHM_OUT).replace('"duration": 6.0', '"duration": 1' + "0" * 400),
                    json.dumps(dict(RHYTHM_OUT, bpm_estimates=[{"bpm": float("nan"), "share": 1}]))):
            with open(self.rhythm_file(), "w") as f:
                f.write(bad)
            st, b = self.c.json("GET", "/report/" + RID64)
            self.assertEqual((st, b["x"], b["rhythm"]), (200, 1.5, {"status": "failed", "code": "invalid_result"}))
            self.assertEqual(self.tl("layer=chords")[1]["error"]["code"], "invalid_result")

    def test_old_and_v2_stored_files(self):
        with open(self.rhythm_file()) as f:
            self.assertNotIn("key_sections", json.load(f))  # stored by the 0.1.0-shaped fake
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual((r["status"], r["bpm"]), ("done", 120.0))
        self.assertEqual([r[k] for k in V2_SUMMARY_KEYS], [None] * len(V2_SUMMARY_KEYS))
        self.assertEqual(len(self.tl("layer=chords")[1]["chords"]), 3)
        st, b = self.c.json("POST", AR, WAV)  # ml cache hit path reuses the old file as is
        self.assertEqual((b["rhythm"]["cache_hit"], b["rhythm"]["key_sections"]), (True, None))
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V2))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual((r["chords_grid"], r["bpm_alternatives"]), ("beat", [60.0, 240.0]))
        self.assertEqual([r[k] for k in V21_SUMMARY_KEYS], [None, None])  # 0.2.0 file: no evidence field
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V21))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual(r["double_tempo_evidence"]["mid_to_beat_ratio"], 1.011)
        for bad in bad_v2() + bad_v21():
            with open(self.rhythm_file(), "w") as f:
                f.write(bad)
            st, b = self.c.json("GET", "/report/" + RID64)
            self.assertEqual((st, b["x"], b["rhythm"]), (200, 1.5, {"status": "failed", "code": "invalid_result"}))

    def test_status_reports_availability(self):
        st, b = self.c.json("GET", "/status")
        self.assertEqual(b["rhythm"], {"configured": True, "python_present": True, "worker_present": True})
        self.assertEqual(b["tool_version"], "fake")

    def test_timeline_beats_paging(self):
        calls = len(self.env.argv_log())
        st, b = self.tl("layer=beats&limit=3")
        self.assertEqual(st, 200)
        self.assertEqual((b["total"], b["cursor"], b["truncated"], b["continuation"]), (7, 0, True, 3))
        self.assertEqual(b["beats"], [{"time": 0.5}, {"time": 1.0}, {"time": 1.5}])
        self.assertEqual((b["layer"], b["layer_status"], b["clock"]), ("beats", "done", "seconds_from_audio_start"))
        b = self.tl("layer=beats&limit=3&cursor=6")[1]
        self.assertEqual((b["beats"], b["truncated"], b["continuation"]), ([{"time": 3.5}], False, None))
        b = self.tl("layer=beats&start=1&end=2.5")[1]
        self.assertEqual([x["time"] for x in b["beats"]], [1.0, 1.5, 2.0, 2.5])
        self.assertEqual(b["range"], {"start": 1.0, "end": 2.5})
        self.assertEqual(len(self.env.argv_log()), calls)  # served by the server, not ml

    def test_timeline_chords(self):
        b = self.tl("layer=chords&start=2.5&end=4.5")[1]
        self.assertEqual([c["label"] for c in b["chords"]], ["Am", "F"])
        self.assertEqual((b["total"], b["reliability"]), (2, "low_reference"))
        b = self.tl("layer=chords&limit=1&cursor=1")[1]
        self.assertEqual(([c["label"] for c in b["chords"]], b["continuation"]), (["Am"], 2))

    def test_timeline_errors_and_words_passthrough(self):
        for q, code in (("layer=beats&start=-1", "invalid_argument"), ("layer=beats&start=3&end=2", "invalid_argument"),
                        ("layer=beats&end=601", "invalid_argument"), ("layer=beats&cursor=8", "invalid_argument"),
                        ("layer=beats&limit=0", "bad_request"), ("layer=beats&limit=201", "bad_request"),
                        ("layer=beats&start=x", "bad_request"), ("layer=downbeats", "bad_request"),
                        ("layer=beats&foo=1", "bad_request")):
            st, b = self.tl(q)
            self.assertEqual((st, b["error"]["code"]), (400, code), q)
        self.assertEqual(self.tl("layer=beats&cursor=7")[1]["beats"], [])  # cursor == total is allowed
        st, b = self.c.json("GET", "/timeline/" + "cd" * 32 + "?layer=chords")
        self.assertEqual((st, b["error"]["code"]), (404, "rhythm_not_found"))
        st, h, raw = self.c.send("GET", "/timeline/" + RID64 + "?layer=words")
        self.assertEqual(raw, ('{"result_id":"%s","layer":"words","words":[]}' % RID64).encode())
        self.assertEqual(self.env.argv_log()[-1], ["timeline", RID64, "--layer=words"])


class RhythmConfigTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)

    def tearDown(self):
        self.env.cleanup()

    def load(self, **over):
        return server.load_config(self.env.write_config(self.env.config(**over)))

    def refused(self, rhythm):
        with self.assertRaises(server.ConfigError):
            self.load(rhythm=rhythm)

    def test_absent_ok_and_valid(self):
        self.assertIsNone(self.load()["rhythm"])
        prod = self.load(test_mode=False, bind=["en0"], port=8765, allow="172.30.1.0/24")
        self.assertIsNone(prod["rhythm"])
        cfg = self.load(rhythm={"python": PY, "worker": self.fw.path})
        self.assertEqual(cfg["rhythm"], {"python": PY, "worker": self.fw.path})
        app = server.App(cfg)
        app.close()
        self.assertTrue(os.path.isdir(os.path.join(self.env.root, "results-rhythm")))

    def test_bad_values_refused(self):
        d = os.path.join(self.env.dir, "d")
        os.mkdir(d)
        noexec = os.path.join(self.env.dir, "noexec")
        open(noexec, "w").close()
        os.chmod(noexec, 0o600)
        gw = os.path.join(self.env.dir, "gw.py")
        open(gw, "w").close()
        os.chmod(gw, 0o664)
        for bad in (None, [], "x", {}, {"python": PY}, {"python": PY, "worker": self.fw.path, "x": 1},
                    {"python": "python3", "worker": self.fw.path}, {"python": PY, "worker": "rhythm_worker.py"},
                    {"python": PY, "worker": self.fw.path + ".missing"}, {"python": PY, "worker": d},
                    {"python": noexec, "worker": self.fw.path}, {"python": PY, "worker": gw},
                    {"python": PY, "worker": 5}):
            self.refused(bad)

    def test_example_config_has_no_rhythm_key(self):
        with open(os.path.join(REPO, "examples", "api.example.json")) as f:
            self.assertNotIn("rhythm", json.load(f))


class WorkerLogicTest(unittest.TestCase):
    """rhythm_worker's pure helpers (stdlib only), run under /usr/bin/python3."""

    def test_bpm_alternatives(self):
        self.assertEqual(rw.bpm_alternatives(120.0), [60.0, 240.0])
        self.assertEqual(rw.bpm_alternatives(109.94), [54.97, 219.88])
        self.assertEqual(rw.bpm_alternatives(70.0), [140.0])  # 35 < 40 dropped
        self.assertEqual(rw.bpm_alternatives(180.0), [90.0])  # 360 > 300 dropped
        self.assertEqual(rw.bpm_alternatives(0.0), [])

    def test_key_windows(self):
        self.assertEqual(rw.key_windows(5.0), [(0.0, 5.0)])
        self.assertEqual(rw.key_windows(60.0), [(0.0, 20.0), (20.0, 40.0), (40.0, 60.0)])
        self.assertEqual(rw.key_windows(69.0), [(0.0, 20.0), (20.0, 40.0), (40.0, 69.0)])  # 9 s tail merged
        self.assertEqual(rw.key_windows(70.0)[-1], (60.0, 70.0))  # 10 s tail kept
        self.assertEqual(len(rw.key_windows(600.0)), 30)
        self.assertEqual(rw.key_windows(216.0)[-1], (200.0, 216.0))

    def test_merge_key_sections(self):
        rows = [(0, 20, "E", "major", 0.8), (20, 40, "E", "major", 0.6), (40, 60, "F", "major", 0.3),
                (60, 80, "F", "major", None), (80, 90, "E", "major", 0.9)]
        self.assertEqual(rw.merge_key_sections(rows), [
            {"start": 0.0, "end": 40.0, "tonic": "E", "scale": "major", "strength": 0.7},
            {"start": 40.0, "end": 80.0, "tonic": "F", "scale": "major", "strength": None},
            {"start": 80.0, "end": 90.0, "tonic": "E", "scale": "major", "strength": 0.9}])

    def test_double_tempo_evidence(self):
        hop = 0.01
        env = [0.0] * 1000  # 10 s
        beats = [0.5 * k + 0.5 for k in range(18)]  # 0.5 .. 9.0
        for b in beats:
            env[int(round(b / hop)) + 2] = 1.0  # peak 20 ms late: inside the +-30 ms pool
        d, w = rw.double_tempo_evidence(env, hop, beats)
        self.assertIsNone(w)
        self.assertEqual((d["pairs"], d["beat_onset_median"], d["mid_onset_median"]), (17, 1.0, 0.0))
        self.assertEqual((d["mid_to_beat_ratio"], d["mid_strong_fraction"], d["window_ms"]), (0.0, 0.0, 30.0))
        for b1, b2 in zip(beats, beats[1:]):
            env[int(round((b1 + b2) / 2 / hop))] = 0.6
        d, _ = rw.double_tempo_evidence(env, hop, beats)
        self.assertEqual((d["mid_onset_median"], d["mid_to_beat_ratio"], d["mid_strong_fraction"]), (0.6, 0.6, 1.0))
        env[int(round(0.75 / hop))] = 0.4  # one midpoint below 0.5 x its beats' mean
        d, _ = rw.double_tempo_evidence(env, hop, beats)
        self.assertEqual(d["mid_strong_fraction"], round(16 / 17.0, 3))
        # a peak 40 ms away from a midpoint falls outside the pool
        env2 = [0.0] * 1000
        for b in beats:
            env2[int(round(b / hop))] = 1.0
        env2[int(round(0.75 / hop)) + 4] = 1.0
        self.assertEqual(rw.double_tempo_evidence(env2, hop, beats)[0]["mid_strong_fraction"], 0.0)
        # too few pairs; gaps outside 0.12..1.5 s are skipped
        d, w = rw.double_tempo_evidence(env, hop, beats[:7])
        self.assertEqual((d["pairs"], d["mid_to_beat_ratio"], d["mid_strong_fraction"]), (6, None, None))
        self.assertTrue(w.startswith("double_tempo_evidence_unavailable:"))
        d, w = rw.double_tempo_evidence(env, hop, [0.5, 0.55, 3.0, 5.0])
        self.assertEqual((d["pairs"], d["beat_onset_median"]), (0, None))
        self.assertTrue(w)
        d, w = rw.double_tempo_evidence(env, hop, [])
        self.assertEqual((d["pairs"], d["mid_to_beat_ratio"]), (0, None))
        # silence at beats: null ratio, onset medians reported
        d, w = rw.double_tempo_evidence([0.0] * 1000, hop, beats)
        self.assertEqual((d["beat_onset_median"], d["mid_to_beat_ratio"]), (0.0, None))
        self.assertIn("~0", w)
        # pool clamps at the envelope edges
        self.assertEqual(rw._pool([0.3, 0.1], hop, -0.02, 0.03), 0.3)
        self.assertEqual(rw._pool([0.3, 0.1], hop, 5.0, 0.03), 0.0)

    def test_beat_chords_vote_and_merge(self):
        step = 0.05
        # frame labels flip every 0.15 s (C C C G G G ...) with C stronger: frame merge gives many tiny segments
        frames = [(round(i * step, 3), "C" if (i // 3) % 2 == 0 else "G", 0.9 if (i // 3) % 2 == 0 else 0.4)
                  for i in range(200)]
        beats = [0.25 + 0.5 * k for k in range(19)]
        frame_segs = rw.merge_frames(frames, 10.0)
        segs = rw.beat_chords(frames, beats, 10.0)
        self.assertGreater(len(frame_segs), 60)
        self.assertEqual([c["label"] for c in segs], ["C"])  # every beat votes C (larger summed strength)
        self.assertEqual((segs[0]["start"], segs[0]["end"]), (0.0, 10.0))
        self.assertEqual(segs[0]["strength"], 0.9)  # mean of the winning label's frames only

    def test_beat_chords_grid_head_tail_and_ties(self):
        frames = [(round(i * 0.1, 3), lab, 0.5) for i, lab in enumerate("AABBBBCCDDEE")]  # 0.0 .. 1.1
        beats = [0.15, 0.55, 0.95]
        segs = rw.beat_chords(frames, beats, 1.2)
        # head [0,0.15): A A; [0.15,0.55): B B B B; [0.55,0.95): C C D D (tie -> earliest, C); tail [0.95,1.2]: E E
        self.assertEqual([(c["start"], c["end"], c["label"]) for c in segs],
                         [(0.0, 0.15, "A"), (0.15, 0.55, "B"), (0.55, 0.95, "C"), (0.95, 1.2, "E")])
        # tie on summed strength and frame count -> earliest frame in the interval wins
        frames = [(0.0, "X", 0.5), (0.1, "Y", 0.5), (0.2, "Y", 0.5), (0.3, "X", 0.5)]
        self.assertEqual([c["label"] for c in rw.beat_chords(frames, [], 0.4)], ["X"])
        # equal sums: more frames wins
        frames = [(0.0, "X", 0.6), (0.1, "Y", 0.3), (0.2, "Y", 0.3)]
        self.assertEqual([c["label"] for c in rw.beat_chords(frames, [], 0.3)], ["Y"])

    def test_beat_chords_null_strength_and_empty_interval(self):
        frames = [(0.0, "C", None), (0.1, "C", None), (0.2, "G", 0.1), (0.3, "C", 0.5)]
        segs = rw.beat_chords(frames, [], 0.4)
        self.assertEqual((segs[0]["label"], segs[0]["strength"]), ("C", None))  # null counts 0, stays null
        frames = [(0.0, "G", None), (0.1, "G", None), (0.2, "C", 0.1)]
        segs = rw.beat_chords(frames, [], 0.3)
        self.assertEqual((segs[0]["label"], segs[0]["strength"]), ("C", 0.1))  # 0.1 > 0 + 0
        # a beat interval with no frame inside takes the frame nearest its midpoint
        frames = [(0.0, "C", 0.9), (1.0, "G", 0.9)]
        segs = rw.beat_chords(frames, [0.1, 0.2, 0.6], 1.5)
        self.assertEqual([(c["start"], c["end"], c["label"]) for c in segs], [(0.0, 0.6, "C"), (0.6, 1.5, "G")])
        self.assertEqual(rw.beat_chords([], [0.5], 1.0), [])
        # beats past the end are clamped; no zero-length segment
        segs = rw.beat_chords([(0.0, "C", 0.5)], [0.0, 1.0, 1.04], 1.0)
        self.assertEqual([(c["start"], c["end"]) for c in segs], [(0.0, 1.0)])


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class RealWorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="mlrhythm-")
        cls.out = {}
        for kind, secs in (("click", "30"), ("chords", "30"), ("modulation", "60"), ("kick100", "30"),
                           ("kick100mid", "30")):
            wav = os.path.join(cls.dir, kind + ".wav")
            subprocess.run([ESSENTIA_PY, "-I", SYNTH, kind, secs, wav], check=True, timeout=120)
            assert server.strict_wav_check(wav)
            p = subprocess.run([ESSENTIA_PY, "-I", WORKER, wav], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=300, env={"PATH": server.CHILD_ENV_PATH, "HOME": os.path.expanduser("~"),
                                                 "LANG": "en_US.UTF-8", "TMPDIR": tempfile.gettempdir()})
            assert p.returncode == 0, p.stderr
            cls.out[kind] = json.loads(p.stdout.decode())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_shape(self):
        for v in self.out.values():
            self.assertIsNotNone(server.check_rhythm(v))
            self.assertEqual(v["downbeats"]["status"], "not_provided")
            self.assertEqual((v["sample_rate"], v["chords_reliability"]), (44100, "low_reference"))
            self.assertIn(v["duration"], (30.0, 60.0))
            self.assertEqual(v["tool"]["essentia_version"], "2.1-beta6-dev")
            self.assertEqual(v["tool"]["version"], "0.2.1")
            self.assertIsNotNone(v["double_tempo_evidence"]["mid_to_beat_ratio"])
            self.assertTrue(v["double_tempo_evidence_note"])
            self.assertEqual(v["key"]["profile"], "bgate")
            self.assertEqual(v["key_change_count"], len(v["key_sections"]) - 1)
            self.assertEqual((v["key_sections"][0]["start"], v["key_sections"][-1]["end"]), (0.0, v["duration"]))
            self.assertEqual(v["chords_grid"], "beat")
            self.assertTrue(v["bpm_alternatives_note"])

    def test_click_120_bpm(self):
        v = self.out["click"]
        self.assertAlmostEqual(v["bpm"], 120.0, delta=2.0)
        self.assertGreater(len(v["beats"]), 50)
        gaps = [b - a for a, b in zip(v["beats"], v["beats"][1:])]
        self.assertAlmostEqual(sorted(gaps)[len(gaps) // 2], 0.5, delta=0.02)
        self.assertEqual(v["bpm_alternatives"], [60.0, 240.0])

    def test_double_tempo_evidence_midpoint_hits(self):
        a, b = self.out["kick100"], self.out["kick100mid"]
        da, db = a["double_tempo_evidence"], b["double_tempo_evidence"]
        # measured (worker 0.2.1): a 0.0 / b 1.011, fraction 0.0 / 1.0; the tracker said 100.04 BPM for both.
        # Only the direction is asserted, not the tracker's tempo for b (100 or 200 would both be legitimate).
        self.assertGreater(db["mid_to_beat_ratio"], da["mid_to_beat_ratio"] + 0.5)
        self.assertGreater(db["mid_strong_fraction"], da["mid_strong_fraction"] + 0.5)
        self.assertLess(self.out["click"]["double_tempo_evidence"]["mid_to_beat_ratio"], 0.2)
        self.assertGreater(min(da["pairs"], db["pairs"]), 30)

    def test_beat_grid_chords(self):
        for v in self.out.values():
            beats, chords = v["beats"], v["chords"]
            min_gap = min(b - a for a, b in zip(beats, beats[1:]))
            edges = {0.0, v["duration"]} | set(beats)
            for c in chords:
                self.assertIn(c["start"], edges)
                self.assertIn(c["end"], edges)
                if c["start"] >= beats[0] and c["end"] <= beats[-1]:  # not head or tail
                    self.assertGreaterEqual(c["end"] - c["start"], min_gap - 0.002, c)
            self.assertLessEqual(len(chords), v["chord_frame_segment_count"])
        # clicks carry no harmony, so frame labels flip; the beat vote removes most of that
        v = self.out["click"]
        self.assertLess(len(v["chords"]), v["chord_frame_segment_count"])

    def test_modulation_key_sections(self):
        v = self.out["modulation"]
        ks = v["key_sections"]
        self.assertEqual((ks[0]["tonic"], ks[0]["scale"]), ("C", "major"))
        self.assertEqual((ks[-1]["tonic"], ks[-1]["scale"]), ("D", "major"))
        # the true boundary is 30 s; with 20 s windows the C section ends at 20 or 40 and the D section starts at
        # 20 or 40 (tolerance one window). A window straddling the change may report a third key (seen: G major).
        self.assertIn(ks[0]["end"], (20.0, 40.0))
        self.assertIn(ks[-1]["start"], (20.0, 40.0))
        self.assertLessEqual(len(ks), 3)
        self.assertTrue(any(w.startswith("key_changes:") for w in v["warnings"]))
        self.assertEqual(self.out["chords"]["key_change_count"], 0)
        self.assertFalse(any(w.startswith("key_changes:") for w in self.out["chords"]["warnings"]))

    def test_chords_key_c_major_or_a_minor(self):
        v = self.out["chords"]
        self.assertIn((v["key"]["tonic"], v["key"]["scale"]), (("C", "major"), ("A", "minor")))
        self.assertTrue(v["chords"])
        labels = {c["label"] for c in v["chords"]}
        self.assertTrue(labels & {"C", "Am", "F", "G"}, labels)

    def test_errors_one_json_line(self):
        bad = os.path.join(self.dir, "bad.wav")
        with open(bad, "wb") as f:
            f.write(b"RIFF0000WAVEjunk")
        for args in ([bad], ["rel.wav"], []):
            p = subprocess.run([ESSENTIA_PY, "-I", WORKER] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=60)
            self.assertEqual(p.returncode, 1)
            self.assertEqual(p.stdout, b"")
            lines = p.stderr.decode().strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn(json.loads(lines[0])["error"]["code"], ("invalid_audio", "invalid_argument"))

    def test_end_to_end_through_server(self):
        env = Env()
        try:
            h = Harness(env, rhythm={"python": ESSENTIA_PY, "worker": WORKER})
            try:
                with open(os.path.join(self.dir, "chords.wav"), "rb") as f:
                    body = f.read()
                st, b = Client(env, h.port).json("POST", AR, body, timeout=120)
                self.assertEqual(st, 200, b)
                self.assertEqual(b["rhythm"]["status"], "done", b["rhythm"])
                self.assertIn(b["rhythm"]["key"]["tonic"], ("C", "A"))
            finally:
                h.stop()
        finally:
            env.cleanup()


if __name__ == "__main__":
    unittest.main()
