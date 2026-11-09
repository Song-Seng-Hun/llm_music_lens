"""The worker's own wall bound (on_alarm) takes a still-running model child down with it when the worker leads its process
group, as the server starts it; run any other way it exits as before. A fake long child; no models."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import REPO

DRIVER = r'''
import os, signal, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import rhythm_worker as rw
child = subprocess.Popen(["/bin/sleep", "60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
with open(sys.argv[2], "w") as f:
    f.write(str(child.pid))
signal.signal(signal.SIGALRM, rw.on_alarm)
signal.alarm(1)
time.sleep(30)
'''


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class AlarmTest(unittest.TestCase):
    def run_driver(self, leader):
        d = tempfile.mkdtemp(prefix="mlalarm-")
        pid_file = os.path.join(d, "child.pid")
        p = subprocess.run([sys.executable, "-I", "-c", DRIVER, REPO, pid_file], capture_output=True, timeout=20,
                           start_new_session=leader)
        with open(pid_file) as f:
            child = int(f.read())
        os.unlink(pid_file)
        os.rmdir(d)
        self.assertEqual(json.loads(p.stderr.decode().splitlines()[-1])["error"]["code"], "timeout")
        return p.returncode, child

    def test_group_leader_takes_its_child(self):
        rc, child = self.run_driver(leader=True)
        self.assertNotEqual(rc, 0)
        for _ in range(50):  # the reparented child is reaped by init once killed
            if not alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(alive(child))

    def test_not_a_leader_exits_alone(self):
        rc, child = self.run_driver(leader=False)
        try:
            self.assertEqual(rc, 1)
            self.assertTrue(alive(child))  # the caller's group (this test runner) is left alone
        finally:
            os.kill(child, 9)


if __name__ == "__main__":
    unittest.main()
