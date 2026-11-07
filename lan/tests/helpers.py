"""Test harness: temp root, throwaway self-signed cert (/usr/bin/openssl), fake ml, raw TLS client."""
import hashlib
import io
import json
import math
import os
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
import server  # noqa: E402
import sign  # noqa: E402

OPENSSL = "/usr/bin/openssl"
PY = "/usr/bin/python3"
RID64 = "ab" * 32

FAKE_ML = r'''#!/usr/bin/python3 -I
import json, os, sys, time
HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "control.json")) as f:
    ctl = json.load(f)
args = sys.argv[1:]
with open(os.path.join(HERE, "argv.log"), "a") as f:
    f.write(json.dumps(args) + "\n")
cmd = args[0] if args else ""
spec = ctl.get(cmd, {})
with open(os.path.join(HERE, "pid.log"), "a") as f:
    f.write(json.dumps({"cmd": cmd, "pid": os.getpid()}) + "\n")
if cmd == "analyze":
    p = os.path.join(ctl["inbox"], args[1])
    info = {"name": args[1], "exists": os.path.isfile(p)}
    if info["exists"]:
        with open(p, "rb") as f:
            info["head"] = f.read(4).decode("latin-1")
        info["size"] = os.path.getsize(p)
    with open(os.path.join(HERE, "seen.log"), "a") as f:
        f.write(json.dumps(info) + "\n")
child = None
if spec.get("child"):  # like the real ml: an inference child in its own session holding the team lock fd
    import fcntl, signal, subprocess
    if spec.get("ignore_int"):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    lfd = os.open(ctl["lock"], os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(600)"], pass_fds=(lfd,),
                             start_new_session=True)
    os.close(lfd)
    with open(os.path.join(HERE, "child.pid"), "w") as f:
        f.write(str(child.pid))
try:
    time.sleep(spec.get("sleep", 0))
finally:  # ml kills its child only here (cancel, timeout, exception such as KeyboardInterrupt)
    if child is not None:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
for line in spec.get("stderr_lines", []):
    sys.stderr.write(line + "\n")
if "stdout" in spec:
    sys.stdout.write(spec["stdout"])
if "stdout_size" in spec:
    sys.stdout.write("x" * spec["stdout_size"])
sys.stdout.flush()
sys.exit(spec.get("exit", 0))
'''

ANALYZE_OUT = {"result_id": RID64, "state": "complete", "asr_status": "complete", "word_count": 11,
               "emotion": {"status": "complete", "window_count": 2}, "warnings": [], "cacheable": True,
               "retry_recommended": False, "cache_hit": False}


def make_wav_bytes(seconds=1.0, rate=16000, channels=1, width=2, freq=440.0):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        n = int(seconds * rate)
        frames = bytearray()
        for i in range(n):
            v = int(8000 * math.sin(2 * math.pi * freq * i / rate))
            sample = struct.pack("<h", v) if width == 2 else bytes([((v >> 8) + 128) & 0xFF])
            frames += sample * channels
        w.writeframes(bytes(frames))
    return buf.getvalue()


def silent_wav_bytes(seconds, rate=16000):
    data = b"\x00\x00" * int(seconds * rate)
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gen_cert(d):
    key, cert = os.path.join(d, "tls.key"), os.path.join(d, "tls.pem")
    subprocess.run([OPENSSL, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-pkeyopt",
                    "ec_param_enc:named_curve", "-days", "2",
                    "-nodes", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                    "-keyout", key, "-out", cert], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.chmod(key, 0o600)
    return cert, key


class Env:
    """A throwaway server environment in a temp dir."""

    def __init__(self, clients=("claude-window", "codex-window")):
        self.dir = tempfile.mkdtemp(prefix="mllan-")
        os.chmod(self.dir, 0o700)
        self.root = os.path.join(self.dir, "AI-Listen")
        self.inbox = os.path.join(self.root, "inbox")
        os.makedirs(self.inbox, 0o700)
        self.tls = os.path.join(self.dir, "tls")
        os.mkdir(self.tls, 0o700)
        self.cert, self.tlskey = gen_cert(self.tls)
        self.keys = {}
        self.keyfiles = {}
        for c in clients:
            k = secrets.token_hex(32)
            p = os.path.join(self.tls, c + ".key")
            with open(p, "w") as f:
                f.write(k + "\n")
            os.chmod(p, 0o600)
            self.keys[c], self.keyfiles[c] = bytes.fromhex(k), p
        self.mldir = os.path.join(self.dir, "fakeml")
        os.mkdir(self.mldir)
        self.ml = os.path.join(self.mldir, "ml")
        with open(self.ml, "w") as f:
            f.write(FAKE_ML)
        os.chmod(self.ml, 0o700)
        self.control = {"inbox": self.inbox, "analyze": {"stdout": json.dumps(ANALYZE_OUT) + "\n"},
                        "status": {"stdout": '{"tool_version":"fake","engine":"x"}\n'},
                        "report": {"stdout": '{"result_id":"%s","state":"complete", "x" : 1.50}\n' % RID64},
                        "timeline": {"stdout": '{"result_id":"%s","layer":"words","words":[]}\n' % RID64}}
        self.write_control()
        self.config_path = os.path.join(self.dir, "api.json")

    def write_control(self, **over):
        self.control.update(over)
        with open(os.path.join(self.mldir, "control.json"), "w") as f:
            json.dump(self.control, f)

    def config(self, limits=None, test_mode=True, **over):
        c = {"bind": ["127.0.0.1"], "port": 0, "allow": "127.0.0.0/8",
             "clients": dict(self.keyfiles), "cert": self.cert, "key": self.tlskey, "ml": self.ml,
             "root": self.root, "inbox": self.inbox, "limit_exec": os.path.join(REPO, "limit_exec.py"),
             "test_mode": test_mode}
        if limits:
            c["limits"] = limits
        c.update(over)
        return c

    def write_config(self, cfg, mode=0o600):
        with open(self.config_path, "w") as f:
            json.dump(cfg, f)
        os.chmod(self.config_path, mode)
        return self.config_path

    def argv_log(self):
        p = os.path.join(self.mldir, "argv.log")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(x) for x in f if x.strip()]

    def seen_log(self):
        p = os.path.join(self.mldir, "seen.log")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(x) for x in f if x.strip()]

    def log_text(self):
        p = os.path.join(self.root, "logs", "api.log")
        with open(p, encoding="utf-8") as f:
            return f.read()

    def log_events(self):
        out = []
        for line in self.log_text().splitlines():
            out.append(json.loads(line))
        return out

    def uploads_entries(self):
        p = os.path.join(self.root, "uploads")
        return sorted(os.listdir(p)) if os.path.isdir(p) else []

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Harness:
    """In-process server bound to 127.0.0.1 (test_mode only)."""

    def __init__(self, env, limits=None, **over):
        self.env = env
        env.write_config(env.config(limits=limits, **over))
        self.cfg = server.load_config(env.config_path)
        self.app = server.App(self.cfg)
        self.listeners = server.start(self.app, ["127.0.0.1"], 0)
        self.port = self.listeners[0].server_address[1]

    def stop(self):
        server.stop(self.listeners)
        self.app.close()


class SubprocessServer:
    def __init__(self, env, limits=None, **over):
        self.ready = os.path.join(env.dir, "ready.json")
        if os.path.exists(self.ready):
            os.unlink(self.ready)
        env.write_config(env.config(limits=limits, test_ready_file=self.ready, **over))
        self.proc = subprocess.Popen([PY, "-I", os.path.join(REPO, "server.py"), "--config", env.config_path],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        end = time.time() + 15
        while not os.path.exists(self.ready):
            if self.proc.poll() is not None or time.time() > end:
                raise RuntimeError("server did not start: " + self.proc.stderr.read().decode(errors="replace"))
            time.sleep(0.05)
        with open(self.ready) as f:
            self.port = json.load(f)["ports"][0]

    def kill(self):
        self.proc.kill()
        self.proc.wait()
        self.proc.stderr.close()

    def terminate(self):
        self.proc.terminate()
        self.proc.wait(10)
        self.proc.stderr.close()


def tls_context(cafile):
    ctx = ssl.create_default_context(cafile=cafile)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def connect(port, cafile, timeout=10):
    raw = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    return tls_context(cafile).wrap_socket(raw, server_hostname="localhost")


def build(method, target, headers, body=b""):
    lines = ["%s %s HTTP/1.1" % (method, target), "Host: localhost"]
    for k, v in headers:
        lines.append("%s: %s" % (k, v))
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body


def read_response(sock, timeout=30):
    sock.settimeout(timeout)
    data = b""
    while True:
        try:
            chunk = sock.recv(65536)
        except (ssl.SSLError, ConnectionResetError, socket.timeout):
            break
        if not chunk:
            break
        data += chunk
    return parse_response(data)


def parse_response(data):
    if not data:
        return None, {}, b""
    while data.startswith(b"HTTP/1.1 100"):
        data = data.split(b"\r\n\r\n", 1)[1]
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = int(lines[0].split()[1])
    hdrs = {}
    for l in lines[1:]:
        k, v = l.split(b":", 1)
        hdrs[k.decode().lower()] = v.strip().decode()
    return status, hdrs, body


class Client:
    def __init__(self, env, port, client="claude-window"):
        self.env, self.port, self.client = env, port, client
        self.key = env.keys[client]

    def signed(self, method, target, body=b"", time_s=None, nonce=None, content_type=None, client=None, key=None):
        h, _ = sign.sign_headers(client or self.client, key or self.key, method, target, body,
                                 content_type=content_type, time_s=time_s, nonce=nonce)
        return h

    def send(self, method, target, body=b"", headers=None, extra=(), drop=(), raw_body=None, timeout=30, **kw):
        h = headers if headers is not None else self.signed(method, target, body, **kw)
        items = [(k, v) for k, v in h.items() if k not in drop] + list(extra)
        s = connect(self.port, self.env.cert)
        try:
            s.sendall(build(method, target, items, body if raw_body is None else raw_body))
            return read_response(s, timeout)
        finally:
            s.close()

    def json(self, method, target, body=b"", **kw):
        st, h, b = self.send(method, target, body, **kw)
        return st, (json.loads(b.decode("utf-8")) if b else None)


class Base(unittest.TestCase):
    limits = None
    over = {}

    def setUp(self):
        self.env = Env()
        self.h = Harness(self.env, limits=self.limits, **self.over)
        self.c = Client(self.env, self.h.port)

    def tearDown(self):
        self.h.stop()
        self.env.cleanup()

    def restart(self, limits=None, **over):
        self.h.stop()
        self.h = Harness(self.env, limits=limits if limits is not None else self.limits, **(over or self.over))
        self.c = Client(self.env, self.h.port)

    def wait_receipt(self, rid, state, client=None, timeout=15):
        c = client or self.c
        end = time.time() + timeout
        while time.time() < end:
            st, b = c.json("GET", "/receipt/" + rid)
            if st == 200 and b["state"] == state:
                return b
            time.sleep(0.1)
        self.fail("receipt %s never reached %s (last %r)" % (rid, state, b))


def sha(b):
    return hashlib.sha256(b).hexdigest()
