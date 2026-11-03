"""Cheap real process tests; no models, network, live lock path or sleeps over 2 s."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('heavy_job', HERE/'heavy_job.py')
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
BOOT = "import importlib.util,sys,os,time,json; s=importlib.util.spec_from_file_location('h',sys.argv[1]); h=importlib.util.module_from_spec(s); s.loader.exec_module(h); path=sys.argv[2]; "
PROBE = BOOT+"\ntry:\n with h.from_env(path) as lease: print(json.dumps({'status':'acquired'}),flush=True)\nexcept h.HeavyJobError as e: print(json.dumps({'status':e.code}),flush=True)\n"


def cmd(code, path):
    return [sys.executable, '-I', '-c', code, str(HERE/'heavy_job.py'), str(path)]


def probe(path, **kw):
    p = subprocess.run(cmd(PROBE,path), capture_output=True, text=True, timeout=3, **kw)
    if p.returncode:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)['status']


class SharedHeavyJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name)/'compute.lock'
    def tearDown(self):
        self.tmp.cleanup()

    def test_ear_eye_rhythm_are_mutually_exclusive_and_fail_fast(self):
        with h.Lease(self.path):
            for route in ('ear_analyze','eye_look','ear_rhythm'):
                with self.subTest(route=route): self.assertEqual(probe(self.path),'busy')
        self.assertEqual(probe(self.path),'acquired')

    def test_child_reference_survives_parent_context_close(self):
        with h.Lease(self.path) as lease:
            p = subprocess.Popen(cmd(BOOT+"print('ready',flush=True); time.sleep(1.0)",self.path),
                                 stdout=subprocess.PIPE, text=True, pass_fds=lease.pass_fds())
            self.assertEqual(p.stdout.readline().strip(),'ready')
        try:
            self.assertEqual(probe(self.path),'busy')
            self.assertEqual(p.wait(timeout=3),0)
            self.assertEqual(probe(self.path),'acquired')
        finally:
            if p.poll() is None:p.kill();p.wait()
            p.stdout.close()

    def test_borrowed_child_lease_does_not_unlock_ancestor(self):
        with h.Lease(self.path) as lease:
            self.assertEqual(probe(self.path,env=lease.child_env(os.environ),pass_fds=lease.pass_fds()),'acquired')
            self.assertEqual(probe(self.path),'busy')
        self.assertEqual(probe(self.path),'acquired')

    def test_sigkill_parent_keeps_exclusion_until_model_child_exits(self):
        code=BOOT+"\nwith h.Lease(path) as lease:\n p=__import__('subprocess').Popen([sys.executable,'-I','-c',\"import time;time.sleep(1.5);print('child_done',flush=True)\"],pass_fds=lease.pass_fds())\n print('parent_ready',flush=True)\n time.sleep(2)\n"
        p=subprocess.Popen(cmd(code,self.path),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(),'parent_ready')
            p.kill();p.wait(timeout=3)
            self.assertEqual(probe(self.path),'busy')
            out,err=p.communicate(timeout=3)
            self.assertIn('child_done',out);self.assertEqual(err,'')
            self.assertEqual(probe(self.path),'acquired')
        finally:
            if p.poll() is None:p.kill();p.wait()
            p.stdout.close();p.stderr.close()

    def test_inherited_marker_cannot_substitute_an_unrelated_file(self):
        self.path.touch()
        other=Path(self.tmp.name)/'other';other.touch()
        fd=os.open(other,os.O_RDWR)
        try:
            env=dict(os.environ,**{h.FD_ENV:str(fd),h.PATH_ENV:str(self.path)})
            self.assertEqual(probe(self.path,env=env,pass_fds=(fd,)),'configuration')
        finally:os.close(fd)
        self.assertEqual(probe(self.path),'acquired')


if __name__ == '__main__':unittest.main()
