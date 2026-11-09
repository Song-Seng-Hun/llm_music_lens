"""POST /earcheck/<id>: re-uploaded audio converted to 44.1 kHz mono, synth child (faked: the test python has no numpy)
run under limit_exec.py --earcheck, stereo WAV returned as base64, every file removed. Plus limit_exec's new modes."""
import base64
import fcntl
import json
import os
import struct
import subprocess
import tempfile
import time
import unittest

from helpers import PY, REPO, RID64, Base, Client, Env, Harness, make_wav_bytes, server, sha
from test_rhythm import RHYTHM_OUT_V40, FakeWorker, RhythmBase

import limit_exec  # noqa: E402

AFCONVERT = "/usr/bin/afconvert"
UPLOAD = make_wav_bytes(2.0)  # 16 kHz mono sine

# Stands in for the rhythm python: limit_exec execs it as PYTHON -I ear_check.py RESULT SRC START END OUT LAYERS.
FAKE_PY = r'''#!/usr/bin/python3 -I
import json, os, resource, struct, sys, time
HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "control.json")) as f:
    ctl = json.load(f)
args = sys.argv[1:]
_, script, result, src, a, b, out, layers = args
with open(src, "rb") as f:
    head = f.read(64)
with open(os.path.join(HERE, "calls.log"), "a") as f:
    f.write(json.dumps({"argv": args, "src_rate": struct.unpack("<I", head[24:28])[0],
                        "src_channels": struct.unpack("<H", head[22:24])[0], "cwd": os.getcwd(),
                        "env_keys": sorted(os.environ), "pid": os.getpid(),
                        "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)[0],
                        "cpu": resource.getrlimit(resource.RLIMIT_CPU)[0]}) + "\n")
time.sleep(ctl.get("sleep", 0))
if ctl.get("exit"):
    sys.stderr.write("x" * 100000)
    sys.exit(ctl["exit"])
n = ctl.get("frames", int((float(b) - float(a)) * 44100))
ch = ctl.get("channels", 2)
d = bytes(range(256)) * (n * 2 * ch // 256) + b"\x00" * (n * 2 * ch % 256)
with open(out, "wb") as f:
    f.write(b"RIFF" + struct.pack("<I", 36 + len(d)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, ch, 44100, 44100 * 2 * ch, 2 * ch, 16)
            + b"data" + struct.pack("<I", len(d)) + d)
'''


class FakePython:
    def __init__(self, env):
        self.dir = os.path.join(env.dir, "fakepy")
        os.mkdir(self.dir)
        self.path = os.path.join(self.dir, "python")
        with open(self.path, "w") as f:
            f.write(FAKE_PY)
        os.chmod(self.path, 0o700)
        self.control = {}
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


def target(start="0", end="1.5", layers=None, rid=RID64, extra=""):
    t = "/earcheck/%s?start=%s&end=%s" % (rid, start, end)
    return t + ("&layers=" + layers if layers is not None else "") + extra


class EarcheckBase(RhythmBase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        self.fp = FakePython(self.env)
        self.h = Harness(self.env, limits=self.limits, rhythm={"python": self.fp.path, "worker": self.fw.path})
        self.c = Client(self.env, self.h.port)
        self.write_result()

    def store(self, raw=None):
        os.makedirs(os.path.dirname(self.rhythm_file()), 0o700, exist_ok=True)
        with open(self.rhythm_file(), "w") as f:
            f.write(raw if raw is not None else json.dumps(dict(RHYTHM_OUT_V40, **self.ids())))

    def post(self, t, body=UPLOAD, **kw):
        return self.c.json("POST", t, body, **kw)

    def assert_clean(self):
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])
        self.assertTrue(self.h.app.gate.acquire(blocking=False))
        self.h.app.gate.release()

    def assert_error(self, t, status, code, body=UPLOAD):
        st, b = self.post(t, body)
        self.assertEqual((st, b["error"]["code"]), (status, code), b)
        self.assert_clean()


@unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
class EarcheckRouteTest(EarcheckBase):
    def test_ok_stereo_wav_and_hash(self):
        self.store()
        os.environ["ML_LEAK_PROBE"] = "1"
        try:
            st, b = self.post(target("0.5", "1.75", "bass,drums,clicks"))
        finally:
            del os.environ["ML_LEAK_PROBE"]
        self.assertEqual(st, 200, b)
        wav = base64.b64decode(b["audio_base64"])
        self.assertEqual({k: b[k] for k in ("result_id", "start", "end", "layers", "format", "sample_rate", "channels")},
                         {"result_id": RID64, "start": 0.5, "end": 1.75, "layers": ["bass", "drums", "clicks"],
                          "format": "wav", "sample_rate": 44100, "channels": 2})
        self.assertEqual((b["bytes"], b["sha256"]), (len(wav), sha(wav)))
        tag, ch, sr, _, _, bits = struct.unpack("<HHIIHH", wav[20:36])
        self.assertEqual((wav[:4], wav[8:12], tag, ch, sr, bits), (b"RIFF", b"WAVE", 1, 2, 44100, 16))
        self.assertEqual(len(wav), 44 + int(1.25 * 44100) * 4)
        call, = self.fp.calls()
        self.assertEqual(call["argv"], ["-I", os.path.join(REPO, "ear_check.py"), self.rhythm_file(),
                                        call["argv"][3], "0.500000", "1.750000", call["argv"][6], "bass,drums,clicks"])
        self.assertTrue(call["argv"][3].endswith("/listen.wav") and call["argv"][6].endswith("/earcheck.wav"))
        self.assertRegex(call["argv"][3], r"/uploads/ec-[0-9a-f]{32}/listen\.wav\Z")
        self.assertEqual((call["src_rate"], call["src_channels"]), (44100, 1))  # listening rate, not the 16 kHz analyze path
        self.assertEqual(call["cwd"], "/")
        self.assertTrue({"HOME", "LANG", "PATH", "TMPDIR"} <= set(call["env_keys"]))  # the /usr/bin/python3 shim adds its own
        self.assertNotIn("ML_LEAK_PROBE", call["env_keys"])
        self.assertEqual((call["fsize"], call["cpu"]), (16 * 1024 * 1024, 60))
        self.assertEqual(self.env.argv_log(), [])  # no ml call
        self.assert_clean()
        log = self.env.log_text()
        self.assertNotIn(b["audio_base64"][:64], log)
        ev = [e for e in self.env.log_events() if e.get("event") == "earcheck_exit"]
        self.assertEqual((len(ev), ev[0]["rc"], ev[0]["result_id"]), (1, 0, RID64))
        ev = [e for e in self.env.log_events() if e.get("event") == "earcheck_start"]
        self.assertEqual((len(ev), ev[0]["op"]), (1, call["argv"][3].split("/")[-2]))

    def test_default_layers_and_max_span_fits(self):
        self.store()
        long = make_wav_bytes(31.0, rate=8000)
        st, b = self.post(target("0", "30"), long)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["layers"], ["melody", "bass", "other", "chords", "drums"])
        self.assertEqual(b["bytes"], 5292044)
        self.assertLess(len(json.dumps(b)), server.DEFAULT_LIMITS["response_max"])
        self.assert_clean()

    def test_query_errors(self):
        self.store()
        for t in (target("0", "30.5"), target("-1", "2"), target("2", "2"), target("3", "2"), target("0", "1", "bass,piano"),
                  target("0", "1", "bass,bass"), target("0", "1", ""), target(extra="&x=1"), target(extra="&start=0"),
                  target("1e1", "2"), target("-0", "2"), target("-0.0", "2"), target("0", "-0"), "/earcheck/%s?end=2" % RID64, "/earcheck/%s" % RID64):
            st, b = self.post(t)
            self.assertEqual((st, b["error"]["code"]), (400, "bad_request"), t)
        for t, d in ((target("0", "30.5"), True), (target("3", "2"), True), (target("0", "1", "bass,piano"), False)):
            self.assertEqual("at most 30 s" in self.post(t)[1]["error"].get("detail", ""), d, t)
        self.assertEqual(self.fp.calls(), [])
        self.assert_clean()

    def test_get_not_allowed(self):
        st, b = self.c.json("GET", target())
        self.assertEqual((st, b["error"]["code"]), (405, "method_not_allowed"))

    def test_missing_and_damaged_result(self):
        self.assert_error(target(), 404, "rhythm_not_found")
        self.store("{")
        self.assert_error(target(), 500, "invalid_result")
        self.assertEqual(self.fp.calls(), [])

    def test_start_after_audio_end(self):
        self.store()
        self.assert_error(target("5", "6"), 400, "invalid_argument")
        self.assertEqual(self.fp.calls(), [])

    def test_bad_audio(self):
        self.store()
        self.assert_error(target(), 400, "invalid_audio", body=b"OggS" + b"\x00" * 100)
        self.assert_error(target(), 400, "invalid_audio", body=b"ID3\x04\x00" + b"\x00" * 200)  # afconvert fails

    def test_body_hash_mismatch(self):
        self.store()
        h = self.c.signed("POST", target(), UPLOAD)
        st, b = self.c.json("POST", target(), UPLOAD[:-2] + b"\x01\x02", headers=h)
        self.assertEqual((st, b["error"]["code"]), (401, "unauthorized"))
        self.assert_clean()

    def test_synth_failure(self):
        self.store()
        self.fp.write(exit=3)
        self.assert_error(target(), 500, "earcheck_failed")
        self.fp.write(exit=0, channels=1)  # mono output: not the stereo file promised
        self.assert_error(target(), 500, "earcheck_failed")
        self.fp.write(channels=2, frames=int(1.5 * 44100) + 50)  # longer than the span
        self.assert_error(target(), 500, "earcheck_failed")

    def test_busy(self):
        self.store()
        self.assertTrue(self.h.app.gate.acquire(blocking=False))
        try:
            st, b = self.post(target())
        finally:
            self.h.app.gate.release()
        self.assertEqual((st, b["error"]["code"]), (409, "busy"))
        fd = os.open(self.h.app.team_lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)  # a Mac-side analyze
            st, b = self.post(target())
        finally:
            os.close(fd)
        self.assertEqual((st, b["error"]["code"]), (409, "busy"))
        self.assertEqual(self.fp.calls(), [])
        self.assert_clean()

    def test_flock_taken_after_upload(self):
        self.store()
        seen = []
        orig = self.h.app.convert

        def convert(*a, **kw):  # the flock is held while converting
            fd = os.open(self.h.app.team_lock, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.append("free")
            except BlockingIOError:
                seen.append("held")
            finally:
                os.close(fd)
            return orig(*a, **kw)
        self.h.app.convert = convert
        orig_stage = server.Handler.stage

        def stage(handler, *a):  # ... but not while the body is received
            fd = os.open(self.h.app.team_lock, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.append("free")
            except BlockingIOError:
                seen.append("held")
            finally:
                os.close(fd)
            return orig_stage(handler, *a)
        server.Handler.stage = stage
        try:
            st, b = self.post(target())
        finally:
            server.Handler.stage = orig_stage
        self.assertEqual((st, seen), (200, ["free", "held"]))
        self.assert_clean()

    def test_expect_continue(self):
        self.store()
        st, b = self.post(target(), extra=[("Expect", "100-continue")])
        self.assertEqual(st, 200, b)
        self.assert_clean()

    def test_status_flag(self):
        self.assertTrue(self.c.json("GET", "/status")[1]["rhythm"]["earcheck_available"])


@unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
class EarcheckTimeoutTest(EarcheckBase):
    limits = {"earcheck_timeout": 1.0}

    def test_timeout_kills_group_and_cleans(self):
        self.store()
        self.fp.write(sleep=30)
        t0 = time.time()
        st, b = self.c.json("POST", target(), UPLOAD)
        self.assertLess(time.time() - t0, 15)
        self.assertEqual((st, b["error"]["code"]), (504, "timeout"))
        pid = self.fp.calls()[0]["pid"]
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assert_clean()


class EarcheckLeftoverTest(Base):
    def test_startup_removes_only_earcheck_and_sheet_dirs(self):
        up = os.path.join(self.env.root, "uploads")
        ec, rl, other = os.path.join(up, "ec-" + "a" * 32), os.path.join(up, "rl-" + "c" * 32), os.path.join(up, "b" * 32)
        with open(os.path.join(up, "rl-" + "d" * 32), "wb") as f:  # a file of that name is not a staging directory
            f.write(b"x")
        for d in (ec, rl, other):
            os.mkdir(d, 0o700)
            for n in ("upload.wav", "listen.wav", "earcheck.wav"):
                with open(os.path.join(d, n), "wb") as f:
                    f.write(b"RIFF")
        with open(os.path.join(rl, "view.m4a"), "wb") as f:
            f.write(b"x")
        self.restart()
        self.assertEqual(self.env.uploads_entries(), ["b" * 32, "rl-" + "d" * 32])
        self.assertEqual(sorted(os.listdir(other)), ["earcheck.wav", "listen.wav", "upload.wav"])
        ev = [e for e in self.env.log_events() if e.get("event") == "earcheck_leftovers_removed"]
        self.assertEqual([e["count"] for e in ev], [2])
        for n in os.listdir(other):
            os.unlink(os.path.join(other, n))
        os.rmdir(other)
        os.unlink(os.path.join(up, "rl-" + "d" * 32))


class EarcheckUnconfiguredTest(Base):
    def test_rhythm_not_configured(self):
        st, b = self.c.json("POST", target(), UPLOAD)
        self.assertEqual((st, b["error"]["code"]), (503, "earcheck_unavailable"))
        self.assertEqual(self.env.uploads_entries(), [])


class EarcheckLimitsTest(unittest.TestCase):
    def test_max_span_response_fits(self):
        wav = 44 + int(server.EARCHECK_MAX_SPAN * server.LISTEN_RATE) * 4
        self.assertEqual(wav, 5292044)
        self.assertLess(4 * ((wav + 2) // 3) + 1024, server.DEFAULT_LIMITS["response_max"])
        self.assertLessEqual(wav, limit_exec.EARCHECK_FSIZE_BYTES)
        self.assertGreater(limit_exec.LISTEN_FSIZE_BYTES, 44 + 600 * 44100 * 2)
        self.assertEqual(limit_exec.LISTEN_FSIZE_BYTES, server.LISTEN_MAX_BYTES)

    def test_listen_and_earcheck_commands(self):
        self.assertEqual(limit_exec.command("/a/s.m4a", "/a/d.wav", listen=True),
                         ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@44100", "-c", "1", "/a/s.m4a", "/a/d.wav"])
        self.assertEqual(limit_exec.command("/a/s.m4a", "/a/d.wav"),
                         ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", "/a/s.m4a", "/a/d.wav"])
        self.assertEqual(limit_exec.earcheck_command("/p", "/s.py", "/r.json", "/a.wav", "1.0", "2.0", "/o.wav", "bass"),
                         ["/p", "-I", "/s.py", "/r.json", "/a.wav", "1.0", "2.0", "/o.wav", "bass"])
        le = os.path.join(REPO, "limit_exec.py")
        d = tempfile.mkdtemp()
        try:
            out = os.path.join(d, "o.wav")
            for argv in (["--listen", "rel.m4a", out], ["--listen", "/a.m4a"], ["--listen", "/a.m4a", out, "x"],
                         ["--earcheck", "/p", "/s", "/r", "/a", "-1", "2", out, "bass"],
                         ["--earcheck", "/p", "/s", "/r", "/a", "1", "2", out, "BASS;rm"],
                         ["--earcheck", "rel", "/s", "/r", "/a", "1", "2", out, "bass"],
                         ["--earcheck", "/p", "/s", "/r", "/a", "1", "2", out]):
                p = subprocess.run([PY, "-I", le] + argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                self.assertEqual(p.returncode, 2, argv)
            open(out, "w").close()
            for argv in (["--listen", "/a.m4a", out], ["--earcheck", "/p", "/s", "/r", "/a", "1", "2", out, "bass"]):
                p = subprocess.run([PY, "-I", le] + argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                self.assertEqual((p.returncode, p.stderr), (2, b"destination exists\n"), argv)
        finally:
            os.unlink(out)
            os.rmdir(d)

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_listen_converts_to_44k_mono(self):
        d = tempfile.mkdtemp()
        src, dst = os.path.join(d, "s.wav"), os.path.join(d, "d.wav")
        try:
            with open(src, "wb") as f:
                f.write(make_wav_bytes(1.0, rate=22050, channels=2))
            p = subprocess.run([PY, "-I", os.path.join(REPO, "limit_exec.py"), "--listen", src, dst],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            self.assertEqual(p.returncode, 0)
            n = server.pcm16_wav_data(dst, server.LISTEN_MAX_BYTES, 44100, 1)
            self.assertAlmostEqual(n / 88200.0, 1.0, delta=0.05)
            self.assertFalse(server.strict_wav_check(dst))  # not the 16 kHz analyze format
        finally:
            for x in (src, dst):
                if os.path.exists(x):
                    os.unlink(x)
            os.rmdir(d)


if __name__ == "__main__":
    unittest.main()
