import json
import math
import os
import resource
import signal
import struct
import subprocess
import tempfile
import threading
import time
import unittest

from helpers import (ANALYZE_OUT, PY, REPO, RID64, Base, Client, Env, SubprocessServer, build, connect,
                     make_wav_bytes, server, sha, silent_wav_bytes, sign)

A = "/analyze?language=en&emotion_mode=voice"
WAV = silent_wav_bytes(0.5)
AFCONVERT = "/usr/bin/afconvert"


PCM_GUID = bytes.fromhex("0100000000001000800000aa00389b71")


def wav_with(fmt=(1, 1, 16000, 32000, 2, 16), data=b"\x00\x00" * 800, riff_delta=0, extra=b"", order="fd",
             data_len_delta=0, ext=None):
    fb = struct.pack("<HHIIHH", *fmt) + (struct.pack("<HHI16s", *ext) if ext else b"")
    f = b"fmt " + struct.pack("<I", len(fb)) + fb
    d = b"data" + struct.pack("<I", len(data) + data_len_delta) + data
    body = b"WAVE" + ((f + d) if order == "fd" else (d + f)) + extra
    return b"RIFF" + struct.pack("<I", len(body) + riff_delta) + body


class StrictWavTest(unittest.TestCase):
    def check(self, data):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
        try:
            return server.strict_wav_check(f.name)
        finally:
            os.unlink(f.name)

    def test_accepts(self):
        self.assertTrue(self.check(wav_with()))
        self.assertTrue(self.check(make_wav_bytes(0.2)))
        fllr = b"FLLR" + struct.pack("<I", 4) + b"\x00" * 4
        self.assertTrue(self.check(wav_with(extra=b"LIST" + struct.pack("<I", 3) + b"abc\x00")))
        fixture = os.environ.get("MUSIC_LENS_FIXTURE_WAV", "")
        if os.path.exists(fixture):
            self.assertTrue(server.strict_wav_check(fixture))
        self.assertTrue(fllr)
        ext_fmt = (0xFFFE, 1, 16000, 32000, 2, 16)
        self.assertTrue(self.check(wav_with(fmt=ext_fmt, ext=(22, 16, 4, PCM_GUID))), "extensible PCM mono")
        self.assertTrue(self.check(wav_with(fmt=ext_fmt, ext=(22, 16, 0, PCM_GUID))))

    def test_rejects(self):
        cases = {
            "stereo": wav_with(fmt=(1, 2, 16000, 64000, 4, 16)),
            "44k": wav_with(fmt=(1, 1, 44100, 88200, 2, 16)),
            "8bit": wav_with(fmt=(1, 1, 16000, 16000, 1, 8)),
            "float": wav_with(fmt=(3, 1, 16000, 64000, 4, 32)),
            "extensible": wav_with(fmt=(0xFFFE, 1, 16000, 32000, 2, 16)),
            "riff_size": wav_with(riff_delta=4),
            "data_past_end": wav_with(data_len_delta=10, riff_delta=0),
            "empty": wav_with(data=b""),
            "odd": wav_with(data=b"\x00" * 3, extra=b"\x00"),
            "data_first": wav_with(order="df"),
            "trailing_garbage": wav_with(extra=b"xyz"),
            "not_riff": b"RIFX" + wav_with()[4:],
            "short": b"RIFF\x04\x00\x00\x00WAVE",
            "ext_float": wav_with(fmt=(0xFFFE, 1, 16000, 32000, 2, 16),
                                  ext=(22, 16, 4, bytes.fromhex("0300000000001000800000aa00389b71"))),
            "ext_validbits": wav_with(fmt=(0xFFFE, 1, 16000, 32000, 2, 16), ext=(22, 12, 4, PCM_GUID)),
            "ext_mask": wav_with(fmt=(0xFFFE, 1, 16000, 32000, 2, 16), ext=(22, 16, 3, PCM_GUID)),
            "two_data": wav_with(extra=b"data" + struct.pack("<I", 2) + b"\x00\x00"),
        }
        for k, v in cases.items():
            self.assertFalse(self.check(v), k)
        self.assertFalse(self.check(silent_wav_bytes(600.05)), "over 600 s")
        self.assertTrue(self.check(silent_wav_bytes(600.0)), "600 s exactly")

    def test_sniff(self):
        self.assertEqual(server.sniff(WAV[:16]), "wav")
        self.assertEqual(server.sniff(b"ID3\x04\x00" + b"\x00" * 11), "mp3")
        self.assertEqual(server.sniff(b"\xff\xfb\x90\x00" + b"\x00" * 12), "mp3")
        self.assertEqual(server.sniff(b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00"), "m4a")
        self.assertIsNone(server.sniff(b"\x00\x00\x00\x20ftypqt  \x00\x00\x00\x00"))
        self.assertIsNone(server.sniff(b"OggS" + b"\x00" * 12))
        self.assertIsNone(server.sniff(b"fLaC" + b"\x00" * 12))
        self.assertIsNone(server.sniff(b"\xff\xf1" + b"\x00" * 14))  # ADTS AAC, not layer III


class LimitExecTest(unittest.TestCase):
    def test_limits_applied_in_helper_process(self):
        code = ("import sys; sys.path.insert(0, %r); import limit_exec, resource; limit_exec.apply_limits(); "
                "print([resource.getrlimit(r) for r in (resource.RLIMIT_CPU, resource.RLIMIT_FSIZE, "
                "resource.RLIMIT_NOFILE)])" % REPO)
        out = subprocess.run([PY, "-I", "-c", code], stdout=subprocess.PIPE, check=True).stdout.decode()
        self.assertEqual(json.loads(out.replace("(", "[").replace(")", "]")),
                         [[60, 60], [24 * 1024 * 1024] * 2, [32, 32]])
        # parent process limits untouched
        self.assertNotEqual(resource.getrlimit(resource.RLIMIT_FSIZE)[0], 24 * 1024 * 1024)

    def test_argv_fixed_and_dst_must_not_exist(self):
        import limit_exec
        self.assertEqual(limit_exec.command("/a/s.m4a", "/a/d.wav"),
                         ["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", "/a/s.m4a", "/a/d.wav"])
        d = tempfile.mkdtemp()
        try:
            dst = os.path.join(d, "x.wav")
            open(dst, "w").close()
            p = subprocess.run([PY, "-I", os.path.join(REPO, "limit_exec.py"), "/nonexistent.m4a", dst],
                               stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 2)
            p = subprocess.run([PY, "-I", os.path.join(REPO, "limit_exec.py"), "rel.m4a", dst + "2"],
                               stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 2)
        finally:
            import shutil
            shutil.rmtree(d)

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_fsize_limit_stops_long_conversion(self):
        d = tempfile.mkdtemp()
        try:
            src_wav = os.path.join(d, "long.wav")
            with open(src_wav, "wb") as f:
                f.write(silent_wav_bytes(900))
            m4a = os.path.join(d, "long.m4a")
            subprocess.run([AFCONVERT, "-f", "m4af", "-d", "aac", src_wav, m4a], check=True)
            out = os.path.join(d, "out.wav")
            p = subprocess.run([PY, "-I", os.path.join(REPO, "limit_exec.py"), m4a, out],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            self.assertNotEqual(p.returncode, 0)
            self.assertLessEqual(os.path.getsize(out) if os.path.exists(out) else 0, 24 * 1024 * 1024)
        finally:
            import shutil
            shutil.rmtree(d)


class AnalyzeTest(Base):
    def test_wav_flow_receipt_and_cleanup_scope(self):
        other = os.path.join(self.env.inbox, "someone-else.wav")
        with open(other, "wb") as f:
            f.write(WAV)
        foreign = os.path.join(self.env.root, "uploads", "foreign")
        os.mkdir(foreign)
        lock = os.path.join(self.env.root, ".analyze.lock")
        open(lock, "w").close()
        h = self.c.signed("POST", A + "&name=x", WAV)
        st, b = self.c.json("POST", A + "&name=x", WAV, headers=h)
        self.assertEqual(st, 200)
        rid = h["X-ML-Nonce"]
        for k, v in ANALYZE_OUT.items():
            self.assertEqual(b[k], v)  # native fields unchanged
        self.assertEqual((b["request_id"], b["upload_sha256"], b["name"]), (rid, sha(WAV), "x"))
        argv = self.env.argv_log()[-1]
        self.assertEqual(argv[0], "analyze")
        self.assertRegex(argv[1], r"\Aapi-[0-9a-f]{32}\.wav\Z")
        self.assertEqual(argv[2:], ["--language", "en", "--emotion-mode", "voice"])
        seen = self.env.seen_log()[-1]
        self.assertTrue(seen["exists"])
        self.assertEqual((seen["head"], seen["size"]), ("RIFF", len(WAV)))
        # receipt
        st, r = self.c.json("GET", "/receipt/" + rid)
        self.assertEqual((st, r), (200, {"request_id": rid, "state": "done", "http_status": 200,
                                         "result_id": RID64, "error_code": None, "upload_sha256": sha(WAV)}))
        other_client = Client(self.env, self.h.port, "codex-window")
        self.assertEqual(other_client.json("GET", "/receipt/" + rid)[0], 404)
        # cleanup: only request-owned paths
        self.assertFalse(os.path.exists(os.path.join(self.env.inbox, argv[1])))
        self.assertTrue(os.path.exists(other))
        self.assertTrue(os.path.isdir(foreign))
        self.assertTrue(os.path.exists(lock))
        self.assertEqual(self.env.uploads_entries(), ["foreign"])
        self.assertEqual(sorted(os.listdir(self.env.inbox)), ["someone-else.wav"])

    def test_argv_only_no_shell(self):
        name = sign.encode_name("$(touch pwned); `id` | x")
        st, b = self.c.json("POST", "/analyze?language=auto&emotion_mode=none&name=" + name, WAV)
        self.assertEqual(st, 200)
        argv = self.env.argv_log()[-1]
        self.assertEqual(len(argv), 6)
        self.assertNotIn("touch", json.dumps(argv))
        self.assertEqual(b["name"], "$(touch pwned); `id` | x")

    def test_invalid_audio_never_reaches_ml(self):
        for body in (b"\x00" * 100, b"OggS" + b"\x00" * 100):
            h = self.c.signed("POST", A, body)
            st, b = self.c.json("POST", A, body, headers=h)
            self.assertEqual((st, b["error"]["code"]), (400, "invalid_audio"))
            st, r = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])
            self.assertEqual((r["state"], r["error_code"]), ("failed", "invalid_audio"))
        self.assertEqual(self.env.argv_log(), [])
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])

    def test_second_job_409_before_staging(self):
        self.env.write_control(analyze={"sleep": 2.0, "stdout": json.dumps(ANALYZE_OUT)})
        first = {}

        def go():
            first["r"] = self.c.json("POST", A, WAV)
        t = threading.Thread(target=go)
        t.start()
        time.sleep(0.8)
        self.assertEqual(len(self.env.uploads_entries()), 1)
        h = self.c.signed("POST", A, WAV)
        st, b = self.c.json("POST", A, WAV, headers=h)
        self.assertEqual((st, b["error"]["code"]), (409, "busy"))
        self.assertEqual(len(self.env.uploads_entries()), 1)  # nothing staged for the second
        self.assertEqual(self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[0], 404)  # never launched, no receipt
        # read calls are not blocked by the job
        self.assertEqual(self.c.json("GET", "/status")[0], 200)
        t.join()
        self.assertEqual(first["r"][0], 200)
        self.assertEqual(self.c.json("POST", A, WAV)[0], 200)  # gate released

    def test_wrapper_busy_maps_409(self):
        self.env.write_control(analyze={"exit": 1, "stderr_lines": [
            '{"error": {"code": "busy", "message": "another team analyze holds the team-job lock"}}']})
        h = self.c.signed("POST", A, WAV)
        st, b = self.c.json("POST", A, WAV, headers=h)
        self.assertEqual((st, b["error"]["code"]), (409, "busy"))
        r = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]
        self.assertEqual((r["state"], r["http_status"], r["error_code"]), ("failed", 409, "busy"))
        self.assertEqual(os.listdir(self.env.inbox), [])

    def test_operation_identity_not_redispatched(self):
        n = "7" * 32
        self.assertEqual(self.c.json("POST", A, WAV, nonce=n)[0], 200)
        calls = len(self.env.argv_log())
        # same (client, nonce) re-signed later: nonce still live -> 401; never re-dispatched
        self.assertEqual(self.c.json("POST", A, WAV, nonce=n)[0], 401)
        self.assertEqual(len(self.env.argv_log()), calls)
        # other client may use the same nonce: a distinct operation
        other = Client(self.env, self.h.port, "codex-window")
        st, b = other.json("POST", A, WAV, nonce=n)
        self.assertEqual((st, b["request_id"]), (200, n))
        self.assertEqual(other.json("GET", "/receipt/" + n)[1]["state"], "done")

    def test_disk_floor(self):
        self.h.app.limits["disk_floor"] = 1 << 62
        h = self.c.signed("POST", A, WAV)
        st, b = self.c.json("POST", A, WAV, headers=h)
        self.assertEqual((st, b["error"]["code"]), (503, "insufficient_storage"))
        self.assertEqual(self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[0], 404)
        self.assertEqual(self.env.uploads_entries(), [])

    def test_receipt_storage_failure_launches_nothing(self):
        app = self.h.app
        real = app.receipts.log.append

        def boom(obj):
            raise OSError("disk")
        app.receipts.log.append = boom
        try:
            st, b = self.c.json("POST", A, WAV)
        finally:
            app.receipts.log.append = real
        self.assertEqual((st, b["error"]["code"]), (503, "storage_error"))
        self.assertEqual(self.env.argv_log(), [])
        self.assertEqual(self.env.uploads_entries(), [])

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_m4a_conversion_path(self):
        self._convert_case(make_wav_bytes(1.0, rate=44100, channels=2))

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_m4a_conversion_path_mono16k_extensible(self):
        self._convert_case(make_wav_bytes(1.0))

    def _convert_case(self, wav_bytes):
        d = self.env.dir
        src = os.path.join(d, "tone.wav")
        with open(src, "wb") as f:
            f.write(wav_bytes)
        m4a = os.path.join(d, "tone.m4a")
        subprocess.run([AFCONVERT, "-f", "m4af", "-d", "aac", src, m4a], check=True)
        with open(m4a, "rb") as f:
            body = f.read()
        h = self.c.signed("POST", A, body)
        st, b = self.c.json("POST", A, body, headers=h)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["upload_sha256"], sha(body))
        seen = self.env.seen_log()[-1]
        self.assertEqual(seen["head"], "RIFF")
        ev = [e for e in self.env.log_events() if e.get("event") == "convert_exit"]
        self.assertEqual(ev[-1]["rc"], 0)
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])


def wav18(rate, channels, bits, seconds, extra=b""):
    """PCM WAV with an 18-byte fmt chunk (WAVEFORMATEX, cbSize 0), as SAPI TTS writes it."""
    width = bits // 8
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        v = int(8000 * math.sin(2 * math.pi * 440.0 * i / rate))
        frames += (struct.pack("<h", v) if width == 2 else struct.pack("<i", v << 8)[:3]) * channels
    fb = struct.pack("<HHIIHHH", 1, channels, rate, rate * channels * width, channels * width, bits, 0)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fb)) + fb + extra + b"data" + struct.pack("<I", len(frames))
            + bytes(frames))
    return b"RIFF" + struct.pack("<I", len(body)) + body


class WavConvertTest(Base):
    def convert_events(self):
        return [e for e in self.env.log_events() if e.get("event") == "convert_exit"]

    def post(self, body):
        h = self.c.signed("POST", A, body)
        st, b = self.c.json("POST", A, body, headers=h)
        return st, b, h["X-ML-Nonce"]

    def assert_clean(self):
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_sapi_22050_mono_fmt18_converts(self):
        body = wav18(22050, 1, 16, 1.0)
        self.assertFalse(StrictWavTest().check(body))
        st, b, rid = self.post(body)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["upload_sha256"], sha(body))
        self.assertEqual(self.convert_events()[-1]["rc"], 0)
        seen = self.env.seen_log()[-1]
        self.assertEqual(seen["head"], "RIFF")
        self.assertNotEqual(seen["size"], len(body))  # ml saw the converted file, not the upload
        r = self.c.json("GET", "/receipt/" + rid)[1]
        self.assertEqual((r["state"], r["error_code"], r["upload_sha256"]), ("done", None, sha(body)))
        self.assert_clean()

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_stereo_44k_converts(self):
        body = make_wav_bytes(1.0, rate=44100, channels=2)
        st, b, _ = self.post(body)
        self.assertEqual(st, 200, b)
        self.assertEqual(self.convert_events()[-1]["rc"], 0)
        self.assert_clean()

    def test_canonical_wav_skips_afconvert(self):
        for body in (WAV, wav18(16000, 1, 16, 0.5)):
            st, b, _ = self.post(body)
            self.assertEqual(st, 200, b)
            self.assertEqual(self.env.seen_log()[-1]["size"], len(body))  # the upload itself was linked
        self.assertEqual(self.convert_events(), [])
        self.assert_clean()

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_corrupt_riff_invalid_audio_and_cleanup(self):
        junk = b"WAVE" + b"\x01\x02garbage!" * 64
        body = b"RIFF" + struct.pack("<I", len(junk)) + junk
        st, b, rid = self.post(body)
        self.assertEqual((st, b["error"]["code"]), (400, "invalid_audio"))
        self.assertNotEqual(self.convert_events()[-1]["rc"], 0)
        r = self.c.json("GET", "/receipt/" + rid)[1]
        self.assertEqual((r["state"], r["error_code"]), ("failed", "invalid_audio"))
        self.assertEqual(self.env.argv_log(), [])
        self.assert_clean()

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_converted_over_600s_rejected(self):
        body = silent_wav_bytes(610.0, rate=8000)  # 8 kHz upload, ~19.5 MB at 16 kHz: under FSIZE, over 600 s
        st, b, rid = self.post(body)
        self.assertEqual((st, b["error"]["code"]), (400, "invalid_audio"))
        self.assertEqual(self.convert_events()[-1]["rc"], 0)  # conversion ran; the duration check rejected it
        self.assertEqual(self.env.argv_log(), [])
        self.assertEqual(self.c.json("GET", "/receipt/" + rid)[1]["error_code"], "invalid_audio")
        self.assert_clean()


class ConvertTimeoutTest(Base):
    limits = {"convert_timeout": 1.0}

    def setUp(self):
        self.env = Env()
        fake = os.path.join(self.env.dir, "slow_limit_exec.py")
        self.pidfile = os.path.join(self.env.dir, "conv.pid")
        with open(fake, "w") as f:
            f.write("import os, sys, time\nopen(%r, 'w').write(str(os.getpid()))\n"
                    "sys.stdout.write('y' * 100000)\nsys.stdout.flush()\ntime.sleep(30)\n" % self.pidfile)
        from helpers import Harness
        self.h = Harness(self.env, limits=self.limits, limit_exec=fake)
        self.c = Client(self.env, self.h.port)

    def test_convert_wall_timeout_kills_group(self):
        body = b"ID3\x04\x00" + b"\x00" * 200
        h = self.c.signed("POST", A, body)
        t0 = time.time()
        st, b = self.c.json("POST", A, body, headers=h)
        self.assertLess(time.time() - t0, 10)
        self.assertEqual((st, b["error"]["code"]), (504, "timeout"))
        with open(self.pidfile) as f:
            pid = int(f.read())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(self.env.argv_log(), [])
        self.assertEqual(self.env.uploads_entries(), [])
        r = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]
        self.assertEqual((r["state"], r["error_code"]), ("failed", "timeout"))


class HandlerDeadlineTest(Base):
    limits = {"handler_total": 1.0}

    def test_504_running_then_receipt_done(self):
        self.env.write_control(analyze={"sleep": 2.5, "stdout": json.dumps(ANALYZE_OUT)})
        h = self.c.signed("POST", A, WAV)
        st, b = self.c.json("POST", A, WAV, headers=h)
        rid = h["X-ML-Nonce"]
        self.assertEqual((st, b), (504, {"request_id": rid, "state": "running"}))
        st, r = self.c.json("GET", "/receipt/" + rid)
        self.assertEqual(r["state"], "running")
        inbox_name = self.env.argv_log()[-1][1]
        self.assertTrue(os.path.exists(os.path.join(self.env.inbox, inbox_name)))  # no cleanup while ml runs
        self.assertEqual(self.c.json("POST", A, WAV)[1]["error"]["code"], "busy")
        r = self.wait_receipt(rid, "done")
        self.assertEqual(r["result_id"], RID64)
        time.sleep(0.2)
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])


class UploadDeadlineTest(Base):
    limits = {"upload_deadline": 1.0}

    def test_slow_upload_408(self):
        h = self.c.signed("POST", A, WAV)
        s = connect(self.h.port, self.env.cert)
        s.sendall(build("POST", A, list(h.items()), WAV[:50]))
        from helpers import read_response
        st, _, body = read_response(s, timeout=10)
        s.close()
        self.assertEqual(st, 408)
        r = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]
        self.assertEqual((r["state"], r["error_code"]), ("failed", "request_timeout"))
        self.assertEqual(self.env.uploads_entries(), [])


class ReceiptCapTest(Base):
    def test_cap_full_with_unknown_receipts(self):
        p = os.path.join(self.env.root, "api", "receipts.log")
        now = int(time.time())
        with open(p, "a") as f:
            f.write(json.dumps({"client": "claude-window", "request_id": "5" * 32, "op": "0" * 32,
                                "state": "running", "created": now, "updated": now}) + "\n")
            f.write(json.dumps({"client": "claude-window", "request_id": "6" * 32, "op": "1" * 32,
                                "state": "done", "created": now - 40 * 86400, "updated": now - 40 * 86400}) + "\n")
        self.restart(limits={"receipt_cap": 1})
        self.assertEqual(self.c.json("GET", "/receipt/" + "5" * 32)[1]["state"], "unknown")
        self.assertEqual(self.c.json("GET", "/receipt/" + "6" * 32)[0], 404)  # >30 days pruned
        st, b = self.c.json("POST", A, WAV)
        self.assertEqual((st, b["error"]["code"]), (503, "receipt_store_full"))
        self.assertEqual(self.env.argv_log(), [])

    def test_cap_prunes_oldest_final(self):
        self.restart(limits={"receipt_cap": 2})
        ids = []
        for _ in range(3):
            h = self.c.signed("POST", A, WAV)
            self.assertEqual(self.c.json("POST", A, WAV, headers=h)[0], 200)
            ids.append(h["X-ML-Nonce"])
            time.sleep(1.05)
        self.assertEqual(self.c.json("GET", "/receipt/" + ids[0])[0], 404)
        self.assertEqual(self.c.json("GET", "/receipt/" + ids[2])[0], 200)


class CrashRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.cleanup()

    def test_restart_marks_running_unknown_and_keeps_paths(self):
        env = self.env
        env.write_control(analyze={"sleep": 30, "stdout": json.dumps(ANALYZE_OUT)})
        srv = SubprocessServer(env)
        c = Client(env, srv.port)
        h = c.signed("POST", A, WAV)
        rid = h["X-ML-Nonce"]
        t = threading.Thread(target=lambda: c.send("POST", A, WAV, headers=h, timeout=40), daemon=True)
        t.start()
        end = time.time() + 10
        while not env.seen_log() and time.time() < end:
            time.sleep(0.05)
        self.assertTrue(env.seen_log(), "fake ml never started")
        srv.kill()  # crash while the job is running; ml analyze survives in its own session
        with open(os.path.join(env.mldir, "pid.log")) as f:
            fake_pid = [json.loads(x)["pid"] for x in f][-1]
        try:
            os.kill(fake_pid, 0)  # the server never killed it
            srv2 = SubprocessServer(env)
            c2 = Client(env, srv2.port)
            st, r = c2.json("GET", "/receipt/" + rid)
            self.assertEqual((st, r["state"]), (200, "unknown"))
            inbox_name = env.argv_log()[-1][1]
            self.assertTrue(os.path.exists(os.path.join(env.inbox, inbox_name)))  # kept
            self.assertEqual(len(env.uploads_entries()), 1)
            ev = [e for e in env.log_events() if e.get("event") == "receipt_unknown"]
            self.assertEqual(ev[-1]["id"], rid)
            self.assertIn(os.path.join(env.inbox, inbox_name), ev[-1]["paths"])
            # unknown never authorizes a retry with the same operation id
            self.assertEqual(c2.json("POST", A, WAV, nonce=rid, time_s=int(time.time()) + 115)[0], 401)
            srv2.terminate()
        finally:
            try:
                os.kill(fake_pid, signal.SIGKILL)  # our own fake process only
            except ProcessLookupError:
                pass

    def test_operation_exists_after_nonce_expiry(self):
        env = self.env
        from helpers import Harness
        h = Harness(env, limits={"skew": 2})
        try:
            c = Client(env, h.port)
            n = "8" * 32
            self.assertEqual(c.json("POST", A, WAV, nonce=n)[0], 200)
            calls = len(env.argv_log())
            time.sleep(3.2)
            st, b = c.json("POST", A, WAV, nonce=n)
            self.assertEqual((st, b["error"]["code"]), (409, "operation_exists"))
            self.assertEqual(len(env.argv_log()), calls)
            self.assertEqual(c.json("GET", "/receipt/" + n)[1]["state"], "done")
        finally:
            h.stop()


if __name__ == "__main__":
    unittest.main()
