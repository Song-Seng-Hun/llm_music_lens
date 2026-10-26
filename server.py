#!/usr/bin/python3 -I
"""Music Lens LAN API v0.6 server (MUSIC-LENS-LAN-API-1026).

Stdlib only, runs on /usr/bin/python3 (3.9). Calls only the team wrapper `ml` by argv (no shell).
The listener loads no model; models load only inside each `ml analyze` job's subprocesses.

Usage: server.py [--config PATH]   (default ~/.music-lens/api.json, mode 0600)
"""
import argparse
import fcntl
import hashlib
import hmac
import http
import ipaddress
import json
import logging
import logging.handlers
import os
import re
import secrets
import shutil
import signal
import socket
import ssl
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:  # -I drops the script directory from sys.path; this directory is the trusted install dir
    sys.path.insert(0, HERE)
import sign  # noqa: E402

HOME = os.path.expanduser("~")
MiB = 1024 * 1024
DEFAULT_LIMITS = {
    "skew": 120, "nonce_cap": 20000, "receipt_cap": 2000, "receipt_days": 30,
    "max_conns": 8, "header_bytes": 16 * 1024, "header_count": 40, "target_bytes": 2048,
    "header_deadline": 10.0, "upload_deadline": 60.0, "convert_timeout": 120.0, "handler_total": 800.0,
    "read_timeout": 30.0, "max_body": 64 * MiB, "disk_floor": 2 * 1024 * MiB, "ml_stdout": 8 * MiB,
    "response_max": 8 * MiB, "stderr_line": 4096, "convert_output": 4096, "log_bytes": 5 * MiB, "log_files": 3,
    "send_timeout": 30.0,
}
CONFIG_KEYS = {"bind", "port", "allow", "clients", "cert", "key", "ml", "root", "inbox", "limit_exec",
               "launchd_logs", "test_mode", "limits", "test_ready_file"}
PYTHON = "/usr/bin/python3"
IPCONFIG = "/usr/sbin/ipconfig"
CHILD_ENV_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

# code -> (http status, fixed message)
ERRORS = {
    "bad_request": (400, "Malformed request"),
    "truncated_body": (400, "Request body ended early"),
    "invalid_audio": (400, "Audio is not an accepted WAV, MP3 or M4A within limits"),
    "invalid_path": (400, "Invalid audio path"),
    "invalid_id": (400, "Invalid result id"),
    "invalid_argument": (400, "Invalid argument"),
    "unauthorized": (401, None),
    "not_found": (404, "Not found"),
    "method_not_allowed": (405, "Method not allowed"),
    "request_timeout": (408, "Upload exceeded its deadline"),
    "busy": (409, "Another analyze job is in progress"),
    "operation_exists": (409, "This operation id was already used; read its receipt"),
    "payload_too_large": (413, "Body exceeds 64 MiB"),
    "uri_too_long": (414, "Request target too long"),
    "expectation_failed": (417, "Only Expect: 100-continue is supported"),
    "header_fields_too_large": (431, "Request header too large"),
    "internal_error": (500, "Internal error"),
    "server_misconfigured": (500, "Server is misconfigured"),
    "invalid_output": (500, "Analyzer returned invalid output"),
    "invalid_result": (500, "Stored result is invalid"),
    "empty_output": (500, "Analyzer returned no output"),
    "cancelled": (500, "Analysis was cancelled"),
    "nonce_store_full": (503, "Nonce store full; retry later"),
    "insufficient_storage": (503, "Not enough free disk space"),
    "receipt_store_full": (503, "Receipt store full"),
    "storage_error": (503, "Durable storage failed; nothing launched"),
    "timeout": (504, "Operation timed out"),
}
ML_400 = ("invalid_audio", "invalid_path", "invalid_id", "invalid_argument")
ML_MISCONFIG = ("pin_mismatch", "configuration", "dependency_missing")
ML_500_KEEP = ("invalid_output", "invalid_result", "empty_output", "cancelled", "internal_error")

RE_IFACE = re.compile(r"\A[a-z]{1,8}[0-9]{1,3}\Z")
RE_TIME = re.compile(r"\A(0|[1-9][0-9]{0,11})\Z")
RE_HEX32 = re.compile(r"\A[0-9a-f]{32}\Z")
RE_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
RE_CL = re.compile(r"\A(0|[1-9][0-9]{0,19})\Z")
RE_TOKEN = re.compile(rb"\A[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
RE_REQLINE = re.compile(rb"\A([A-Z]{1,16}) (\S{1,8192}) HTTP/1\.1\Z")
RE_PCT = re.compile(r"\A(?:[^%]|%[0-9A-Fa-f]{2})*\Z")
RE_NUM = re.compile(r"\A-?(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?\Z")
RE_LIMIT = re.compile(r"\A[1-9][0-9]{0,2}\Z")
RE_CURSOR = re.compile(r"\A(0|[1-9][0-9]{0,5})\Z")
ROUTE_REPORT = re.compile(r"\A/report/([0-9a-f]{64})\Z")
ROUTE_TIMELINE = re.compile(r"\A/timeline/([0-9a-f]{64})\Z")
ROUTE_RECEIPT = re.compile(r"\A/receipt/([0-9a-f]{32})\Z")
M4A_BRANDS = (b"M4A ", b"M4B ", b"mp42", b"mp41", b"isom", b"iso2")
PCM_GUID = b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
UPLOAD_NAMES = ("upload.part", "upload.wav", "upload.mp3", "upload.m4a", "converted.wav")


class ConfigError(Exception):
    pass


class HttpError(Exception):
    def __init__(self, code, close_silently=False):
        super().__init__(code)
        self.code = code
        self.close_silently = close_silently


# ---------------------------------------------------------------- durable files

def full_fsync(fd):
    try:
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    except (AttributeError, OSError):
        os.fsync(fd)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_lines(path, lines):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        data = "".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines).encode()
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        full_fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    fsync_dir(os.path.dirname(path))


def read_json_lines(path):
    out = []
    try:
        with open(path, "rb") as f:
            for raw in f:
                try:
                    v = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue  # torn last line after a crash
                if isinstance(v, dict):
                    out.append(v)
    except FileNotFoundError:
        pass
    return out


class AppendLog:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)

    def append(self, obj):
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        n = os.write(self.fd, data)
        if n != len(data):
            raise OSError("short write")
        full_fsync(self.fd)

    def reopen(self):
        os.close(self.fd)
        self.fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


class NonceStore:
    """Durable (client, nonce, expiry) registry; live entries are never evicted."""

    def __init__(self, path, cap, now):
        self.path, self.cap, self.lock = path, cap, threading.Lock()
        self.live = {}
        for e in read_json_lines(path):
            c, n, x = e.get("c"), e.get("n"), e.get("e")
            if isinstance(c, str) and isinstance(n, str) and isinstance(x, int) and x >= now:
                self.live[(c, n)] = max(x, self.live.get((c, n), 0))
        atomic_write_lines(path, [{"c": c, "n": n, "e": x} for (c, n), x in self.live.items()])
        self.lines = len(self.live)
        self.log = AppendLog(path)

    def _purge(self, now):
        for k in [k for k, x in self.live.items() if x < now]:
            del self.live[k]

    def admit(self, client, nonce, expiry, now):
        with self.lock:
            x = self.live.get((client, nonce))
            if x is not None and x >= now:
                return "replay"
            if len(self.live) >= self.cap:
                self._purge(now)
                if len(self.live) >= self.cap:
                    return "full"
            try:
                self.log.append({"c": client, "n": nonce, "e": expiry})
            except OSError:
                return "storage"
            self.live[(client, nonce)] = expiry
            self.lines += 1
            if self.lines > 2 * len(self.live) + 1000:
                self._purge(now)
                try:
                    atomic_write_lines(self.path, [{"c": c, "n": n, "e": e} for (c, n), e in self.live.items()])
                    self.log.reopen()
                    self.lines = len(self.live)
                except OSError:
                    pass
            return "ok"

    def close(self):
        self.log.close()


class ReceiptStore:
    """Durable receipts keyed by operation identity (client, request_id = POST nonce)."""
    FIELDS = ("client", "request_id", "op", "state", "http_status", "result_id", "error_code", "upload_sha256",
              "created", "updated")

    def __init__(self, path, cap, days, now):
        self.path, self.cap, self.max_age, self.lock = path, cap, days * 86400, threading.Lock()
        self.items = {}
        for e in read_json_lines(path):
            if isinstance(e.get("client"), str) and isinstance(e.get("request_id"), str):
                self.items[(e["client"], e["request_id"])] = {k: e.get(k) for k in self.FIELDS}
        self.recovered = []
        for r in self.items.values():
            if r["state"] == "running":
                r["state"], r["updated"] = "unknown", int(now)
                self.recovered.append(dict(r))
        self._prune(now)
        atomic_write_lines(path, list(self.items.values()))
        self.lines = len(self.items)
        self.log = AppendLog(path)

    def _prune(self, now, need_room=False):
        final = sorted((r for r in self.items.values() if r["state"] in ("done", "failed")),
                       key=lambda r: r.get("updated") or 0)
        for r in final:
            old = (now - (r.get("updated") or 0)) > self.max_age
            over = len(self.items) > self.cap or (need_room and len(self.items) >= self.cap)
            if old or over:
                del self.items[(r["client"], r["request_id"])]

    def get(self, client, rid):
        with self.lock:
            r = self.items.get((client, rid))
            return dict(r) if r else None

    def reserve(self, client, rid, op, now):
        with self.lock:
            if (client, rid) in self.items:
                return "exists"
            self._prune(now, need_room=True)
            if len(self.items) >= self.cap:
                return "full"
            r = {"client": client, "request_id": rid, "op": op, "state": "running", "http_status": None,
                 "result_id": None, "error_code": None, "upload_sha256": None, "created": int(now), "updated": int(now)}
            try:
                self.log.append(r)
            except OSError:
                return "storage"
            self.items[(client, rid)] = r
            self.lines += 1
            return "ok"

    def update(self, client, rid, now, **fields):
        with self.lock:
            r = dict(self.items[(client, rid)])
            r.update(fields)
            r["updated"] = int(now)
            self.log.append(r)  # OSError propagates to the caller
            self.items[(client, rid)] = r
            self.lines += 1
            if self.lines > 2 * len(self.items) + 1000:
                try:
                    atomic_write_lines(self.path, list(self.items.values()))
                    self.log.reopen()
                    self.lines = len(self.items)
                except OSError:
                    pass
            return dict(r)

    def close(self):
        self.log.close()


# ---------------------------------------------------------------- config

def _no_dup_keys(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ConfigError("duplicate key in config: " + k)
        out[k] = v
    return out


def _private_file(path, what):
    try:
        st = os.stat(path)
    except OSError:
        raise ConfigError(what + " not readable")
    if not stat.S_ISREG(st.st_mode):
        raise ConfigError(what + " is not a regular file")
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ConfigError(what + " must be owned by this user with mode 0600")


def _abs(v, what):
    if not isinstance(v, str) or not os.path.isabs(v) or "\x00" in v:
        raise ConfigError(what + " must be an absolute path")
    return os.path.normpath(v)


def load_config(path):
    _private_file(path, "config")
    try:
        with open(path, "rb") as f:
            raw = json.loads(f.read().decode("utf-8"), object_pairs_hook=_no_dup_keys)
    except ConfigError:
        raise
    except (ValueError, OSError) as e:
        raise ConfigError("config is not valid JSON: " + type(e).__name__)
    if not isinstance(raw, dict):
        raise ConfigError("config must be an object")
    unknown = set(raw) - CONFIG_KEYS
    if unknown:
        raise ConfigError("unknown config keys: " + ", ".join(sorted(unknown)))
    test_mode = raw.get("test_mode", False)
    if test_mode not in (True, False):
        raise ConfigError("test_mode must be boolean")
    cfg = {"test_mode": test_mode}

    bind = raw.get("bind")
    if not isinstance(bind, list) or not bind or len(bind) > 4 or not all(isinstance(b, str) for b in bind):
        raise ConfigError("bind must be a non-empty list of interface names")
    for b in bind:
        if RE_IFACE.match(b):
            continue
        if test_mode:
            try:
                ip = ipaddress.ip_address(b)
            except ValueError:
                raise ConfigError("bind entry invalid: " + b)
            if ip.version != 4 or ip.is_unspecified:
                raise ConfigError("bind entry must be IPv4 and not 0.0.0.0")
            continue
        raise ConfigError("bind entries must be interface names (en0, en1)")
    cfg["bind"] = list(bind)

    port = raw.get("port", 8765)
    if not isinstance(port, int) or isinstance(port, bool) or not ((0 if test_mode else 1024) <= port <= 65535):
        raise ConfigError("port out of range")
    cfg["port"] = port

    try:
        allow = ipaddress.ip_network(raw.get("allow"), strict=True)
    except (TypeError, ValueError):
        raise ConfigError("allow must be an IPv4 network like 172.30.1.0/24")
    if allow.version != 4:
        raise ConfigError("allow must be IPv4")
    if not test_mode:
        if (allow.prefixlen < 24 or not allow.is_private or allow.overlaps(ipaddress.ip_network("127.0.0.0/8"))
                or allow.overlaps(ipaddress.ip_network("0.0.0.0/8"))):
            raise ConfigError("allow must be a private LAN /24 or narrower, never loopback or 0.0.0.0")
    cfg["allow"] = allow

    clients = raw.get("clients")
    if not isinstance(clients, dict) or not clients or len(clients) > 16:
        raise ConfigError("clients must map client id to key path")
    keys = {}
    for cid, kpath in clients.items():
        if not sign.CLIENT_RE.match(cid):
            raise ConfigError("client id invalid")
        kpath = _abs(kpath, "client key path")
        _private_file(kpath, "client key file")
        try:
            with open(kpath, "rb") as f:
                text = f.read(200).decode("ascii")
            keys[cid] = sign.parse_key_text(text)
        except (UnicodeDecodeError, sign.KeyFormatError, OSError):
            raise ConfigError("client key for " + cid + " must be 64 lowercase hex + at most one LF")
    if len(set(keys.values())) != len(keys):
        raise ConfigError("each client needs its own key")
    cfg["clients"] = keys

    cfg["cert"] = _abs(raw.get("cert"), "cert")
    cfg["key"] = _abs(raw.get("key"), "key")
    if not os.path.isfile(cfg["cert"]):
        raise ConfigError("cert missing")
    _private_file(cfg["key"], "TLS key")

    cfg["ml"] = _abs(raw.get("ml", HOME + "/tools/music-lens/ml"), "ml")
    if not (os.path.isfile(cfg["ml"]) and os.access(cfg["ml"], os.X_OK)):
        raise ConfigError("ml wrapper missing or not executable")
    cfg["root"] = _abs(raw.get("root", HOME + "/AI-Listen"), "root")
    cfg["inbox"] = _abs(raw.get("inbox", cfg["root"] + "/inbox"), "inbox")
    if not os.path.isdir(cfg["root"]) or not os.path.isdir(cfg["inbox"]):
        raise ConfigError("root and inbox directories must exist")
    cfg["limit_exec"] = _abs(raw.get("limit_exec", os.path.join(HERE, "limit_exec.py")), "limit_exec")
    if not os.path.isfile(cfg["limit_exec"]):
        raise ConfigError("limit_exec.py missing")
    ll = raw.get("launchd_logs", [])
    if not isinstance(ll, list):
        raise ConfigError("launchd_logs must be a list")
    cfg["launchd_logs"] = [_abs(p, "launchd log") for p in ll]

    limits = dict(DEFAULT_LIMITS)
    over = raw.get("limits", {})
    if over:
        if not test_mode:
            raise ConfigError("limits overrides are test-only")
        if not isinstance(over, dict) or set(over) - set(DEFAULT_LIMITS):
            raise ConfigError("unknown limits")
        for k, v in over.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                raise ConfigError("limit " + k + " must be positive")
            limits[k] = v
    cfg["limits"] = limits
    rf = raw.get("test_ready_file")
    if rf is not None and not test_mode:
        raise ConfigError("test_ready_file is test-only")
    cfg["test_ready_file"] = _abs(rf, "test_ready_file") if rf else None
    return cfg


def iface_addr(name):
    try:
        p = subprocess.run([IPCONFIG, "getifaddr", name], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=5, env={"PATH": CHILD_ENV_PATH})
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    try:
        ip = ipaddress.ip_address(p.stdout.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError):
        return None
    return ip if ip.version == 4 else None


def discover_addresses(cfg, log, lookup=iface_addr):
    """One address per distinct current IPv4 of the bind interfaces, inside allow. Never 0.0.0.0."""
    out = []
    for b in cfg["bind"]:
        ip = lookup(b) if RE_IFACE.match(b) else ipaddress.ip_address(b)
        if ip is None:
            log.event("bind_skip", iface=b, reason="no_address")
            continue
        if ip.is_unspecified or ip not in cfg["allow"] or (ip.is_loopback and not cfg["test_mode"]):
            log.event("bind_skip", iface=b, reason="outside_allow")
            continue
        if str(ip) not in out:
            out.append(str(ip))
    return out


def rotate_file(path, max_bytes, count):
    try:
        if os.path.getsize(path) <= max_bytes:
            return
    except OSError:
        return
    for i in range(count - 1, 0, -1):
        src = path if i == 1 else "%s.%d" % (path, i - 1)
        if os.path.exists(src):
            os.replace(src, "%s.%d" % (path, i))


# ---------------------------------------------------------------- logging

class JsonLog:
    def __init__(self, path, max_bytes, files):
        self.logger = logging.getLogger("music-lens-lan-api." + path)
        self.logger.propagate = False
        self.logger.setLevel(logging.INFO)
        if not os.path.exists(path):
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        self.handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=max(0, files - 1))
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(self.handler)

    def write(self, obj):
        obj = dict(obj, t=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self.logger.info(json.dumps(obj, ensure_ascii=True, separators=(",", ":")))

    def event(self, name, **kw):
        self.write(dict(kw, event=name))

    def close(self):
        self.logger.removeHandler(self.handler)
        self.handler.close()


# ---------------------------------------------------------------- audio helpers

def sniff(head):
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[0:3] == b"ID3":
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE6) == 0xE2:  # frame sync + layer III
        return "mp3"
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in M4A_BRANDS:
        return "m4a"
    return None


def strict_wav_check(path, max_bytes=64 * MiB):
    """RIFF/WAVE, PCM (plain or EXTENSIBLE-PCM), mono, 16 kHz, s16, chunks tile the file exactly, 0 < duration <= 600 s."""
    size = os.path.getsize(path)
    if size < 44 or size > max_bytes:
        return False
    with open(path, "rb") as f:
        hdr = f.read(12)
        if hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE" or struct.unpack("<I", hdr[4:8])[0] + 8 != size:
            return False
        pos, fmt, data, chunks = 12, None, None, 0
        while pos < size:
            chunks += 1
            if chunks > 64 or pos + 8 > size:
                return False
            f.seek(pos)
            cid, csz = struct.unpack("<4sI", f.read(8))
            end = pos + 8 + csz
            if end > size:
                return False
            if cid == b"fmt ":
                if fmt is not None or data is not None or csz < 16:
                    return False
                fmt = struct.unpack("<HHIIHH", f.read(16))
                if fmt[0] == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE (afconvert writes it): accept only plain 16-bit PCM
                    if csz < 40:
                        return False
                    cb, valid, mask, guid = struct.unpack("<HHI16s", f.read(24))
                    if cb != 22 or valid != 16 or mask not in (0, 4) or guid != PCM_GUID:
                        return False
                    fmt = (1,) + fmt[1:]
            elif cid == b"data":
                if data is not None or fmt is None:
                    return False
                data = csz
            pos = end + (csz & 1)
        if pos != size or fmt is None or data is None:
            return False
        if fmt != (1, 1, 16000, 32000, 2, 16) or data % 2:
            return False
        return 0 < data / 32000.0 <= 600


# ---------------------------------------------------------------- query parsing

def pct_decode(s):
    if not RE_PCT.match(s):
        raise HttpError("bad_request")
    try:
        return urllib.parse.unquote_to_bytes(s).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise HttpError("bad_request")


def parse_query(q, allowed):
    out = {}
    if q == "":
        return out
    for part in q.split("&"):
        if "=" not in part:
            raise HttpError("bad_request")
        k, v = part.split("=", 1)
        k, v = pct_decode(k), pct_decode(v)
        if k not in allowed or k in out:
            raise HttpError("bad_request")
        out[k] = v
    return out


def valid_name(name):
    if len(name.encode("utf-8")) > 120:
        return False
    return not any(unicodedata.category(c) == "Cc" for c in name)


def error_body(code, **extra):
    status, message = ERRORS[code]
    if code == "unauthorized":
        return status, {"error": {"code": code}}  # auth failures carry nothing else
    body = {"error": {"code": code, "message": message}}
    body.update(extra)
    return status, body


def map_ml_error(code):
    if code == "busy":
        return "busy"
    if code in ML_400:
        return code
    if code == "not_found":
        return "not_found"
    if code == "timeout":
        return "timeout"
    if code in ML_MISCONFIG:
        return "server_misconfigured"
    if code in ML_500_KEEP:
        return code
    return "internal_error"


def last_stderr_code(f, max_line):
    """Parse only the last non-empty stderr line (<= max_line bytes) as {"error":{"code"}}."""
    size = os.fstat(f.fileno()).st_size
    span = min(size, max_line * 2 + 2)
    f.seek(size - span)
    tail = f.read(span).rstrip(b"\r\n\t ")
    if not tail:
        return "internal_error"
    nl = tail.rfind(b"\n")
    if nl < 0 and span < size:
        return "internal_error"  # the last line is longer than we read
    line = tail[nl + 1:]
    if len(line) > max_line:
        return "internal_error"
    try:
        v = json.loads(line.decode("utf-8"))
        code = v["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return "internal_error"
    return code if isinstance(code, str) and len(code) <= 64 else "internal_error"


# ---------------------------------------------------------------- application core

class Job:
    def __init__(self, client, rid, op, kind, src, language, emotion_mode, name, upload_sha256):
        self.client, self.rid, self.op, self.kind, self.src = client, rid, op, kind, src
        self.language, self.emotion_mode, self.name, self.upload_sha256 = language, emotion_mode, name, upload_sha256
        self.linked = False
        self.done = threading.Event()
        self.status, self.body, self.raw = 500, None, None


class App:
    def __init__(self, cfg, clock=time.time):
        self.cfg, self.limits, self.clock = cfg, cfg["limits"], clock
        root = cfg["root"]
        self.api_dir, self.uploads, self.logs = root + "/api", root + "/uploads", root + "/logs"
        for d in (self.api_dir, self.uploads, self.logs):
            if not os.path.isdir(d):
                os.mkdir(d, 0o700)
            st = os.lstat(d)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise ConfigError(d + " must be a 0700 directory owned by this user")
        if os.stat(self.uploads).st_dev != os.stat(cfg["inbox"]).st_dev:
            raise ConfigError("uploads and inbox must be on the same filesystem (hard-link promotion)")
        lim = self.limits
        for p in cfg["launchd_logs"]:
            rotate_file(p, lim["log_bytes"], lim["log_files"])
        self.log = JsonLog(self.logs + "/api.log", lim["log_bytes"], lim["log_files"])
        now = self.clock()
        self.nonces = NonceStore(self.api_dir + "/nonces.log", lim["nonce_cap"], now)
        self.receipts = ReceiptStore(self.api_dir + "/receipts.log", lim["receipt_cap"], lim["receipt_days"], now)
        for r in self.receipts.recovered:
            # Crash leftover: outcome unknown; paths kept until the owner verifies the job and descendants ended.
            self.log.event("receipt_unknown", id=r["request_id"], client=r["client"],
                           paths=[self.uploads + "/" + str(r["op"]) + "/", cfg["inbox"] + "/api-" + str(r["op"]) + ".wav"])
        self.gate = threading.Lock()
        self.conn_lock, self.conns = threading.Lock(), 0
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.options |= getattr(ssl, "OP_NO_COMPRESSION", 0) | getattr(ssl, "OP_NO_RENEGOTIATION", 0)
        ctx.load_cert_chain(cfg["cert"], cfg["key"])
        self.ssl_ctx = ctx
        self.allow_net = cfg["allow"]
        self.jobs = []

    # connections
    def peer_allowed(self, ip):
        try:
            return ipaddress.ip_address(ip) in self.allow_net
        except ValueError:
            return False

    def acquire_conn(self):
        with self.conn_lock:
            if self.conns >= self.limits["max_conns"]:
                return False
            self.conns += 1
            return True

    def release_conn(self):
        with self.conn_lock:
            self.conns -= 1

    def close(self):
        for j in list(self.jobs):
            j.done.wait(60)
        self.nonces.close()
        self.receipts.close()
        self.log.close()

    def child_env(self):
        return {"PATH": CHILD_ENV_PATH, "HOME": HOME, "LANG": "en_US.UTF-8",
                "TMPDIR": os.environ.get("TMPDIR", "/tmp")}

    # wrapper calls
    def run_ml(self, args, timeout):
        """Returns (ok, stdout_bytes or None, error_code). timeout None = never kill (analyze)."""
        lim = self.limits
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                p = subprocess.Popen([self.cfg["ml"]] + list(args), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                     env=self.child_env(), cwd="/", close_fds=True, shell=False,
                                     start_new_session=True)
            except OSError:
                return False, None, "server_misconfigured"
            if timeout is None:
                p.wait()
            else:
                try:
                    p.wait(timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    p.wait()
                    return False, None, "timeout"
            if p.returncode != 0:
                return False, None, map_ml_error(last_stderr_code(err, lim["stderr_line"]))
            size = os.fstat(out.fileno()).st_size
            if size > lim["ml_stdout"]:
                return False, None, "invalid_output"
            out.seek(0)
            data = out.read().strip()
            if not data:
                return False, None, "empty_output"
            try:
                v = json.loads(data.decode("utf-8"))
            except ValueError:
                return False, None, "invalid_output"
            if not isinstance(v, dict):
                return False, None, "invalid_output"
            return True, data, None

    def convert(self, src, dst):
        """afconvert under limit_exec.py; returns (returncode or None on timeout)."""
        argv = [PYTHON, "-I", self.cfg["limit_exec"], src, dst]
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=self.child_env(), cwd="/", close_fds=True, start_new_session=True)
        cap, kept = self.limits["convert_output"], []

        def drain():
            n = 0
            while True:
                chunk = p.stdout.read(4096)
                if not chunk:
                    break
                if n < cap:
                    kept.append(chunk[:cap - n])
                    n += len(kept[-1])
        t = threading.Thread(target=drain, daemon=True)
        t.start()
        try:
            p.wait(self.limits["convert_timeout"])
            rc = p.returncode
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()
            rc = None
        t.join(5)
        p.stdout.close()
        return rc

    # job supervision
    def run_job(self, job):
        status, body, raw, rec = 500, None, None, {}
        try:
            status, body, raw, rec = self._job(job)
        except Exception as e:  # never leak details
            self.log.event("job_error", id=job.rid, kind=type(e).__name__)
            status, body = error_body("internal_error", request_id=job.rid)
            rec = {"state": "failed", "http_status": status, "error_code": "internal_error"}
        try:
            self.receipts.update(job.client, job.rid, self.clock(), **rec)
        except Exception:
            self.log.event("receipt_write_failed", id=job.rid)
            status, body, raw = error_body("storage_error", request_id=job.rid) + (None,)
        self.cleanup(job.op, job.linked)
        self.gate.release()
        job.status, job.body, job.raw = status, body, raw
        job.done.set()
        try:
            self.jobs.remove(job)
        except ValueError:
            pass

    def _job(self, job):
        def fail(code):
            st, b = error_body(code, request_id=job.rid)
            return st, b, None, {"state": "failed", "http_status": st, "error_code": code}
        wav = job.src
        if job.kind in ("mp3", "m4a"):
            wav = self.uploads + "/" + job.op + "/converted.wav"
            rc = self.convert(job.src, wav)
            self.log.event("convert_exit", id=job.rid, rc=rc)
            if rc is None:
                return fail("timeout")
            if rc != 0 or not os.path.isfile(wav):
                return fail("invalid_audio")
        if not strict_wav_check(wav):
            return fail("invalid_audio")
        dst_name = "api-" + job.op + ".wav"
        try:
            os.link(wav, self.cfg["inbox"] + "/" + dst_name)  # no overwrite, same filesystem
        except FileExistsError:
            return fail("internal_error")
        job.linked = True
        ok, data, code = self.run_ml(["analyze", dst_name, "--language", job.language, "--emotion-mode",
                                      job.emotion_mode], None)
        self.log.event("analyze_exit", id=job.rid, ok=ok, code=code)
        if not ok:
            return fail(code)
        native = json.loads(data.decode("utf-8"))
        native["request_id"], native["upload_sha256"], native["name"] = job.rid, job.upload_sha256, job.name
        rid = native.get("result_id")
        rid = rid if isinstance(rid, str) and RE_HEX64.match(rid) else None
        return 200, native, None, {"state": "done", "http_status": 200, "result_id": rid}

    def cleanup(self, op, linked):
        """Request-owned paths only, after owning processes exited. No directory sweeps."""
        d = self.uploads + "/" + op
        for n in UPLOAD_NAMES:
            try:
                os.unlink(d + "/" + n)
            except FileNotFoundError:
                pass
            except OSError:
                self.log.event("cleanup_failed", op=op, path="upload")
        try:
            os.rmdir(d)
        except FileNotFoundError:
            pass
        except OSError:
            self.log.event("cleanup_incomplete", op=op)
        if linked:
            try:
                os.unlink(self.cfg["inbox"] + "/api-" + op + ".wav")
            except FileNotFoundError:
                pass
            except OSError:
                self.log.event("cleanup_failed", op=op, path="inbox")


# ---------------------------------------------------------------- HTTP

class Request:
    pass


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        pass

    def finish(self):
        pass

    def log_message(self, format, *args):  # no request line (query values) in any log
        pass

    def handle(self):
        app = self.server.app
        lim = app.limits
        self.t0 = time.monotonic()
        self.sock = None
        self.unread_body = False
        self.info = {"client": "-", "ip": self.client_address[0], "method": "-", "route": "-", "id": None,
                     "status": 0, "bytes": 0}
        raw = self.request
        deadline = self.t0 + lim["header_deadline"]
        try:
            raw.settimeout(lim["header_deadline"])
            sock = app.ssl_ctx.wrap_socket(raw, server_side=True, do_handshake_on_connect=False)
            self.sock = sock
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            sock.do_handshake()
        except (OSError, ValueError):
            self.info["status"] = "tls_fail"
            self._finish_log()
            self._close()
            return
        try:
            self.process(app, deadline)
        except HttpError as e:
            if not e.close_silently:
                self.send_error_code(e.code)
                if self.unread_body:
                    self.drain()
        except Exception as e:
            app.log.event("handler_error", kind=type(e).__name__)
            self.send_error_code("internal_error")
        finally:
            self._finish_log()
            self._close()

    def _close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass

    def _finish_log(self):
        app = self.server.app
        i = self.info
        app.log.write({"client": i["client"], "ip": i["ip"], "method": i["method"], "route": i["route"],
                       "id": i["id"], "status": i["status"], "bytes": i["bytes"],
                       "ms": int((time.monotonic() - self.t0) * 1000)})

    # ---- io
    def send_raw(self, status, body_bytes):
        lim = self.server.app.limits
        if len(body_bytes) > lim["response_max"]:
            status, obj = error_body("invalid_output")
            body_bytes = json.dumps(obj).encode()
        phrase = http.HTTPStatus(status).phrase
        head = ("HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
                "Cache-Control: no-store\r\nConnection: close\r\n\r\n" % (status, phrase, len(body_bytes))).encode()
        self.info["status"], self.info["bytes"] = status, len(body_bytes)
        try:
            self.sock.settimeout(lim["send_timeout"])
            self.sock.sendall(head + body_bytes)
        except OSError:
            self.info["status"] = "%d_unsent" % status

    def send_json(self, status, obj):
        self.send_raw(status, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def send_error_code(self, code, **extra):
        status, body = error_body(code, **extra)
        self.send_json(status, body)

    def drain(self):
        """Bounded linger so an unread body does not reset the connection before the client reads the reply."""
        end = time.monotonic() + 0.5
        n = 0
        try:
            while n < MiB and time.monotonic() < end:
                self.sock.settimeout(max(0.01, min(0.1, end - time.monotonic())))  # stop once the peer goes idle
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                n += len(chunk)
        except OSError:
            pass

    def read_head(self, deadline, max_bytes):
        buf = bytearray()
        while True:
            idx = buf.find(b"\r\n\r\n")
            if idx >= 0:
                if idx > max_bytes:
                    raise HttpError("header_fields_too_large")
                return bytes(buf[:idx]), bytes(buf[idx + 4:])
            if len(buf) > max_bytes + 4:
                self.unread_body = True
                raise HttpError("header_fields_too_large")
            rem = deadline - time.monotonic()
            if rem <= 0:
                raise HttpError("request_timeout", close_silently=True)
            self.sock.settimeout(rem)
            try:
                chunk = self.sock.recv(8192)
            except (socket.timeout, OSError):
                raise HttpError("request_timeout", close_silently=True)
            if not chunk:
                raise HttpError("bad_request", close_silently=True)
            buf += chunk

    # ---- request processing
    def process(self, app, deadline):
        lim = app.limits
        head, rest = self.read_head(deadline, lim["header_bytes"])
        lines = head.split(b"\r\n")
        m = RE_REQLINE.match(lines[0])
        if not m:
            raise HttpError("bad_request")
        method, target_b = m.group(1).decode(), m.group(2)
        self.info["method"] = method
        if len(target_b) > lim["target_bytes"]:
            raise HttpError("uri_too_long")
        if not target_b.startswith(b"/") or any(c < 0x21 or c > 0x7E for c in target_b):
            raise HttpError("bad_request")
        target = target_b.decode("ascii")
        if len(lines) - 1 > lim["header_count"]:
            raise HttpError("header_fields_too_large")
        headers = {}
        for line in lines[1:]:
            if b":" not in line or b"\n" in line or b"\r" in line:
                raise HttpError("bad_request")
            name, value = line.split(b":", 1)
            if not RE_TOKEN.match(name):
                raise HttpError("bad_request")
            value = value.strip(b" \t")
            if any((c < 0x20 and c != 0x09) or c >= 0x7F for c in value):
                raise HttpError("bad_request")
            key = name.decode("ascii").lower()
            if key in headers:
                raise HttpError("bad_request")  # every header at most once
            headers[key] = value.decode("ascii")
        self.unread_body = "transfer-encoding" in headers or "content-length" in headers or bool(rest)
        if method not in ("GET", "POST"):
            raise HttpError("method_not_allowed")
        if "transfer-encoding" in headers:
            raise HttpError("bad_request")
        if headers.get("content-encoding", "identity").lower() != "identity":
            raise HttpError("bad_request")
        cl_text = headers.get("content-length")
        if cl_text is not None and not RE_CL.match(cl_text):
            raise HttpError("bad_request")
        cl = int(cl_text) if cl_text is not None else None
        expect = headers.get("expect")
        expect_continue = False
        if method == "GET":
            if (cl is not None and cl != 0) or rest or "content-type" in headers:
                raise HttpError("bad_request")
            if expect is not None:
                raise HttpError("expectation_failed")
            ct, cl_canon = "", "0"
        else:
            if cl is None or headers.get("content-type") != "application/octet-stream":
                raise HttpError("bad_request")
            if expect is not None:
                if expect.lower() != "100-continue":
                    raise HttpError("expectation_failed")
                expect_continue = True
            if len(rest) > cl:
                raise HttpError("bad_request")
            ct, cl_canon = headers["content-type"], cl_text
        vals = {}
        for h in ("x-ml-client", "x-ml-time", "x-ml-nonce", "x-ml-body-sha256", "x-ml-sig"):
            if h not in headers:
                raise HttpError("bad_request")
            vals[h] = headers[h]
        if not (RE_TIME.match(vals["x-ml-time"]) and RE_HEX32.match(vals["x-ml-nonce"])
                and RE_HEX64.match(vals["x-ml-body-sha256"]) and RE_HEX64.match(vals["x-ml-sig"])):
            raise HttpError("bad_request")
        # ---- auth: known client, skew, HMAC, nonce admission
        client = vals["x-ml-client"]
        key = app.cfg["clients"].get(client)
        if key is None:
            raise HttpError("unauthorized")
        now = app.clock()
        t = int(vals["x-ml-time"])
        if abs(now - t) > lim["skew"]:
            raise HttpError("unauthorized")
        canon = sign.canonical(client, method, target, ct, cl_canon, vals["x-ml-time"], vals["x-ml-nonce"],
                               vals["x-ml-body-sha256"])
        if not hmac.compare_digest(sign.signature(key, canon), vals["x-ml-sig"]):
            raise HttpError("unauthorized")
        if method == "GET" and vals["x-ml-body-sha256"] != sign.EMPTY_SHA256:
            raise HttpError("unauthorized")
        nonce = vals["x-ml-nonce"]
        res = app.nonces.admit(client, nonce, t + int(lim["skew"]), now)
        if res == "replay":
            raise HttpError("unauthorized")
        if res == "full":
            raise HttpError("nonce_store_full")
        if res == "storage":
            raise HttpError("storage_error")
        self.info["client"], self.info["id"] = client, nonce
        # ---- routing on the verified target
        path, _, query = target.partition("?")
        if path == "/analyze":
            self.info["route"] = "/analyze"
            if method != "POST":
                raise HttpError("method_not_allowed")
            return self.do_analyze(app, client, nonce, query, cl, rest, vals["x-ml-body-sha256"], expect_continue)
        if method != "GET":
            raise HttpError("method_not_allowed")
        if path == "/status":
            self.info["route"] = "/status"
            if "?" in target:
                raise HttpError("bad_request")
            return self.read_call(app, ["status"])
        m = ROUTE_REPORT.match(path)
        if m:
            self.info["route"] = "/report/:id"
            if "?" in target:
                raise HttpError("bad_request")
            return self.read_call(app, ["report", m.group(1)])
        m = ROUTE_TIMELINE.match(path)
        if m:
            self.info["route"] = "/timeline/:id"
            q = parse_query(query, ("layer", "start", "end", "limit", "cursor"))
            args = ["timeline", m.group(1)]
            if "layer" in q:
                if q["layer"] not in ("words", "emotion"):
                    raise HttpError("bad_request")
                args.append("--layer=" + q["layer"])
            for k in ("start", "end"):
                if k in q:
                    if not RE_NUM.match(q[k]):
                        raise HttpError("bad_request")
                    args.append("--%s=%s" % (k, q[k]))
            if "limit" in q:
                if not RE_LIMIT.match(q["limit"]) or not 1 <= int(q["limit"]) <= 200:
                    raise HttpError("bad_request")
                args.append("--limit=" + q["limit"])
            if "cursor" in q:
                if not RE_CURSOR.match(q["cursor"]):
                    raise HttpError("bad_request")
                args.append("--cursor=" + q["cursor"])
            return self.read_call(app, args)
        m = ROUTE_RECEIPT.match(path)
        if m:
            self.info["route"] = "/receipt/:id"
            if "?" in target:
                raise HttpError("bad_request")
            r = app.receipts.get(client, m.group(1))
            if r is None:
                raise HttpError("not_found")
            return self.send_json(200, {k: r[k] for k in ("request_id", "state", "http_status", "result_id",
                                                           "error_code", "upload_sha256")})
        raise HttpError("not_found")

    def read_call(self, app, args):
        ok, data, code = app.run_ml(args, app.limits["read_timeout"])
        if not ok:
            raise HttpError(code)
        self.send_raw(200, data)  # native JSON bytes unchanged

    def do_analyze(self, app, client, rid, query, cl, rest, body_sha, expect_continue):
        lim = app.limits
        q = parse_query(query, ("language", "emotion_mode", "name"))
        if q.get("language") not in ("en", "ko", "ja", "auto"):
            raise HttpError("bad_request")
        emotion_mode = q.get("emotion_mode", "none")
        if emotion_mode not in ("voice", "music", "none"):
            raise HttpError("bad_request")
        name = q.get("name")
        if name is not None and not valid_name(name):
            raise HttpError("bad_request")
        if cl > lim["max_body"]:
            self.send_error_code("payload_too_large")
            return self.drain()
        if app.receipts.get(client, rid) is not None:  # operation identity reused: never re-dispatch
            self.send_error_code("operation_exists", request_id=rid)
            return self.drain()
        if not app.gate.acquire(blocking=False):  # one API job; nothing staged yet
            self.send_error_code("busy")
            return self.drain()
        op, launched = secrets.token_hex(16), False
        reserved = False
        try:
            if shutil.disk_usage(app.cfg["root"]).free < lim["disk_floor"]:
                self.send_error_code("insufficient_storage")
                return self.drain()
            res = app.receipts.reserve(client, rid, op, app.clock())
            if res != "ok":
                self.send_error_code({"exists": "operation_exists", "full": "receipt_store_full",
                                      "storage": "storage_error"}[res])
                return self.drain()
            reserved = True
            if expect_continue:
                self.sock.settimeout(lim["send_timeout"])
                self.sock.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
            code, kind, upload_sha = self.stage(app, op, cl, rest, body_sha)
            if code is not None:
                self._prelaunch_fail(app, client, rid, op, code)
                return
            job = Job(client, rid, op, kind, app.uploads + "/" + op + "/upload." + kind, q["language"], emotion_mode,
                      name, upload_sha)
            try:
                app.receipts.update(client, rid, app.clock(), upload_sha256=upload_sha)
            except OSError:
                self._prelaunch_fail(app, client, rid, op, "storage_error")
                return
            app.jobs.append(job)
            th = threading.Thread(target=app.run_job, args=(job,), daemon=True)
            th.start()
            launched = True
        except Exception:
            if reserved and not launched:
                self._prelaunch_fail(app, client, rid, op, "internal_error")
                return
            raise
        finally:
            if not launched and not reserved:
                app.gate.release()
        remaining = self.t0 + lim["handler_total"] - time.monotonic()
        if job.done.wait(max(0.0, remaining)):
            self.send_json(job.status, job.body)
        else:
            self.send_json(504, {"request_id": rid, "state": "running"})

    def _prelaunch_fail(self, app, client, rid, op, code):
        status, body = error_body(code, request_id=rid)
        try:
            app.receipts.update(client, rid, app.clock(), state="failed", http_status=status, error_code=code)
        except Exception:
            app.log.event("receipt_write_failed", id=rid)
        app.cleanup(op, False)
        app.gate.release()
        self.send_json(status, body)

    def stage(self, app, op, cl, rest, body_sha):
        """Upload <= upload_deadline into uploads/<op>/ (0700), hashed as received. Returns (error, kind, sha)."""
        lim = app.limits
        d = app.uploads + "/" + op
        try:
            os.mkdir(d, 0o700)
            fd = os.open(d + "/upload.part", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError:
            return "storage_error", None, None
        h = hashlib.sha256()
        got, head = 0, b""
        deadline = time.monotonic() + lim["upload_deadline"]
        err = None
        try:
            chunk = rest
            while True:
                if chunk:
                    h.update(chunk)
                    if len(head) < 16:
                        head += chunk[:16 - len(head)]
                    view = memoryview(chunk)
                    while view:
                        n = os.write(fd, view)
                        view = view[n:]
                    got += len(chunk)
                if got >= cl:
                    break
                rem = deadline - time.monotonic()
                if rem <= 0:
                    err = "request_timeout"
                    break
                self.sock.settimeout(rem)
                try:
                    chunk = self.sock.recv(min(262144, cl - got))
                except socket.timeout:
                    err = "request_timeout"
                    break
                except OSError:
                    err = "truncated_body"
                    break
                if not chunk:
                    err = "truncated_body"
                    break
        finally:
            os.close(fd)
        if err:
            return err, None, None
        self.unread_body = False
        digest = h.hexdigest()
        if not hmac.compare_digest(digest, body_sha):
            return "unauthorized", None, None
        kind = sniff(head)
        if kind is None:
            return "invalid_audio", None, None
        os.rename(d + "/upload.part", d + "/upload." + kind)
        return None, kind, digest


class Listener(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, addr, app):
        self.app = app
        super().__init__(addr, Handler)

    def server_bind(self):
        TCPServer.server_bind(self)  # skip getfqdn reverse lookup
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port

    def verify_request(self, request, client_address):
        # Peer outside allow: close before reading anything (secondary gate).
        if not self.app.peer_allowed(client_address[0]):
            return False
        return self.app.acquire_conn()

    def handle_error(self, request, client_address):
        self.app.log.event("listener_error")

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.app.release_conn()


def start(app, addresses, port):
    listeners = []
    for a in addresses:
        try:
            srv = Listener((a, port), app)
        except OSError as e:
            app.log.event("bind_failed", addr=a, errno=e.errno)
            continue
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        th.start()
        listeners.append(srv)
        app.log.event("listening", addr=a, port=srv.server_address[1])
    return listeners


def stop(listeners):
    for s in listeners:
        s.shutdown()
        s.server_close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Music Lens LAN API server")
    ap.add_argument("--config", default=HOME + "/.music-lens/api.json")
    ns = ap.parse_args(argv)
    try:
        cfg = load_config(ns.config)
        app = App(cfg)
    except (ConfigError, OSError, ssl.SSLError) as e:
        sys.stderr.write("music-lens-lan-api: config error: %s\n" % (e if isinstance(e, ConfigError) else type(e).__name__))
        return 2
    addrs = discover_addresses(cfg, app.log)
    listeners = start(app, addrs, cfg["port"]) if addrs else []
    if not listeners:
        app.log.event("no_listener")
        sys.stderr.write("music-lens-lan-api: no address bound inside allow\n")
        return 1
    if cfg["test_ready_file"]:
        with open(cfg["test_ready_file"] + ".tmp", "w") as f:
            json.dump({"ports": [s.server_address[1] for s in listeners], "pid": os.getpid()}, f)
        os.replace(cfg["test_ready_file"] + ".tmp", cfg["test_ready_file"])
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stopped.set())
    signal.signal(signal.SIGINT, lambda *a: stopped.set())
    while not stopped.wait(1):
        pass
    stop(listeners)
    app.log.event("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
