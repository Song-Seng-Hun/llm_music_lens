"""Fault-injection regressions for the c4c7266 review (F1-F4): journals fail closed, outcome-unknown after launch,
graceful SIGTERM. Temp files and a fake ml only."""
import json
import os
import plistlib
import shutil
import signal
import tempfile
import threading
import time
import unittest

from helpers import ANALYZE_OUT, REPO, Base, Client, Env, SubprocessServer, server, silent_wav_bytes

A = "/analyze?language=en&emotion_mode=voice"
WAV = silent_wav_bytes(0.5)
NOW = 1793000000
CLIENT = "test-client"
IDS = {k: k * 32 for k in "abcdef"}


def new_store(kind, path):
    if kind == "nonce":
        return server.NonceStore(path, 20000, NOW)
    return server.ReceiptStore(path, 2000, 30, NOW)


def admit(kind, store, rid):
    if kind == "nonce":
        return store.admit(CLIENT, rid, NOW + 120, NOW)
    return store.reserve(CLIENT, rid, rid, NOW)


def contains(kind, store, rid):
    return (CLIENT, rid) in store.live if kind == "nonce" else store.get(CLIENT, rid) is not None


def journal_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def journal_lines(path):
    raw = journal_bytes(path)
    return raw, [json.loads(x) for x in raw.splitlines()]  # raises on any malformed line


class Patch:
    """Swap a module attribute for the duration of a with block."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)


class JournalFaultTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mllan-j-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def _short_write(self, kind):
        p = self.path(kind + ".log")
        store = new_store(kind, p)
        real = os.write

        def partial(fd, data):  # one short write, then the device "fails"
            if fd == store.log.fd:
                real(fd, bytes(data[:8]))
                raise OSError(28, "injected ENOSPC after a partial write")
            return real(fd, data)
        with Patch(server.os, "write", partial):
            self.assertEqual(admit(kind, store, IDS["a"]), "storage")
        self.assertEqual(admit(kind, store, IDS["b"]), "ok")  # repaired (truncated to the good offset) first
        raw = journal_bytes(p)
        store.close()
        again = new_store(kind, p)
        try:
            self.assertTrue(contains(kind, again, IDS["b"]), "acknowledged record lost after restart")
            self.assertFalse(contains(kind, again, IDS["a"]))
            self.assertNotEqual(admit(kind, again, IDS["b"]), "ok")  # never readmitted
            journal_lines(p)
            self.assertEqual(len(raw), store.log.good)
            self.assertFalse(again.torn_tail)
        finally:
            again.close()

    def test_short_write_nonce(self):
        self._short_write("nonce")

    def test_short_write_receipt(self):
        self._short_write("receipt")

    def test_short_write_is_completed(self):
        p = self.path("loop.log")
        store = new_store("nonce", p)
        real = os.write

        def tiny(fd, data):  # many short but successful writes
            return real(fd, bytes(data[:5])) if fd == store.log.fd else real(fd, data)
        with Patch(server.os, "write", tiny):
            self.assertEqual(admit("nonce", store, IDS["a"]), "ok")
        store.close()
        self.assertEqual(len(journal_lines(p)[1]), 1)

    def _poisoned_then_append(self, kind):
        p = self.path(kind + ".log")
        store = new_store(kind, p)
        self.assertEqual(admit(kind, store, IDS["a"]), "ok")
        real_fsync, real_trunc = server.full_fsync, os.ftruncate

        def bad_fsync(fd):
            raise OSError(5, "injected fsync failure")

        def bad_trunc(fd, n):
            raise OSError(5, "injected ftruncate failure")
        with Patch(server, "full_fsync", bad_fsync):
            self.assertEqual(admit(kind, store, IDS["b"]), "storage")
        with Patch(server.os, "ftruncate", bad_trunc):  # repair fails: every later append fails closed
            self.assertEqual(admit(kind, store, IDS["c"]), "storage")
            self.assertEqual(admit(kind, store, IDS["d"]), "storage")
        self.assertEqual(store.log.poisoned, "append")
        self.assertEqual(admit(kind, store, IDS["e"]), "ok")  # repair succeeds, poison cleared
        self.assertIsNone(store.log.poisoned)
        raw, rows = journal_lines(p)
        self.assertEqual(len(raw), store.log.good)
        store.close()
        again = new_store(kind, p)
        try:
            for k, want in (("a", True), ("b", False), ("c", False), ("d", False), ("e", True)):
                self.assertEqual(contains(kind, again, IDS[k]), want, k)
        finally:
            again.close()
        self.assertIs(server.full_fsync, real_fsync)
        self.assertIs(os.ftruncate, real_trunc)

    def test_poisoned_tail_nonce(self):
        self._poisoned_then_append("nonce")

    def test_poisoned_tail_receipt(self):
        self._poisoned_then_append("receipt")

    def test_receipt_update_failure_poisons(self):
        p = self.path("r.log")
        store = new_store("receipt", p)
        store.reserve(CLIENT, IDS["a"], "op", NOW)
        with Patch(server, "full_fsync", lambda fd: (_ for _ in ()).throw(OSError(5, "x"))):
            with self.assertRaises(OSError):
                store.update(CLIENT, IDS["a"], NOW, state="done", http_status=200)
        self.assertEqual(store.get(CLIENT, IDS["a"])["state"], "running")  # memory keeps the durable record
        store.update(CLIENT, IDS["a"], NOW, state="done", http_status=200)
        store.close()
        again = new_store("receipt", p)
        self.assertEqual(again.get(CLIENT, IDS["a"])["state"], "done")
        self.assertEqual(len(journal_lines(p)[1]), 1)
        again.close()

    def test_startup_truncates_torn_tail(self):
        for kind in ("nonce", "receipt"):
            p = self.path(kind + "-torn.log")
            store = new_store(kind, p)
            admit(kind, store, IDS["a"])
            store.close()
            with open(p, "ab") as f:
                f.write(b'{"c":"te')  # crash mid-append
            calls = []
            real_trunc = os.ftruncate
            with Patch(server.os, "ftruncate", lambda fd, n: (calls.append(n), real_trunc(fd, n))[1]):
                again = new_store(kind, p)
            try:
                self.assertTrue(again.torn_tail)
                self.assertEqual(len(calls), 1)
                self.assertTrue(contains(kind, again, IDS["a"]))
                self.assertEqual(admit(kind, again, IDS["b"]), "ok")
                journal_lines(p)  # every line parses: nothing appended onto the torn tail
            finally:
                again.close()

    def test_startup_refuses_interior_corruption(self):
        p = self.path("interior.log")
        with open(p, "wb") as f:
            f.write(b'{"c":"te{"c":"test-client","n":"%s","e":%d}\n' % (IDS["b"].encode(), NOW + 120))
            f.write(b'{"c":"test-client","n":"%s","e":%d}\n' % (IDS["c"].encode(), NOW + 120))
        before = journal_bytes(p)
        with self.assertRaises(server.JournalCorrupt):
            new_store("nonce", p)
        self.assertEqual(journal_bytes(p), before)  # left for the owner

    def _dir_fsync_after_replace(self, kind):
        p = self.path(kind + "-compact.log")
        store = new_store(kind, p)
        self.assertEqual(admit(kind, store, IDS["a"]), "ok")
        n = len(store.live) if kind == "nonce" else len(store.items)
        store.lines = 2 * n + 1000  # reachable compaction threshold, without 1000 unrelated fsyncs
        failures = []

        def fail(d):
            failures.append(d)
            raise OSError(5, "injected directory fsync failure after replace")
        with Patch(server, "fsync_dir", fail):
            if kind == "nonce":
                self.assertEqual(admit(kind, store, IDS["c"]), "storage")
            else:
                with self.assertRaises(OSError):
                    store.update(CLIENT, IDS["a"], NOW, state="done", http_status=200)
            self.assertEqual(admit(kind, store, IDS["d"]), "storage")  # still failing: no stale-fd acknowledgement
            self.assertEqual(store.log.poisoned, "rebind")
        self.assertEqual(len(failures), 2)
        self.assertEqual(admit(kind, store, IDS["b"]), "ok")  # rebuilt + rebound first
        self.assertEqual(os.fstat(store.log.fd).st_ino, os.stat(p).st_ino)
        self.assertEqual(os.path.getsize(p), store.log.good)
        store.close()
        again = new_store(kind, p)
        try:
            self.assertTrue(contains(kind, again, IDS["a"]))
            self.assertTrue(contains(kind, again, IDS["b"]), "acknowledged record lost after restart")
            self.assertFalse(contains(kind, again, IDS["d"]))
            self.assertNotEqual(admit(kind, again, IDS["b"]), "ok")
            if kind == "receipt":
                self.assertEqual(again.get(CLIENT, IDS["a"])["state"], "unknown")  # done was never acknowledged
        finally:
            again.close()

    def test_dir_fsync_failure_after_replace_nonce(self):
        self._dir_fsync_after_replace("nonce")

    def test_dir_fsync_failure_after_replace_receipt(self):
        self._dir_fsync_after_replace("receipt")


class OutcomeUnknownTest(Base):
    def _fail_final(self):
        app = self.h.app
        real = app.receipts.update

        def update(client, rid, now, **fields):
            if "state" in fields and fields["state"] in ("done", "failed"):
                raise OSError(5, "injected final receipt failure")
            return real(client, rid, now, **fields)
        app.receipts.update = update
        return real

    def test_final_receipt_failure_after_launch(self):
        self._fail_final()
        h = self.c.signed("POST", A, WAV)
        st, b = self.c.json("POST", A, WAV, headers=h)
        self.assertEqual(self.env.argv_log()[0][0], "analyze")  # ml really ran
        self.assertEqual((st, b["error"]["code"]), (503, "outcome_unknown"))
        self.assertEqual(b["request_id"], h["X-ML-Nonce"])
        self.assertNotIn("nothing launched", b["error"]["message"])
        self.assertIn("may have run", b["error"]["message"])
        st, r = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])
        self.assertEqual((st, r["state"], r["error_code"]), (200, "unknown", "outcome_unknown"))
        self.restart()
        self.assertEqual(self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]["state"], "unknown")

    def test_final_receipt_failure_before_launch_keeps_prelaunch_wording(self):
        self._fail_final()
        bad =b"RIFF\x24\x00\x00\x00WAVEjunk" + b"\x00" * 40  # sniffs as wav, fails the strict check in the job
        st, b = self.c.json("POST", A, bad)
        self.assertEqual(self.env.argv_log(), [])
        self.assertEqual((st, b["error"]["code"]), (503, "storage_error"))
        self.assertIn("nothing launched", b["error"]["message"])

    def test_stopping_refuses_launch(self):
        with self.h.app.job_lock:
            self.h.app.stopping = True
        h = self.c.signed("POST", A, WAV)
        st, b = self.c.json("POST", A, WAV, headers=h)
        self.assertEqual((st, b["error"]["code"]), (503, "shutting_down"))
        self.assertEqual(self.env.argv_log(), [])
        self.assertEqual(self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]["state"], "failed")
        self.h.app.stopping = False


class GracefulStopTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.pids = []

    def tearDown(self):
        for pid in self.pids:  # our own fake ml processes only
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.env.cleanup()

    def _start_job(self, sleep, grace):
        env = self.env
        env.write_control(analyze={"sleep": sleep, "stdout": json.dumps(ANALYZE_OUT)})
        limits = {"stop_grace": grace} if "stop_grace" in server.DEFAULT_LIMITS else None
        srv = SubprocessServer(env, limits=limits)
        c = Client(env, srv.port)
        h = c.signed("POST", A, WAV)
        out = {}
        t = threading.Thread(target=lambda: out.update(r=c.send("POST", A, WAV, headers=h, timeout=40)), daemon=True)
        t.start()
        end = time.time() + 10
        while not env.seen_log() and time.time() < end:
            time.sleep(0.02)
        self.assertTrue(env.seen_log(), "fake ml never started")
        with open(os.path.join(env.mldir, "pid.log")) as f:
            pid = [json.loads(x)["pid"] for x in f][-1]
        self.pids.append(pid)
        return srv, h["X-ML-Nonce"], t, out, pid

    def _last_receipt(self, rid):
        with open(os.path.join(self.env.root, "api", "receipts.log")) as f:
            rows = [json.loads(x) for x in f if x.strip()]
        return [r for r in rows if r["request_id"] == rid][-1]

    def _alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def test_sigterm_drains_running_job(self):
        srv, rid, t, out, pid = self._start_job(sleep=2.0, grace=20)
        t0 = time.monotonic()
        srv.proc.send_signal(signal.SIGTERM)
        srv.proc.wait(30)
        elapsed = time.monotonic() - t0
        srv.proc.stderr.close()
        t.join(5)
        self.assertEqual(srv.proc.returncode, 0)
        self.assertGreater(elapsed, 1.0)  # waited for the job instead of exiting at once
        self.assertEqual(self._last_receipt(rid)["state"], "done")
        self.assertEqual(out["r"][0], 200)  # the waiting client still got its answer
        time.sleep(0.2)
        self.assertFalse(self._alive(pid))
        self.assertEqual(self.env.uploads_entries(), [])

    def test_sigterm_grace_expiry_marks_unknown(self):
        srv, rid, t, out, pid = self._start_job(sleep=8.0, grace=0.5)
        t0 = time.monotonic()
        srv.proc.send_signal(signal.SIGTERM)
        srv.proc.wait(30)
        elapsed = time.monotonic() - t0
        srv.proc.stderr.close()
        self.assertEqual(srv.proc.returncode, 0)
        self.assertLess(elapsed, 6.0)
        last = self._last_receipt(rid)
        self.assertEqual((last["state"], last["error_code"]), ("unknown", "interrupted_by_stop"))
        self.assertTrue(self._alive(pid))  # no cancellation; leftovers kept like a crash
        self.assertEqual(len(self.env.uploads_entries()), 1)
        ev = [e for e in self.env.log_events() if e.get("event") == "receipt_unknown"]
        self.assertEqual((ev[-1]["id"], ev[-1]["reason"]), (rid, "stop"))


class LaunchdTemplateTest(unittest.TestCase):
    def test_exit_timeout_covers_stop_grace(self):
        with open(os.path.join(REPO, "examples", "com.music-lens.lan-api.plist.template"), "rb") as f:
            pl = plistlib.load(f)
        self.assertGreaterEqual(pl["ExitTimeOut"], server.DEFAULT_LIMITS["stop_grace"] + 20)


if __name__ == "__main__":
    unittest.main()
