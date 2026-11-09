#!/usr/bin/python3 -I
"""Music Lens LAN API v0.6 server (MUSIC-LENS-LAN-API-1026).

Stdlib only, runs on /usr/bin/python3 (3.9). Calls only the team wrapper `ml` by argv (no shell).
The listener loads no model; models load only inside each `ml analyze` job's subprocesses.

Usage: server.py [--config PATH]   (default ~/.music-lens/api.json, mode 0600)
"""
import argparse
import base64
import bisect
import errno
import fcntl
import hashlib
import hmac
import http
import ipaddress
import json
import logging
import logging.handlers
import math
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
import sheet  # noqa: E402
import sign  # noqa: E402

HOME = os.path.expanduser("~")
MiB = 1024 * 1024
DEFAULT_LIMITS = {
    "skew": 120, "nonce_cap": 20000, "receipt_cap": 2000, "receipt_days": 30,
    "max_conns": 8, "header_bytes": 16 * 1024, "header_count": 40, "target_bytes": 2048,
    "header_deadline": 10.0, "upload_deadline": 60.0, "convert_timeout": 120.0, "handler_total": 800.0,
    "read_timeout": 30.0, "max_body": 64 * MiB, "disk_floor": 2 * 1024 * MiB, "ml_stdout": 8 * MiB,
    "response_max": 8 * MiB, "stderr_line": 4096, "convert_output": 4096, "log_bytes": 5 * MiB, "log_files": 3,
    "send_timeout": 30.0, "stop_grace": 630.0, "rhythm_timeout": 300.0, "rhythm_output": 4 * MiB,
    "earcheck_timeout": 60.0, "roll_timeout": 60.0, "view_timeout": 180.0, "ml_stop_grace": 40.0,
}
CONFIG_KEYS = {"bind", "port", "allow", "clients", "cert", "key", "ml", "root", "inbox", "limit_exec",
               "launchd_logs", "test_mode", "limits", "test_ready_file", "rhythm"}
RHYTHM_KEYS = {"python", "worker"}
RHYTHM_SEPARATION_KEYS = {"demucs", "rmvpe"}
RHYTHM_BASS_KEYS = {"basic_pitch_python"}
RHYTHM_SECTIONS_KEYS = {"allin1_python", "allin1_model"}
RHYTHM_SINGING_KEYS = {"yourmt3_python"}
SEP_DIR = "sep"  # per-request scratch of the rhythm worker, under the upload directory
RHYTHM_DIR = "results-rhythm"  # under root: <result_id>.json, 0600, written atomically
SECTIONS_CACHE_DIR = "cache-allin1"  # under root, 0700: matplotlib's font list for allin1_sections.py, kept between runs
RHYTHM_LAYERS = ("beats", "chords", "energy", "melody", "genre", "bass", "other", "drums", "bars")
RHYTHM_MAX_BEATS = 5000
RHYTHM_MAX_CHORDS = 20000
RHYTHM_MAX_KEY_SECTIONS = 64  # worker 0.2.0 emits <= 30 (20 s windows over 600 s)
RHYTHM_CHORD_GRIDS = ("beat", "frame")
RHYTHM_CHORD_RELIABILITY = ("low_reference", "reference_candidate")
MAX_WIRE_PAGE = 128 * 1024 // 3  # same page byte cap as the native timeline (MAX_WIRE // 3)
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
    "rhythm_unavailable": (400, "Rhythm analysis is not configured on this server"),
    "unauthorized": (401, None),
    "not_found": (404, "Not found"),
    "rhythm_not_found": (404, "No rhythm data is stored for this result"),
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
    "earcheck_failed": (500, "Ear check synth failed"),
    "roll_failed": (500, "Sheet PNG render failed"),
    "empty_output": (500, "Analyzer returned no output"),
    "cancelled": (500, "Analysis was cancelled"),
    "nonce_store_full": (503, "Nonce store full; retry later"),
    "insufficient_storage": (503, "Not enough free disk space"),
    "receipt_store_full": (503, "Receipt store full"),
    "storage_error": (503, "Durable storage failed; nothing launched"),
    "outcome_unknown": (503, "Analysis may have run but its outcome could not be stored; "
                             "read /receipt later and do not retry"),
    "shutting_down": (503, "Server is stopping; nothing launched"),
    "earcheck_unavailable": (503, "Ear check is not available on this server"),
    "roll_unavailable": (503, "Sheet PNG is not available on this server (no rhythm roll_python)"),
    "timeout": (504, "Operation timed out"),
}
ML_400 = ("invalid_audio", "invalid_path", "invalid_id", "invalid_argument")
ML_MISCONFIG = ("pin_mismatch", "configuration", "dependency_missing")
ML_500_KEEP = ("invalid_output", "invalid_result", "empty_output", "cancelled", "internal_error")
# ml 0.2.6's fixed reason texts (music_lens_local.py Failure messages) per code the server keeps as is. Only these
# reach the client, as error.detail: never other stderr text (paths, OSError strings, diagnostics).
ML_REASONS = {
    "empty_output": ("No timed words in structurally valid output; silence or unrecognized audio is not a successful transcript",
                     "No requested analysis layer succeeded; no result published"),
    "invalid_audio": ("WAV exceeds 64 MiB", "Expected mono 16000 Hz signed16 PCM WAV",
                      "Empty, truncated or longer-than-600s WAV", "Malformed PCM WAV"),
    "invalid_path": ("Invalid audio path or traversal", "Audio path must resolve inside audio root",
                     "Audio must be a regular file", "Result symlinks are forbidden"),
    "invalid_id": ("Expected a 64-character lowercase hex result ID",),
    "not_found": ("Unknown result ID",),
    "invalid_argument": ("end must be at least start", "layer must be words or emotion", "cursor exceeds matching word count",
                         "title must be 1..300 printable characters", "language must be explicit: auto, en, ko or ja",
                         "emotion_mode must be none, voice or music", "Unknown tool or invalid arguments",
                         "Unknown or missing arguments"),
    "busy": ("This analysis is locked; stale locks require manual owner verification",),
    "timeout": ("Analysis exceeded configured timeout", "Inference exceeded configured timeout"),
    "cancelled": ("Analysis cancelled", "Analysis cancelled before child start", "Analysis cancelled during preparation",
                  "Analysis cancelled before inference", "Analysis cancelled between stages",
                  "Analysis cancelled before publication", "Inference cancelled", "Inference cancelled before publication"),
}
NO_WORDS_HINT = ("no layer succeeded (no words recognized, or speech recognition failed); for instrumental audio use "
                 "emotion_mode=music with rhythm=1 (local CLI: --emotion-mode music, without --no-rhythm) so the "
                 "music-mood layer succeeds and the rhythm layer runs")

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
ROUTE_SHEET = re.compile(r"\A/sheet/([0-9a-f]{64})\Z")
ROUTE_RECEIPT = re.compile(r"\A/receipt/([0-9a-f]{32})\Z")
ROUTE_EARCHECK = re.compile(r"\A/earcheck/([0-9a-f]{64})\Z")
M4A_BRANDS = (b"M4A ", b"M4B ", b"mp42", b"mp41", b"isom", b"iso2")
PCM_GUID = b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
UPLOAD_NAMES = ("upload.part", "upload.wav", "upload.mp3", "upload.m4a", "converted.wav", "listen.wav", "earcheck.wav",
                "roll.png", "roll.png.part", "roll-marks.json", "view.m4a", "view.html", "view.html.part")
ROLL = os.path.join(HERE, "roll.py")  # run by rhythm roll_python (numpy, matplotlib); never imported here
VIEW = os.path.join(HERE, "view.py")  # sheet HTML, run by roll_python too (with view.html); never imported here
VIEW_MAX = 24 * MiB  # view.py SIZE_MAX: the HTML response may exceed response_max (it carries the audio)
HTML_DETAIL = ("format=html is POST only, with the analysed audio as the body; optional marks=1; no other parameter")
EAR_CHECK = os.path.join(HERE, "ear_check.py")  # run by the rhythm python (numpy); never imported here
EARCHECK_LAYERS = ("melody", "bass", "other", "chords", "drums", "clicks")  # ear_check.LAYERS
EARCHECK_DEFAULT = ("melody", "bass", "other", "chords", "drums")  # ear_check.DEFAULT
RE_PAGE = re.compile(r"\A[1-9][0-9]{0,3}\Z")  # limit_exec.RE_PAGE
ROLL_RENDER = ("POST /sheet/<result_id>?format=png&page=N with the analysed audio as the body returns that page as image/png; "
               "add marks=1 for suspect-note and missed-energy marks; style=poster&start=S&end=E[&title=T&credit=C] "
               "for a 2400x1350 poster of at most 30 s")
ROLL_POSTER_MAX = 30.0  # s; roll.py POSTER_MAX
ROLL_POSTER_MIN = 0.5  # s; roll.py POSTER_MIN
ROLL_TEXT_MAX = 80  # roll.py TEXT_MAX
ROLL_POSTER_DETAIL = "poster span: 0.5 s <= end - start <= 30 s, end within the result's duration"
ROLL_TEXT_DETAIL = "title and credit: at most 80 printable characters"
ROLL_DURATION_TOL = 0.5  # s; the posted audio must have the result's duration
ROLL_AUDIO_DETAIL = "audio duration differs from the result's by more than %g s: not this result's audio" % ROLL_DURATION_TOL
ROLL_DETAIL = ("format=png takes no other parameter on GET (page index); POST it with the audio and either page=N[&marks=1] "
               "or style=poster&start=S&end=E[&title=T&credit=C]")
SPAN_DETAIL = "end - start must be more than 0 and at most %g s"
VIEW_DETAIL = ("view=brief or layers=<comma list of %s>, one of them, not with start/end"
               % ",".join(sheet.SECTION_LAYERS))
EARCHECK_MAX_SPAN = 30.0  # s; 30 s stereo s16 44.1 kHz = 5,292,044 bytes, base64 7.06 MB < response_max 8 MiB
LISTEN_RATE = 44100
LISTEN_MAX_BYTES = 56 * MiB  # limit_exec.LISTEN_FSIZE_BYTES
RE_EARCHECK_OP = re.compile(r"\A(ec|rl)-[0-9a-f]{32}\Z")  # uploads/ec-<hex> (ear-check), rl-<hex> (sheet PNG/HTML): never owned by a receipt


class ConfigError(Exception):
    pass


class HttpError(Exception):
    def __init__(self, code, close_silently=False, detail=None):
        super().__init__(code)
        self.code = code
        self.close_silently = close_silently
        self.detail = detail  # fixed server text naming the broken limit; never echoes client input


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


def _write_tmp(path, lines):
    """Write `lines` to path.tmp completely and fsync it. Returns the tmp path."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        write_all(fd, "".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines).encode())
        full_fsync(fd)
    finally:
        os.close(fd)
    return tmp


def write_all(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError(errno.EIO, "zero-length write")
        view = view[n:]


def atomic_write_lines(path, lines):
    os.replace(_write_tmp(path, lines), path)
    fsync_dir(os.path.dirname(path))


class JournalCorrupt(Exception):
    pass


def load_journal(path):
    """Parse a JSON-lines journal. Returns (records, torn_tail).

    Only the final line may be damaged (torn by a crash mid-append): it is truncated away and the file fsynced
    before any new work. An unparsable line followed by any other line raises JournalCorrupt, because it may hide
    a later acknowledged record; the owner must inspect the file."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return [], False
    out, pos, good, bad = [], 0, 0, None
    while pos < len(data):
        nl = data.find(b"\n", pos)
        end = len(data) if nl < 0 else nl + 1
        v = None
        if nl >= 0:
            try:
                v = json.loads(data[pos:nl].decode("utf-8"))
            except ValueError:
                v = None
        if bad is not None:
            raise JournalCorrupt("%s: unparsable record at byte %d is not the last line" % (path, bad))
        if isinstance(v, dict):
            out.append(v)
            good = end
        else:
            bad = pos
        pos = end
    if bad is None:
        return out, False
    fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
    try:
        os.ftruncate(fd, good)
        full_fsync(fd)
    finally:
        os.close(fd)
    return out, True


class AppendLog:
    """Append-only JSON-lines journal with a durable boundary `good` (bytes fully written and fsynced).

    Any write/fsync failure poisons the log. Every later append or compaction first repairs it and raises if the
    repair fails, so nothing is acknowledged on a poisoned log (fail closed). Repair truncates back to `good` and
    fsyncs; if the descriptor no longer names the journal (or the file is shorter than `good`) it rebuilds the
    journal from the owner's in-memory `snapshot()` instead."""

    def __init__(self, path, snapshot):
        self.path, self.snapshot = path, snapshot
        self.fd = self._open()
        self.good = os.fstat(self.fd).st_size
        self.poisoned = None  # None | "append" (tail past good) | "rebind" (fd may name an unlinked inode)
        self.closed = False

    def _open(self):
        return os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)

    def _same_file(self):
        try:
            a, b = os.fstat(self.fd), os.stat(self.path)
        except OSError:
            return False
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) and a.st_size >= self.good

    def _ready(self):
        if self.closed:
            raise OSError(errno.EBADF, "journal closed")
        if self.poisoned == "append" and self._same_file():
            os.ftruncate(self.fd, self.good)
            full_fsync(self.fd)
            if os.fstat(self.fd).st_size != self.good:
                raise OSError(errno.EIO, "journal repair did not reach the durable boundary")
            self.poisoned = None
        elif self.poisoned:
            self.compact(self.snapshot())

    def append(self, obj):
        self._ready()
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        try:
            write_all(self.fd, data)
            full_fsync(self.fd)
        except OSError:
            self.poisoned = "append"
            raise
        self.good += len(data)

    def compact(self, lines):
        """One fail-closed transition: tmp+fsync, replace, fsync dir, rebind fd to the new inode, reset good."""
        if self.closed:
            raise OSError(errno.EBADF, "journal closed")
        tmp = _write_tmp(self.path, lines)  # failure here leaves the journal and fd untouched
        os.replace(tmp, self.path)
        self.poisoned = "rebind"
        fsync_dir(os.path.dirname(self.path))
        fd = self._open()
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = fd
        a, b = os.fstat(fd), os.stat(self.path)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise OSError(errno.EIO, "journal descriptor does not name the journal")
        self.good = a.st_size
        self.poisoned = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            os.close(self.fd)
        except OSError:
            pass


class NonceStore:
    """Durable (client, nonce, expiry) registry; live entries are never evicted."""

    def __init__(self, path, cap, now):
        self.path, self.cap, self.lock = path, cap, threading.Lock()
        self.live = {}
        records, self.torn_tail = load_journal(path)
        for e in records:
            c, n, x = e.get("c"), e.get("n"), e.get("e")
            if isinstance(c, str) and isinstance(n, str) and isinstance(x, int) and x >= now:
                self.live[(c, n)] = max(x, self.live.get((c, n), 0))
        atomic_write_lines(path, self._snapshot())
        self.lines = len(self.live)
        self.log = AppendLog(path, self._snapshot)

    def _snapshot(self):
        return [{"c": c, "n": n, "e": x} for (c, n), x in self.live.items()]

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
                if self.lines >= 2 * len(self.live) + 1000:
                    self._purge(now)
                    self.log.compact(self._snapshot())
                    self.lines = len(self.live)
                self.log.append({"c": client, "n": nonce, "e": expiry})
            except OSError:
                return "storage"  # nothing admitted; the log stays poisoned until a repair succeeds
            self.live[(client, nonce)] = expiry
            self.lines += 1
            return "ok"

    def close(self):
        with self.lock:
            self.log.close()


class ReceiptStore:
    """Durable receipts keyed by operation identity (client, request_id = POST nonce)."""
    FIELDS = ("client", "request_id", "op", "state", "http_status", "result_id", "error_code", "upload_sha256",
              "created", "updated")

    def __init__(self, path, cap, days, now):
        self.path, self.cap, self.max_age, self.lock = path, cap, days * 86400, threading.Lock()
        self.items = {}
        records, self.torn_tail = load_journal(path)
        for e in records:
            if isinstance(e.get("client"), str) and isinstance(e.get("request_id"), str):
                self.items[(e["client"], e["request_id"])] = {k: e.get(k) for k in self.FIELDS}
        self.recovered = []
        for r in self.items.values():
            if r["state"] == "running":
                r["state"], r["updated"] = "unknown", int(now)
                self.recovered.append(dict(r))
        self._prune(now)
        atomic_write_lines(path, self._snapshot())
        self.lines = len(self.items)
        self.log = AppendLog(path, self._snapshot)

    def _snapshot(self):
        return list(self.items.values())

    def _write(self, r):
        """Journal one record (compacting first when due). OSError propagates; memory is the caller's job."""
        if self.lines >= 2 * len(self.items) + 1000:
            self.log.compact(self._snapshot())
            self.lines = len(self.items)
        self.log.append(r)
        self.lines += 1

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
                self._write(r)
            except OSError:
                return "storage"
            self.items[(client, rid)] = r
            return "ok"

    def update(self, client, rid, now, **fields):
        with self.lock:
            r = dict(self.items[(client, rid)])
            r.update(fields)
            r["updated"] = int(now)
            self._write(r)  # OSError propagates to the caller; memory keeps the last durable record
            self.items[(client, rid)] = r
            return dict(r)

    def _mark_unknown(self, key, now, code):
        r = dict(self.items[key], state="unknown", updated=int(now), error_code=code)
        self.items[key] = r  # unknown is the conservative state: a durable "running" also reads unknown at restart
        try:
            self._write(r)
            return True
        except OSError:
            return False

    def mark_unknown(self, client, rid, now, code):
        """Running -> unknown in memory, then best-effort journal. Returns True if durable, None if not running."""
        with self.lock:
            r = self.items.get((client, rid))
            if r is None or r["state"] != "running":
                return None
            return self._mark_unknown((client, rid), now, code)

    def mark_running_unknown(self, now, code):
        with self.lock:
            out = []
            for k in [k for k, r in self.items.items() if r["state"] == "running"]:
                durable = self._mark_unknown(k, now, code)
                out.append((dict(self.items[k]), durable))
            return out

    def close(self):
        with self.lock:
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
        raise ConfigError("allow must be an IPv4 network like 192.0.2.0/24")
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
    cfg["rhythm"] = None
    if "rhythm" in raw:  # optional layer; absent = unavailable
        rh = raw["rhythm"]
        if not isinstance(rh, dict) or set(rh) - {"separation", "chord_model", "bass", "drum_model", "sections", "singing", "roll_python"} != RHYTHM_KEYS:
            raise ConfigError("rhythm must be an object with python, worker and optionally separation, chord_model, bass, drum_model, sections, singing and roll_python")
        sp = rh.get("separation")
        if "separation" in rh and (not isinstance(sp, dict) or set(sp) != RHYTHM_SEPARATION_KEYS):
            raise ConfigError("rhythm separation must be an object with exactly demucs and rmvpe")
        ba = rh.get("bass")
        if "bass" in rh and (not sp or not isinstance(ba, dict) or set(ba) != RHYTHM_BASS_KEYS):
            raise ConfigError("rhythm bass needs separation and exactly basic_pitch_python")
        if "drum_model" in rh and not sp:
            raise ConfigError("rhythm drum_model needs separation")
        sc = rh.get("sections")
        if "sections" in rh and (not sp or not isinstance(sc, dict) or set(sc) != RHYTHM_SECTIONS_KEYS):
            raise ConfigError("rhythm sections needs separation and exactly allin1_python and allin1_model")
        sg = rh.get("singing")
        if "singing" in rh and (not sp or not isinstance(sg, dict) or set(sg) != RHYTHM_SINGING_KEYS):
            raise ConfigError("rhythm singing needs separation and exactly yourmt3_python")
        out = {}
        for k, raw_path in [("python", rh["python"]), ("worker", rh["worker"])] + sorted((sp or {}).items()) + (
                [("chord_model", rh["chord_model"])] if "chord_model" in rh else []) + sorted((ba or {}).items()) + (
                [("drum_model", rh["drum_model"])] if "drum_model" in rh else []) + sorted((sc or {}).items()) + sorted((sg or {}).items()) + (
                [("roll_python", rh["roll_python"])] if "roll_python" in rh else []):
            out[k] = _abs(raw_path, "rhythm " + k)
            try:
                st = os.stat(out[k])
            except OSError:
                raise ConfigError("rhythm " + k + " missing")
            if not stat.S_ISREG(st.st_mode):
                raise ConfigError("rhythm " + k + " is not a regular file")
            if st.st_mode & 0o022:
                raise ConfigError("rhythm " + k + " must not be group or world writable")
        if not os.access(out["python"], os.X_OK):
            raise ConfigError("rhythm python not executable")
        if sp and not os.access(out["demucs"], os.X_OK):
            raise ConfigError("rhythm demucs not executable")
        if ba and not os.access(out["basic_pitch_python"], os.X_OK):
            raise ConfigError("rhythm basic_pitch_python not executable")
        if sc and not os.access(out["allin1_python"], os.X_OK):
            raise ConfigError("rhythm allin1_python not executable")
        if sg and not os.access(out["yourmt3_python"], os.X_OK):
            raise ConfigError("rhythm yourmt3_python not executable")
        if "roll_python" in out and not os.access(out["roll_python"], os.X_OK):
            raise ConfigError("rhythm roll_python not executable")
        cfg["rhythm"] = out
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
    data = pcm16_wav_data(path, max_bytes, 16000, 1)
    return data is not None and 0 < data / 32000.0 <= 600


def pcm16_wav_data(path, max_bytes, rate, channels):
    """Data bytes of a strict RIFF/WAVE s16 PCM file (plain or EXTENSIBLE-PCM) at exactly `rate` and `channels`,
    chunks tiling the file exactly; None otherwise."""
    size = os.path.getsize(path)
    if size < 44 or size > max_bytes:
        return None
    with open(path, "rb") as f:
        hdr = f.read(12)
        if hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE" or struct.unpack("<I", hdr[4:8])[0] + 8 != size:
            return None
        pos, fmt, data, chunks = 12, None, None, 0
        while pos < size:
            chunks += 1
            if chunks > 64 or pos + 8 > size:
                return None
            f.seek(pos)
            cid, csz = struct.unpack("<4sI", f.read(8))
            end = pos + 8 + csz
            if end > size:
                return None
            if cid == b"fmt ":
                if fmt is not None or data is not None or csz < 16:
                    return None
                fmt = struct.unpack("<HHIIHH", f.read(16))
                if fmt[0] == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE (afconvert writes it): accept only plain 16-bit PCM
                    if csz < 40:
                        return None
                    cb, valid, mask, guid = struct.unpack("<HHI16s", f.read(24))
                    if cb != 22 or valid != 16 or mask not in (0, 4 if channels == 1 else 3) or guid != PCM_GUID:
                        return None
                    fmt = (1,) + fmt[1:]
            elif cid == b"data":
                if data is not None or fmt is None:
                    return None
                data = csz
            pos = end + (csz & 1)
        if pos != size or fmt is None or data is None:
            return None
        if fmt != (1, channels, rate, rate * 2 * channels, 2 * channels, 16) or data % (2 * channels):
            return None
        return data


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


def error_body(code, detail=None, hint=None, **extra):
    status, message = ERRORS[code]
    if code == "unauthorized":
        return status, {"error": {"code": code}}  # auth failures carry nothing else
    body = {"error": {"code": code, "message": message}}
    if detail:
        body["error"]["detail"] = detail
    if hint:
        body["error"]["hint"] = hint
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
    return last_stderr_error(f, max_line)[0]


def ml_error(f, max_line):
    """(server code, detail): ml's code mapped, and its message when it is one of ML_REASONS[code]."""
    code, message = last_stderr_error(f, max_line)
    mapped = map_ml_error(code)
    return mapped, (message if mapped == code and message in ML_REASONS.get(code, ()) else None)


def last_stderr_error(f, max_line):
    """Parse only the last non-empty stderr line (<= max_line bytes) as {"error":{"code","message"}}: (code, message
    or None)."""
    size = os.fstat(f.fileno()).st_size
    span = min(size, max_line * 2 + 2)
    f.seek(size - span)
    tail = f.read(span).rstrip(b"\r\n\t ")
    if not tail:
        return "internal_error", None
    nl = tail.rfind(b"\n")
    if nl < 0 and span < size:
        return "internal_error", None  # the last line is longer than we read
    line = tail[nl + 1:]
    if len(line) > max_line:
        return "internal_error", None
    try:
        v = json.loads(line.decode("utf-8"))
        code, message = v["error"]["code"], v["error"].get("message")
    except (ValueError, KeyError, TypeError):
        return "internal_error", None
    if not (isinstance(code, str) and len(code) <= 64):
        return "internal_error", None
    return code, message if isinstance(message, str) else None


# ---------------------------------------------------------------- rhythm layer (optional)

def file_sha256(path, cap=16 * 1024 * 1024):
    """Hex SHA-256 of a regular file (<= cap bytes), or None when it cannot be read."""
    h = hashlib.sha256()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None
            n = 0
            for chunk in iter(lambda: f.read(65536), b""):
                n += len(chunk)
                if n > cap:
                    return None
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _num(v, lo, hi):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and _finite(v) and lo <= v <= hi


def _finite(v):
    """Finite float, or int small enough that math.isfinite cannot overflow (JSON allows 10**400)."""
    if isinstance(v, int):
        return abs(v) <= 2 ** 53
    return math.isfinite(v)


def _reject_constant(name):
    raise ValueError("non-finite JSON constant " + name)


def loads_rhythm(data):
    """Strict JSON for rhythm output and stored files: NaN/Infinity literals refused (ValueError)."""
    return json.loads(data.decode("utf-8"), parse_constant=_reject_constant)


def _tree_ok(v, depth=0):
    """Every number anywhere is finite and bounded; containers nest at most 6 deep."""
    if depth > 6:
        return False
    if isinstance(v, dict):
        return all(isinstance(k, str) and _tree_ok(x, depth + 1) for k, x in v.items())
    if isinstance(v, list):
        return all(_tree_ok(x, depth + 1) for x in v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _finite(v)
    return v is None or isinstance(v, (str, bool))


def _short_str(v, n=64):
    return isinstance(v, str) and 0 < len(v) <= n and not any(unicodedata.category(c) == "Cc" for c in v)


def check_rhythm(v):
    """Shape check for worker output and stored rhythm files. Returns the dict or None."""
    try:
        if not isinstance(v, dict) or not _tree_ok(v) or not _num(v.get("duration"), 0, 600.5):
            return None
        dur = v["duration"] + 0.5
        if not (_num(v.get("bpm"), 0, 1000) and _num(v.get("bpm_confidence"), 0, 100)
                and _num(v.get("sample_rate"), 1, 400000)):
            return None
        beats = v.get("beats")
        if not isinstance(beats, list) or len(beats) > RHYTHM_MAX_BEATS or not all(_num(b, 0, dur) for b in beats):
            return None
        if any(b2 < b1 for b1, b2 in zip(beats, beats[1:])):
            return None
        chords = v.get("chords")
        if not isinstance(chords, list) or len(chords) > RHYTHM_MAX_CHORDS:
            return None
        for c in chords:
            if (not isinstance(c, dict) or not _num(c.get("start"), 0, dur) or not _num(c.get("end"), 0, dur)
                    or c["end"] < c["start"] or not _short_str(c.get("label"), 16)
                    or not (c.get("strength") is None or _num(c.get("strength"), -1e6, 1e6))
                    or c.get("one_beat", True) is not True or not _short_str(c.get("display", "-"), 24)):
                return None
        key = v.get("key")
        if not (isinstance(key, dict) and _short_str(key.get("tonic"), 8) and _short_str(key.get("scale"), 16)
                and _num(key.get("strength"), -1e6, 1e6)):
            return None
        for k in ("downbeats", "tool"):
            d = v.get(k)
            if not isinstance(d, dict) or len(d) > 8 or not all(_short_str(x, 200) or (k, n) == ("downbeats", "count") for n, x in d.items()):
                return None
        w = v.get("warnings")
        if not isinstance(w, list) or len(w) > 32 or not all(_short_str(x, 200) for x in w):
            return None
        est = v.get("bpm_estimates", [])
        if not isinstance(est, list) or len(est) > 16:
            return None
        for e in est:
            if not isinstance(e, dict) or not _num(e.get("bpm"), 0, 1000) or not _num(e.get("share"), 0, 1):
                return None
        if (not _rhythm_v2_ok(v, dur) or not _rhythm_v21_ok(v) or not _rhythm_v22_ok(v)
                or not _rhythm_v30_ok(v, dur) or not _rhythm_v40_ok(v, dur) or not _rhythm_v43_ok(v, dur)
                or not _rhythm_v50_ok(v, dur) or not _rhythm_v80_ok(v, dur) or not _rhythm_v90_ok(v, dur)
                or not _rhythm_v110_ok(v) or not _rhythm_v120_ok(v, dur) or not _rhythm_v130_ok(v) or not _rhythm_v140_ok(v, dur) or not _rhythm_v160_ok(v) or not _rhythm_v170_ok(v) or not _rhythm_v190_ok(v)):
            return None
        for k in ("worker_sha256", "words_sha256"):  # server-added identities
            if k in v and not (isinstance(v[k], str) and RE_HEX64.match(v[k])):
                return None
        if v.get("profile", "quick") != "quick":  # server-added: present only on a rhythm=quick result
            return None
    except (TypeError, KeyError, OverflowError):
        return None
    return v


def _rhythm_v2_ok(v, dur):
    """Fields added by worker 0.2.0. Each may be absent (files stored by 0.1.0); when present it must be valid."""
    if "bpm_alternatives" in v:
        alt = v["bpm_alternatives"]
        if not isinstance(alt, list) or len(alt) > 4 or not all(_num(a, 1, 1000) for a in alt):
            return False
    if "bpm_alternatives_note" in v and not _short_str(v["bpm_alternatives_note"], 200):
        return False
    if "key_sections" in v:
        ks = v["key_sections"]
        if not isinstance(ks, list) or len(ks) > RHYTHM_MAX_KEY_SECTIONS:
            return False
        prev = 0
        for r in ks:
            if (not isinstance(r, dict) or not _num(r.get("start"), 0, dur) or not _num(r.get("end"), 0, dur)
                    or r["end"] < r["start"] or r["start"] < prev or not _short_str(r.get("tonic"), 8)
                    or not _short_str(r.get("scale"), 16)
                    or not (r.get("strength") is None or _num(r.get("strength"), -1e6, 1e6))):
                return False
            prev = r["start"]
    if "key_change_count" in v:
        n = v["key_change_count"]
        if not (isinstance(n, int) and not isinstance(n, bool) and 0 <= n < RHYTHM_MAX_KEY_SECTIONS):
            return False
        if "key_sections" in v and n != max(0, len(v["key_sections"]) - 1):
            return False
    if "key_section_window" in v and not _num(v["key_section_window"], 0.1, 600):
        return False
    if "chords_grid" in v and v["chords_grid"] not in RHYTHM_CHORD_GRIDS:
        return False
    if "chords_reliability" in v and v["chords_reliability"] not in RHYTHM_CHORD_RELIABILITY:
        return False
    if "chord_vocabulary" in v and not _short_str(v["chord_vocabulary"], 200):
        return False
    if "chord_frame_segment_count" in v:
        n = v["chord_frame_segment_count"]
        if not (isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= RHYTHM_MAX_CHORDS * 10):
            return False
    return True


DTE_KEYS = {"method", "window_ms", "beat_onset_median", "mid_onset_median", "mid_to_beat_ratio",
            "mid_strong_fraction", "mid_strong_threshold", "pairs"}
DTE_WARNING = "double_tempo_evidence_unavailable:"
DTE_MIN_PAIRS, DTE_MIN_BEAT_MEDIAN = 7, 0.001  # the worker's gates: below either, ratio/fraction must be null


def _opt_num(v, lo, hi):
    return v is None or _num(v, lo, hi)


def _rhythm_v21_ok(v):
    """Fields added by worker 0.2.1 (double_tempo_evidence[_note]); absent in older files, valid when present.
    A null ratio (or fraction) is allowed only together with a double_tempo_evidence_unavailable warning; a numeric
    one needs pairs >= DTE_MIN_PAIRS and beat_onset_median >= DTE_MIN_BEAT_MEDIAN (the worker's gates)."""
    if "double_tempo_evidence_note" in v and not _short_str(v["double_tempo_evidence_note"], 200):
        return False
    if "double_tempo_evidence" not in v:
        return True
    d = v["double_tempo_evidence"]
    if not isinstance(d, dict) or set(d) != DTE_KEYS:
        return False
    n = d["pairs"]
    if not (_short_str(d["method"], 64) and _num(d["window_ms"], 1, 200) and _num(d["mid_strong_threshold"], 0, 1)
            and isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= RHYTHM_MAX_BEATS
            and _opt_num(d["beat_onset_median"], 0, 1) and _opt_num(d["mid_onset_median"], 0, 1)
            and _opt_num(d["mid_to_beat_ratio"], 0, 1000) and _opt_num(d["mid_strong_fraction"], 0, 1)):
        return False
    if (d["mid_to_beat_ratio"] is None) != (d["mid_strong_fraction"] is None):
        return False
    if d["mid_to_beat_ratio"] is None:
        return any(w.startswith(DTE_WARNING) for w in v["warnings"])
    return (n >= DTE_MIN_PAIRS and d["beat_onset_median"] is not None and d["mid_onset_median"] is not None
            and d["beat_onset_median"] >= DTE_MIN_BEAT_MEDIAN)


TC_KEYS = {"done": {"status", "model", "model_sha256", "bpm", "local_bpm_median", "local_bpm_min", "local_bpm_max",
                    "local_count", "top", "note"},
           "unavailable": {"status", "reason"}, "failed": {"status", "code"}}
TC_BPM = (30, 300)
TC_MAX_LOCAL = 10000  # worker 0.2.2: one patch per ~5.9 s, ~100 for 600 s
RE_CODE = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")  # e.g. sha256_mismatch
# worker 0.3.1: failed/inconsistent_output may carry a detail naming the failed check (exact key set per check)
TC_DETAIL_KEYS = {"class_count_mismatch": {"code", "row", "classes"},
                  "patch_count_mismatch": {"code", "local", "predictions"},
                  "argmax_mismatch": {"code", "patch", "local_bpm", "argmax_bpm", "prob_gap"},
                  "global_out_of_range": {"code", "bpm"}}


def _count(x, hi=TC_MAX_LOCAL):
    return isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= hi


def _tc_detail_ok(d):
    if not isinstance(d, dict) or d.get("code") not in TC_DETAIL_KEYS or set(d) != TC_DETAIL_KEYS[d["code"]]:
        return False
    c = d["code"]
    if c == "class_count_mismatch":
        return _count(d["row"]) and _count(d["classes"], 100000)
    if c == "patch_count_mismatch":
        return _count(d["local"]) and _count(d["predictions"])
    if c == "argmax_mismatch":
        return (_count(d["patch"]) and _num(d["local_bpm"], -1e6, 1e6) and _num(d["argmax_bpm"], *TC_BPM)
                and _opt_num(d["prob_gap"], 0, 1))
    return _num(d["bpm"], -1e6, 1e6)


def _rhythm_v22_ok(v):
    """tempo_cnn (worker 0.2.2); absent in older files, valid when present. Exact key set per status; a failed
    inconsistent_output may add `detail` (worker 0.3.1)."""
    if "tempo_cnn" not in v:
        return True
    d = v["tempo_cnn"]
    if not isinstance(d, dict) or d.get("status") not in TC_KEYS:
        return False
    if d["status"] == "failed" and "detail" in d:  # 0.3.1; older files have no detail and stay valid
        if d.get("code") != "inconsistent_output" or set(d) != {"status", "code", "detail"}:
            return False
        return _tc_detail_ok(d["detail"])
    keys = set(d)
    if d["status"] == "done" and "silent_patches" in d:  # worker 0.4.1: patches excluded as near-silent
        if not _count(d["silent_patches"]):
            return False
        keys.discard("silent_patches")
    if keys != TC_KEYS[d["status"]]:
        return False
    if d["status"] != "done":
        c = d["reason" if d["status"] == "unavailable" else "code"]
        return isinstance(c, str) and bool(RE_CODE.match(c))
    n, top = d["local_count"], d["top"]
    if not (_short_str(d["model"], 64) and isinstance(d["model_sha256"], str) and RE_HEX64.match(d["model_sha256"])
            and _short_str(d["note"], 200) and isinstance(n, int) and not isinstance(n, bool)
            and 1 <= n <= TC_MAX_LOCAL and isinstance(top, list) and 1 <= len(top) <= 3
            and all(_num(d[k], *TC_BPM) for k in ("bpm", "local_bpm_median", "local_bpm_min", "local_bpm_max"))
            and d["local_bpm_min"] <= d["local_bpm_median"] <= d["local_bpm_max"]):
        return False
    return all(isinstance(t, dict) and set(t) == {"bpm", "prob"} and _num(t["bpm"], *TC_BPM)
               and _num(t["prob"], 0, 1) for t in top)


ENERGY_DB = (-120, 0)  # dBFS: the worker's floor and digital full scale
ENERGY_MAX_VALUES = 601
SECTIONS_MAX = 40
RE_SECTION_LABEL = re.compile(r"\A[A-Z]{1,2}\Z")
SILENCE_LABEL = "silence"  # worker 0.4.1: a mostly silent section; takes no letter
ENERGY_SUMMARY_SILENCE_KEYS = {"silence_db", "silence_s"}  # worker 0.4.1
SPAN_KEYS = {"start", "end", "mean"}
SECTION_KEYS = {"start", "end", "label", "energy_mean", "top_chord", "similarity_to_first"}
SECTION_FUNCTIONS = frozenset(("start", "end", "intro", "outro", "break", "bridge", "inst", "solo", "verse", "chorus"))  # worker 0.11.0
SECTIONS_RELIABILITY = frozenset(("low_reference", "reference_candidate"))
ENERGY_SUMMARY_KEYS = {"unit", "window_s", "min", "max", "median", "peaks", "quietest"}


def _label_index(lab):
    """A=0 .. Z=25, AA=26 .. (the worker's order of first appearance)."""
    return ord(lab) - 65 if len(lab) == 1 else (ord(lab[0]) - 64) * 26 + ord(lab[1]) - 65


def _span_ok(d, dur, lo, hi):
    return (isinstance(d, dict) and set(d) == SPAN_KEYS and _num(d["start"], 0, dur) and _num(d["end"], 0, dur)
            and d["start"] < d["end"] and _num(d["mean"], lo, hi))


def _rhythm_v30_ok(v, dur):
    """Fields added by worker 0.3.0 (energy, energy_summary, sections, sections_method, sections_reliability); absent
    in older files, valid when present: finite, bounded, sorted, inside the duration, exact key sets, capped."""
    if "energy" in v:
        e = v["energy"]
        if not (isinstance(e, dict) and set(e) == {"unit", "step_s", "values"} and _short_str(e["unit"], 16)
                and _num(e["step_s"], 0.01, 60) and isinstance(e["values"], list)
                and 1 <= len(e["values"]) <= ENERGY_MAX_VALUES and all(_num(x, *ENERGY_DB) for x in e["values"])
                and (len(e["values"]) - 1) * e["step_s"] <= dur):
            return False
    if "energy_summary" in v:
        es = v["energy_summary"]
        if es is not None:
            # 0.4.1 adds silence_db/silence_s; peaks and quietest then skip silent windows and may be empty/null
            new = isinstance(es, dict) and set(es) == ENERGY_SUMMARY_KEYS | ENERGY_SUMMARY_SILENCE_KEYS
            if not (isinstance(es, dict) and (new or set(es) == ENERGY_SUMMARY_KEYS) and _short_str(es["unit"], 16)
                    and _num(es["window_s"], 0.01, 600) and all(_num(es[k], *ENERGY_DB) for k in ("min", "max", "median"))
                    and es["min"] <= es["median"] <= es["max"] and isinstance(es["peaks"], list)
                    and (0 if new else 1) <= len(es["peaks"]) <= 3):
                return False
            if new and not (_num(es["silence_db"], *ENERGY_DB) and _num(es["silence_s"], 0, dur)
                            and (es["quietest"] is None) == (not es["peaks"])):
                return False
            lo, hi = es["min"] - 0.01, es["max"] + 0.01
            if new:
                lo = max(lo, es["silence_db"] - 0.01)  # windows holding a value below the threshold are skipped
            if not (all(_span_ok(p, dur, lo, hi) for p in es["peaks"])
                    and ((new and es["quietest"] is None) or _span_ok(es["quietest"], dur, lo, hi))):
                return False
            pk = es["peaks"]
            if any(b["mean"] > a["mean"] for a, b in zip(pk, pk[1:])):  # loudest first
                return False
            for i, a in enumerate(pk):
                if any(a["start"] < b["end"] and b["start"] < a["end"] for b in pk[i + 1:]):  # non-overlapping
                    return False
    if "sections" in v:
        ss = v["sections"]
        if not isinstance(ss, list) or len(ss) > SECTIONS_MAX:
            return False
        prev_end, seen = 0, 0
        for r in ss:
            if not (isinstance(r, dict) and (set(r) == SECTION_KEYS or (set(r) == SECTION_KEYS | {"function"}
                                                                        and r["function"] in SECTION_FUNCTIONS))
                    and _num(r["start"], 0, dur)
                    and _num(r["end"], 0, dur) and r["start"] < r["end"] and r["start"] >= prev_end
                    and _num(r["energy_mean"], *ENERGY_DB)
                    and (r["top_chord"] is None or _short_str(r["top_chord"], 16))):
                return False
            if r["label"] == SILENCE_LABEL:  # 0.4.1: no letter, no similarity; letter order skips it
                if r["similarity_to_first"] is not None:
                    return False
                prev_end = r["end"]
                continue
            if not (isinstance(r["label"], str) and RE_SECTION_LABEL.match(r["label"])
                    and _num(r["similarity_to_first"], 0, 1)):
                return False
            k = _label_index(r["label"])
            if k > seen:  # labels are letters in order of first appearance: a new one is the next letter
                return False
            seen = max(seen, k + 1)
            prev_end = r["end"]
    if "sections_method" in v and not _short_str(v["sections_method"], 200):
        return False
    if "sections_reliability" in v and v["sections_reliability"] not in SECTIONS_RELIABILITY:
        return False
    return True


MELODY_MAX_NOTES = 6000  # worker cap: 600 s / PitchContourSegmentation's 0.1 s minimum note
MELODY_KEYS = {"status", "method", "reliability", "voiced_fraction", "voiced_s", "note_count", "notes_per_second",
               "range", "median_midi", "intervals", "scale_degrees", "sections"}
MELODY_RANGE_KEYS = ("low", "high", "p5", "p95")
MELODY_INTERVAL_KEYS = {"count", "repeat", "step", "leap", "up", "down", "max_gap_s"}
MELODY_DEGREE_KEYS = {"tonic", "scale", "weight", "bins"}
MELODY_SECTION_KEYS = {"label", "start", "end", "note_count", "low", "high", "median_midi", "notes_per_second", "peak",
                       "direction"}
MELODY_NOTE_KEYS = {"start", "end", "midi", "note"}
MELODY_DIRECTIONS = (None, "rising", "falling", "flat")
MELODY_DIR_NOTES = 4  # the worker's minimum notes for a section direction
MELODY_MAX_NPS = 1000  # notes per second of note time; stored rows last >= 0.001 s (ms rounding, end > start)
MELODY_NPS_TOL = 0.002  # recomputed from the stored rows; the worker rounds to 3 decimals
MELODY_RETRY_CODES = frozenset(("analysis_error", "dependency_missing"))  # environment-dependent, like tempo_cnn
BASS_MAX_NOTES = 7500  # worker cap: 600 s / the 80 ms shortest note
BASS_FAIL_REASONS = frozenset(("models_invalid", "decode_failed", "separation_failed", "rmvpe_failed", "onsets_failed"))
BASS_FINAL_REASONS = frozenset(("canonical_16k", "band_limited"))
OTHER_MAX_NOTES = 24000  # worker cap: 40 notes per second over 600 s
OTHER_FAIL_REASONS = frozenset(("models_invalid", "decode_failed", "separation_failed", "notes_failed"))
DRUM_MAX_HITS = 100000  # worker cap: 5 classes, one hit per 30 ms over 600 s
DRUM_CLASSES = ("kick", "snare", "tom", "hihat", "cymbal")
DRUM_FAIL_REASONS = frozenset(("models_invalid", "decode_failed", "separation_failed", "adtof_failed"))
SECTIONS_MODEL_FAIL_REASONS = frozenset(("models_invalid", "decode_failed", "separation_failed", "sections_failed", "too_many_sections"))
SEP_RETRY_REASONS = frozenset(("models_invalid", "decode_failed", "separation_failed", "rmvpe_failed"))
SEP_REASONS = SEP_RETRY_REASONS | {"not_configured", "canonical_16k", "band_limited"}
SING_RETRY_REASONS = frozenset(("models_invalid", "notes_failed"))  # timeout, no_time, too_many_notes and no_notes stay: same audio, same cost
SING_REASONS = SING_RETRY_REASONS | {"no_vocal", "canonical_16k", "timeout", "no_time", "too_many_notes", "no_notes"}


def _notes_per_second(notes):
    """The worker's definition: len(notes) / summed note duration; None without notes."""
    t = sum(n["end"] - n["start"] for n in notes)
    return len(notes) / t if notes and t > 0 else None


def _nps_matches(stored, notes):
    want = _notes_per_second(notes)
    return stored is None if want is None else stored is not None and abs(stored - want) <= MELODY_NPS_TOL
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(midi):
    return NOTE_NAMES[midi % 12] + str(midi // 12 - 1)


def _midi(x):
    return isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 127


def _pitch_ok(p):
    return isinstance(p, dict) and set(p) == {"midi", "note"} and _midi(p["midi"]) and p["note"] == note_name(p["midi"])


def _sum_ok(*xs):
    return 0.99 <= sum(xs) <= 1.01  # fractions rounded to 3 decimals


def _melody_done_ok(m, v, dur, notes):
    keys = set(m)
    if "note_count_raw" in m:  # worker 0.4.2: count before the vibrato/same-pitch merge, >= note_count
        r = m["note_count_raw"]
        if not (_count(r, MELODY_MAX_NOTES * 4) and _count(m.get("note_count"), MELODY_MAX_NOTES * 4)
                and r >= m["note_count"]):
            return False
        keys.discard("note_count_raw")
    if "separation" in m:  # worker 0.6.0: whether the notes come from a separated vocal, and why not
        sp = m["separation"]
        if sp not in ({"status": "done"},) and not (isinstance(sp, dict) and set(sp) == {"status", "reason"}
                                                     and sp["status"] == "skipped" and sp["reason"] in SEP_REASONS):
            return False
        keys.discard("separation")
    if "singing" in m:  # worker 0.21.0: whether the notes come from YourMT3+ singing notes, and why not
        sg = m["singing"]
        if sg != {"status": "done"} and not (isinstance(sg, dict) and set(sg) == {"status", "reason"}
                                             and sg["status"] == "skipped" and sg["reason"] in SING_REASONS):
            return False
        if sg == {"status": "done"} and m.get("separation") != {"status": "done"}:
            return False
        keys.discard("singing")
    if keys != MELODY_KEYS or m["reliability"] != "low_reference" or not _short_str(m["method"], 200):
        return False
    n = m["note_count"]
    if not (_num(m["voiced_fraction"], 0, 1) and _num(m["voiced_s"], 0, dur) and _count(n, MELODY_MAX_NOTES * 4)
            and _opt_num(m["notes_per_second"], 0, MELODY_MAX_NPS) and _opt_num(m["median_midi"], 0, 127)):
        return False
    if notes is not None and len(notes) != min(n, MELODY_MAX_NOTES):
        return False
    full = notes is not None and len(notes) == n  # not capped: densities are recomputed from the rows
    if full and not _nps_matches(m["notes_per_second"], notes):
        return False
    if n == 0:  # no note: every note statistic is null
        if any(m[k] is not None for k in ("notes_per_second", "range", "median_midi", "intervals", "scale_degrees")):
            return False
    else:
        r = m["range"]
        if not (isinstance(r, dict) and set(r) == set(MELODY_RANGE_KEYS) and all(_pitch_ok(r[k]) for k in r)
                and r["low"]["midi"] <= r["p5"]["midi"] <= r["p95"]["midi"] <= r["high"]["midi"]
                and _midi(m["median_midi"]) and r["p5"]["midi"] <= m["median_midi"] <= r["p95"]["midi"]
                and m["notes_per_second"] is not None):
            return False
    iv = m["intervals"]
    if iv is not None:
        if not (isinstance(iv, dict) and set(iv) == MELODY_INTERVAL_KEYS and _count(iv["count"], MELODY_MAX_NOTES * 4)
                and 1 <= iv["count"] < max(n, 1) and _num(iv["max_gap_s"], 0, 60)
                and all(_num(iv[k], 0, 1) for k in ("repeat", "step", "leap", "up", "down"))
                and _sum_ok(iv["repeat"], iv["step"], iv["leap"]) and _sum_ok(iv["repeat"], iv["up"], iv["down"])):
            return False
    sd = m["scale_degrees"]
    if sd is not None:
        if not (isinstance(sd, dict) and set(sd) == MELODY_DEGREE_KEYS and _short_str(sd["tonic"], 8)
                and _short_str(sd["scale"], 16) and sd["weight"] == "duration" and isinstance(sd["bins"], list)
                and len(sd["bins"]) == 12 and all(_num(b, 0, 1) for b in sd["bins"]) and _sum_ok(*sd["bins"])):
            return False
        if (sd["tonic"], sd["scale"]) != (v["key"]["tonic"], v["key"]["scale"]):
            return False
    ss = m["sections"]
    ref = v.get("sections")
    if not isinstance(ss, list) or len(ss) > SECTIONS_MAX:
        return False
    if ref is not None and [(r["label"], r["start"], r["end"]) for r in ss] != [
            (r["label"], r["start"], r["end"]) for r in ref]:  # aligned to the 0.3.0 sections
        return False
    total = 0
    for k, r in enumerate(ss):
        if not (isinstance(r, dict) and set(r) == MELODY_SECTION_KEYS and _count(r["note_count"], MELODY_MAX_NOTES * 4)
                and r["direction"] in MELODY_DIRECTIONS and _opt_num(r["notes_per_second"], 0, MELODY_MAX_NPS)):
            return False
        total += r["note_count"]
        if full:  # onset attribution: [start, end), the last section closed
            last = k == len(ss) - 1
            inside = [x for x in notes if r["start"] <= x["start"] < r["end"] or (last and x["start"] == r["end"])]
            if len(inside) != r["note_count"] or not _nps_matches(r["notes_per_second"], inside):
                return False
        if r["note_count"] == 0:
            if any(r[k] is not None for k in ("low", "high", "median_midi", "notes_per_second", "peak", "direction")):
                return False
            continue
        pk = r["peak"]
        if not (_midi(r["low"]) and _midi(r["high"]) and _midi(r["median_midi"])
                and r["low"] <= r["median_midi"] <= r["high"] and isinstance(pk, dict)
                and set(pk) == {"midi", "note", "time"} and _pitch_ok({"midi": pk["midi"], "note": pk["note"]})
                and pk["midi"] == r["high"] and _num(pk["time"], r["start"], r["end"])
                and (r["direction"] is None) == (r["note_count"] < MELODY_DIR_NOTES)):
            return False
    return not ss or total == n


def _rhythm_v40_ok(v, dur):
    """Fields added by worker 0.4.0 (melody summary + melody_notes rows); absent in older files, both or neither.
    failed: exactly {status, code}, no notes. done: exact key sets, ints for MIDI, names match, bounded, sorted,
    inside the duration, section rows aligned to `sections`, stored rows = min(note_count, cap)."""
    if ("melody" in v) != ("melody_notes" in v):
        return False
    if "melody" not in v:
        return True
    m, notes = v["melody"], v["melody_notes"]
    if not isinstance(notes, list) or len(notes) > MELODY_MAX_NOTES or not isinstance(m, dict):
        return False
    prev = 0
    for r in notes:
        if not (isinstance(r, dict) and set(r) == MELODY_NOTE_KEYS and _num(r["start"], 0, dur)
                and _num(r["end"], 0, dur) and r["start"] < r["end"] and r["start"] >= prev and _midi(r["midi"])
                and r["note"] == note_name(r["midi"])):
            return False
        prev = r["start"]
    if m.get("status") == "failed":
        return set(m) == {"status", "code"} and isinstance(m["code"], str) and bool(RE_CODE.match(m["code"])) \
            and notes == []
    return m.get("status") == "done" and _melody_done_ok(m, v, dur, notes)


SDEBUG_KEYS = {"kernel_beats", "min_gap_beats", "novelty_threshold", "group_threshold", "novelty", "peaks",
               "section_similarity"}
SDEBUG_MAX_PEAKS = 300  # the worker's SEC_DEBUG_PEAKS
SDEBUG_TOL = 1e-4  # novelty values are rounded to 4 decimals
LYRIC_KEYS = {"done": {"status", "method", "unit", "n", "blocks", "group_count", "coverage_s", "note"},
              "unavailable": {"status", "reason"}, "failed": {"status", "code"}}
LYRIC_UNITS = ("char", "word")
LYRIC_MAX_BLOCKS = 40
LYRIC_MIN_BLOCK = 4.0  # s; the worker drops shorter blocks
LYRIC_BLOCK_TOL = 0.002  # both block ends are rounded to ms
LYRIC_MAX_WORDS = 20000  # words passed to the worker (char-level Japanese ASR: ~1000-1200 for 3-5 min)
LYRIC_MAX_TEXT = 200
ML_RESULT_MAX = 16 * MiB  # ml result file read for its words
LYRIC_RETRY_REASONS = frozenset(("result_unreadable",))  # the result file could not be read: may work next time


def _structure_debug_ok(d, v, dur):
    """structure_debug (worker 0.4.3, stored file only): exact keys, bounded, novelty times sorted inside the
    duration with values in -1..1, every peak a novelty point, selected peaks = the section boundaries, similarity
    a symmetric 0..1 matrix with one row per section (or null)."""
    if d is None:
        return True
    if not (isinstance(d, dict) and set(d) == SDEBUG_KEYS and "sections" in v
            and _count(d["kernel_beats"], 256) and _count(d["min_gap_beats"], 256)
            and _num(d["novelty_threshold"], -1, 1) and _num(d["group_threshold"], 0, 1)):
        return False
    nv, pk, sim = d["novelty"], d["peaks"], d["section_similarity"]
    if not (isinstance(nv, dict) and set(nv) == {"times", "values"} and isinstance(nv["times"], list)
            and isinstance(nv["values"], list) and len(nv["times"]) == len(nv["values"]) <= RHYTHM_MAX_BEATS
            and all(_num(t, 0, dur) for t in nv["times"]) and all(_num(x, -1, 1) for x in nv["values"])
            and all(b >= a for a, b in zip(nv["times"], nv["times"][1:]))):
        return False
    points = set(zip(nv["times"], nv["values"]))
    if not isinstance(pk, list) or len(pk) > SDEBUG_MAX_PEAKS:
        return False
    prev = -1
    for p in pk:
        if not (isinstance(p, dict) and set(p) == {"time", "value", "selected"} and isinstance(p["selected"], bool)
                and _num(p["time"], 0, dur) and p["time"] > prev and (p["time"], p["value"]) in points
                and (not p["selected"] or p["value"] >= d["novelty_threshold"] - SDEBUG_TOL)):
            return False
        prev = p["time"]
    if [p["time"] for p in pk if p["selected"]] != [r["start"] for r in v["sections"][1:]]:
        return False
    if sim is None:
        return True
    n = len(v["sections"])
    return (isinstance(sim, list) and len(sim) == n
            and all(isinstance(r, list) and len(r) == n and all(_num(x, 0, 1) for x in r) for r in sim)
            and all(sim[a][b] == sim[b][a] for a in range(n) for b in range(a)))


def _lyric_repeats_ok(d, dur):
    """lyric_repeats (worker 0.4.3): exact key set per status; done blocks sorted, non-overlapping, inside the
    duration, >= 4 s, group ids in order of first appearance, coverage = summed block length."""
    if not isinstance(d, dict) or d.get("status") not in LYRIC_KEYS or set(d) != LYRIC_KEYS[d["status"]]:
        return False
    if d["status"] != "done":
        c = d["reason" if d["status"] == "unavailable" else "code"]
        return isinstance(c, str) and bool(RE_CODE.match(c))
    bl = d["blocks"]
    if not (_short_str(d["method"], 200) and _short_str(d["note"], 200) and d["unit"] in LYRIC_UNITS
            and _count(d["n"], 64) and d["n"] >= 1 and isinstance(bl, list) and len(bl) <= LYRIC_MAX_BLOCKS
            and _count(d["group_count"], LYRIC_MAX_BLOCKS) and _num(d["coverage_s"], 0, dur)):
        return False
    prev, seen, total = 0, 0, 0.0
    for b in bl:
        if not (isinstance(b, dict) and set(b) == {"start", "end", "group"} and _num(b["start"], 0, dur)
                and _num(b["end"], 0, dur) and b["start"] >= prev
                and b["end"] - b["start"] >= LYRIC_MIN_BLOCK - LYRIC_BLOCK_TOL
                and _count(b["group"], LYRIC_MAX_BLOCKS) and b["group"] <= seen):
            return False
        seen = max(seen, b["group"] + 1)
        prev = b["end"]
        total += b["end"] - b["start"]
    return d["group_count"] == seen and abs(d["coverage_s"] - total) <= LYRIC_BLOCK_TOL


def _rhythm_v43_ok(v, dur):
    """Fields added by worker 0.4.3 (structure_debug, lyric_repeats); absent in older files, valid when present."""
    if "structure_debug" in v and not _structure_debug_ok(v["structure_debug"], v, dur):
        return False
    return "lyric_repeats" not in v or _lyric_repeats_ok(v["lyric_repeats"], dur)


GENRE_KEYS = {"done": {"status", "model", "model_sha256", "embedding_model", "embedding_sha256", "reliability",
                       "patches", "silent_patches", "patch_hop_s", "window_s", "top", "parents", "windows", "note"},
              "unavailable": {"status", "reason"}, "failed": {"status", "code"}}
GENRE_SEP = "---"  # class names are "Parent---Style"
GENRE_MAX_PATCHES = 700  # worker 0.5.0: one patch per 0.992 s, 603 for 600 s
GENRE_MAX_WINDOWS = 21  # 30 s windows over <= 600.5 s
# unavailable reasons that cannot change for the same audio and worker; every other unavailable (a model file
# missing, unreadable or not matching its pin) and failed inference_error/dependency_missing are recomputed
GENRE_FINAL_REASONS = frozenset(("too_short", "no_audible_patches"))


def _genre_scores_ok(rows, key, n):
    """Up to n {key, score} rows, distinct names, scores 0..1 in non-increasing order."""
    if not (isinstance(rows, list) and len(rows) <= n):
        return False
    prev, seen = 1.0, set()
    for r in rows:
        if not (isinstance(r, dict) and set(r) == {key, "score"} and _short_str(r[key], 64) and r[key] not in seen
                and _num(r["score"], 0, prev)):
            return False
        if key == "style" and not (r[key].count(GENRE_SEP) == 1 and all(r[key].split(GENRE_SEP))):
            return False
        seen.add(r[key])
        prev = r["score"]
    return True


def _genre_ok(d, dur, duration):
    """genre (worker 0.5.0): exact key set per status; done: top 1..5 styles and 1..5 parents sorted by score, the
    best parent's score = the best style's (parent score = max over its styles) and no listed style above its
    listed parent, windows contiguous 0..duration with top 3 (empty exactly when the window had no audible patch),
    window patch counts summing to the audible patches."""
    if not isinstance(d, dict) or d.get("status") not in GENRE_KEYS or set(d) != GENRE_KEYS[d["status"]]:
        return False
    if d["status"] != "done":
        c = d["reason" if d["status"] == "unavailable" else "code"]
        return isinstance(c, str) and bool(RE_CODE.match(c))
    top, par, wins = d["top"], d["parents"], d["windows"]
    if not (_short_str(d["model"], 64) and _short_str(d["embedding_model"], 64) and _short_str(d["note"], 200)
            and all(isinstance(d[k], str) and RE_HEX64.match(d[k]) for k in ("model_sha256", "embedding_sha256"))
            and d["reliability"] == "model_estimate" and _count(d["patches"], GENRE_MAX_PATCHES)
            and _count(d["silent_patches"], GENRE_MAX_PATCHES) and d["silent_patches"] < d["patches"]
            and _num(d["patch_hop_s"], 0.01, 60) and _num(d["window_s"], 1, 600)
            and _genre_scores_ok(top, "style", 5) and _genre_scores_ok(par, "parent", 5) and top and par
            and par[0]["score"] == top[0]["score"]
            and isinstance(wins, list) and 1 <= len(wins) <= GENRE_MAX_WINDOWS):
        return False
    ps = {p["parent"]: p["score"] for p in par}
    if any(t["score"] > ps.get(t["style"].split(GENRE_SEP)[0], 1.0) for t in top):
        return False
    prev, used = 0.0, 0
    for w in wins:
        if not (isinstance(w, dict) and set(w) == {"start", "end", "patches", "top"} and w["start"] == prev
                and _num(w["end"], 0, dur) and w["end"] > w["start"] and _count(w["patches"], GENRE_MAX_PATCHES)
                and _genre_scores_ok(w["top"], "style", 3) and (len(w["top"]) == 0) == (w["patches"] == 0)):
            return False
        prev, used = w["end"], used + w["patches"]
    return used == d["patches"] - d["silent_patches"] and abs(prev - duration) <= 0.002


def _rhythm_v50_ok(v, dur):
    """genre (worker 0.5.0); absent in older files, valid when present."""
    return "genre" not in v or _genre_ok(v["genre"], dur, v["duration"])


def genre_view(g):
    """Summary form of a stored genre: windows only from /timeline layer=genre (window_count instead)."""
    if not isinstance(g, dict) or g.get("status") != "done":
        return g
    out = {k: x for k, x in g.items() if k != "windows"}
    out["window_count"] = len(g["windows"])
    return out


def lyric_words_payload(data, rid):
    """ml result file bytes (or None when missing) -> the --words-stdin object for the worker: {"words": [[text,
    start, end], ...]} or {"words": null, "reason": code}. Strict: any malformed word makes the whole list invalid."""
    if data is None:
        return {"words": None, "reason": "result_missing"}
    bad = {"words": None, "reason": "result_invalid"}
    if len(data) > ML_RESULT_MAX:
        return bad
    try:
        return _lyric_words_payload(data, rid)
    except (ValueError, TypeError, OverflowError, RecursionError):  # bad JSON, nesting too deep, huge numbers
        return bad


def _lyric_words_payload(data, rid):
    bad = {"words": None, "reason": "result_invalid"}
    r = loads_rhythm(data)
    if not isinstance(r, dict) or r.get("result_id") != rid:
        return bad
    w = r.get("words", [])
    if not isinstance(w, list) or len(w) > LYRIC_MAX_WORDS:
        return bad
    out = []
    for x in w:
        if not (isinstance(x, dict) and isinstance(x.get("text"), str) and len(x["text"]) <= LYRIC_MAX_TEXT
                and _num(x.get("start"), 0, 700) and _num(x.get("end"), 0, 700)):
            return bad
        out.append([x["text"], x["start"], x["end"]])
    return {"words": out}


def confidence_band(c):
    """Bands documented by Essentia BeatTrackerMultiFeature: [0,1) very low, [1,1.5] low, (1.5,3.5] good, >3.5."""
    if c < 1:
        return "very_low"
    if c <= 1.5:
        return "low"
    return "good" if c <= 3.5 else "excellent"


# tempo_cnn outcomes whose cause can change without a worker change (model file restored, transient TF error);
# deterministic ones (too_short, non_finite, inconsistent_output) stay cached
TEMPO_CNN_RETRY_CODES = frozenset(("inference_error", "dependency_missing"))


def _note_layer_ok(v, dur, key, cap, reasons):
    """An optional layer of note rows, summary and rows together."""
    rows_key = key + "_notes"
    if (key in v) != (rows_key in v):
        return False
    if key not in v:
        return True
    b, notes = v[key], v[rows_key]
    if not isinstance(notes, list) or len(notes) > cap or not isinstance(b, dict):
        return False
    prev = 0
    for r in notes:
        if not (isinstance(r, dict) and set(r) == MELODY_NOTE_KEYS and _num(r["start"], 0, dur)
                and _num(r["end"], 0, dur) and r["start"] < r["end"] and r["start"] >= prev and _midi(r["midi"])
                and r["note"] == note_name(r["midi"])):
            return False
        prev = r["start"]
    if b.get("status") == "skipped":
        return set(b) == {"status", "reason"} and b["reason"] in reasons and notes == []
    return (set(b) == {"status", "method", "reliability", "note_count"} and b["status"] == "done"
            and b["reliability"] == "low_reference" and _short_str(b["method"], 200) and b["note_count"] == len(notes))


def _rhythm_v80_ok(v, dur):
    """Worker 0.8.0: the bass layer."""
    return _note_layer_ok(v, dur, "bass", BASS_MAX_NOTES, BASS_FAIL_REASONS | BASS_FINAL_REASONS)


def _rhythm_v120_ok(v, dur):
    """Worker 0.12.0: the accompaniment layer."""
    return _note_layer_ok(v, dur, "other", OTHER_MAX_NOTES, OTHER_FAIL_REASONS | BASS_FINAL_REASONS | {"too_many_notes"})


BAR_MAX = 2000  # worker cap
BAR_ROW_KEYS = {"start", "end", "chord", "kick", "snare"}  # stored by worker 0.14.0
BAR_KEYS = {"status", "method", "reliability", "rows"}
BAR_KEYS_V220 = BAR_KEYS | {"beats_per_bar", "steps"}
BAR_KEYS_V230 = BAR_KEYS_V220 | {"extended_rows"}
BAR_ROW_KEYS_V150 = BAR_ROW_KEYS | {"scale", "degree"}
BAR_ROW_KEYS_V180 = BAR_ROW_KEYS_V150 | {"split"}
SCALE_NAMES = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"}
DEGREE_NAMES = {"1", "b2", "2", "b3", "3", "4", "b5", "5", "b6", "6", "b7", "7"}
PART_ROW_KEYS = {"start", "end", "label", "vocals", "bass", "drums", "bass_notes_per_s", "other_notes_per_s", "drum_hits_per_s"}
PART_RATE_MAX = 1000
SWING_HITS_MAX = 1000000


def _unit_rows_ok(rows, dur):
    """Contiguous rows inside the song, in time order."""
    return (isinstance(rows, list) and 0 < len(rows) <= BAR_MAX
            and all(isinstance(r, dict) and _num(r.get("start"), 0, dur) and _num(r.get("end"), 0, dur) and r["start"] < r["end"] for r in rows)
            and all(a["end"] == b["start"] for a, b in zip(rows, rows[1:])))


def _rhythm_v140_ok(v, dur):
    """Worker 0.14.0: downbeats and the bars, which exist only together. Worker 0.15.0: scale and degree in every row. Worker 0.18.0: split.
    Worker 0.22.0: beats_per_bar (3, 4 or null) and steps (12 for 3, else 16). Worker 0.23.0: extended_rows, the rows after the
    last downbeat (bars run on to the song end)."""
    d, bars = v["downbeats"], v.get("bars")
    if d.get("status") != "done":
        return bars is None and "count" not in d
    if not (set(d) == {"status", "method", "reliability", "count"} and d["reliability"] == "reference_candidate"
            and isinstance(bars, dict) and set(bars) in (BAR_KEYS, BAR_KEYS_V220, BAR_KEYS_V230) and bars["status"] == "done"
            and bars["reliability"] == "reference_candidate" and _short_str(bars["method"], 200)
            and _unit_rows_ok(bars["rows"], dur) and _int(bars.get("extended_rows", 0), 0, len(bars["rows"]) - 1)
            and d["count"] == len(bars["rows"]) - bars.get("extended_rows", 0) + 1):
        return False
    if set(bars) != BAR_KEYS and (bars["beats_per_bar"], bars["steps"]) not in ((3, 12), (4, 16), (None, 16)):
        return False
    last = bars.get("steps", 16) - 1
    keys = set(bars["rows"][0])
    if keys not in (BAR_ROW_KEYS, BAR_ROW_KEYS_V150, BAR_ROW_KEYS_V180):
        return False
    for r in bars["rows"]:
        if not (set(r) == keys and (r["chord"] is None or _short_str(r["chord"], 24))):
            return False
        if keys == BAR_ROW_KEYS_V180 and not (
                r["split"] is None or (r["chord"] is not None and isinstance(r["split"], list) and len(r["split"]) == 2
                                       and all(_short_str(x, 24) for x in r["split"]) and r["split"][0] != r["split"][1])):
            return False
        if keys != BAR_ROW_KEYS and not (
                (r["scale"] is None or r["scale"] in SCALE_NAMES) and (r["degree"] is None or r["degree"] in DEGREE_NAMES)
                and (r["degree"] is None or (r["scale"] is not None and r["chord"] is not None))):
            return False
        for c in ("kick", "snare"):
            s = r[c]
            if not (s is None or (isinstance(s, list) and all(_int(x, 0, last) for x in s) and s == sorted(set(s)))):
                return False
    return True


def _rhythm_v160_ok(v):
    """Worker 0.16.0: swing, which needs the bars. Absent in older results."""
    if "swing" not in v:
        return True
    s = v["swing"]
    if not (isinstance(s, dict) and set(s) == {"status", "method", "reliability", "off_beat_phase", "hits", "feel"}
            and "bars" in v and s["status"] == "done" and s["reliability"] == "reference_candidate" and _short_str(s["method"], 200)
            and _int(s["hits"], 0, SWING_HITS_MAX)):
        return False
    if s["off_beat_phase"] is None:
        return s["feel"] is None
    return _num(s["off_beat_phase"], 0.4, 0.8) and s["feel"] in ("straight", "swung")


MIX_ROW_KEYS = {"start", "end", "label", "balance", "width_db", "low_width_db", "mid_width_db", "high_width_db"}
POSITION_PARTS = ("vocals", "bass", "drums", "other")
STEREO_FAIL_REASONS = frozenset(("decode_failed", "mix_failed", "separation_failed"))
STEREO_REASONS = STEREO_FAIL_REASONS | BASS_FAIL_REASONS | BASS_FINAL_REASONS | {"mono_source"}


def _db_or_none(x, lo=-40, hi=40):
    return x is None or _num(x, lo, hi)


def _stereo_rows(rows, sections, keys):
    return (isinstance(rows, list) and isinstance(sections, list) and len(rows) == len(sections)
            and all(isinstance(r, dict) and set(r) == keys and all(r[k] == s[k] for k in ("start", "end", "label"))
                    for r, s in zip(rows, sections)))


def _rhythm_v170_ok(v):
    """Worker 0.17.0: mix and part_positions, one row per section. Absent in older results."""
    for key in ("mix", "part_positions"):
        f = v.get(key)
        if key in v and isinstance(f, dict) and f.get("status") == "skipped" and not (set(f) == {"status", "reason"} and f["reason"] in STEREO_REASONS):
            return False
    if "mix" in v:
        m = v["mix"]
        if not isinstance(m, dict):
            return False
        if m.get("status") != "skipped":
            if not (set(m) == {"status", "method", "reliability", "lufs", "loudness_range", "sections"} and m["status"] == "done"
                    and m["reliability"] == "reference_candidate" and _short_str(m["method"], 200)
                    and _db_or_none(m["lufs"], -200, 20) and _db_or_none(m["loudness_range"], 0, 200)
                    and _stereo_rows(m["sections"], v.get("sections"), MIX_ROW_KEYS)):
                return False
            for r in m["sections"]:
                if not ((r["balance"] is None or _num(r["balance"], -1, 1))
                        and all(_db_or_none(r[k]) for k in ("width_db", "low_width_db", "mid_width_db", "high_width_db"))):
                    return False
    if "part_positions" in v:
        p = v["part_positions"]
        if not isinstance(p, dict):
            return False
        if p.get("status") != "skipped":
            if not (set(p) == {"status", "method", "reliability", "threshold_db", "sections"} and p["status"] == "done"
                    and p["reliability"] == "low_reference" and _short_str(p["method"], 200) and _num(p["threshold_db"], -120, 0)
                    and _stereo_rows(p["sections"], v.get("sections"), {"start", "end", "label", *POSITION_PARTS})):
                return False
            for r in p["sections"]:
                for k in POSITION_PARTS:
                    x = r[k]
                    if x is not None and not (isinstance(x, dict) and set(x) == {"pan", "width_db", "level_db"} and _num(x["pan"], -1, 1)
                                              and _num(x["width_db"], -40, 40) and _num(x["level_db"], p["threshold_db"], 200)):
                        return False
    return True


TONE_ROW_KEYS = {"start", "end", "label", "vocals_brightness_hz", "drums_brightness_hz", "other_brightness_hz", "kick_weight_db", "snare_brightness_hz"}
TONE_FAIL_REASONS = STEREO_FAIL_REASONS | {"tone_failed"}


def _rhythm_v190_ok(v):
    """Worker 0.19.0: tone, one row per section. Absent in older results."""
    if "tone" not in v:
        return True
    t = v["tone"]
    if not isinstance(t, dict):
        return False
    if t.get("status") == "skipped":
        return set(t) == {"status", "reason"} and t["reason"] in STEREO_REASONS | TONE_FAIL_REASONS
    if not (set(t) == {"status", "method", "reliability", "sections"} and t["status"] == "done" and t["reliability"] == "low_reference"
            and _short_str(t["method"], 200) and _stereo_rows(t["sections"], v.get("sections"), TONE_ROW_KEYS)):
        return False
    return all(all(r[k] is None or _num(r[k], 20, 16000) for k in ("vocals_brightness_hz", "drums_brightness_hz", "other_brightness_hz", "snare_brightness_hz"))
               and (r["kick_weight_db"] is None or _num(r["kick_weight_db"], -310, 0)) for r in t["sections"])


def _rhythm_v130_ok(v):
    """Worker 0.13.0: part activity, one row per section."""
    if "parts" not in v:
        return True
    p = v["parts"]
    if not isinstance(p, dict):
        return False
    if p.get("status") == "skipped":
        return set(p) == {"status", "reason"} and p["reason"] in BASS_FAIL_REASONS | BASS_FINAL_REASONS | {"levels_failed"}
    if not (set(p) == {"status", "method", "reliability", "window_s", "threshold_db", "sections"} and p["status"] == "done"
            and p["reliability"] == "reference_candidate" and _short_str(p["method"], 200)
            and _num(p["window_s"], 0.1, 60) and _num(p["threshold_db"], -120, 0) and isinstance(p["sections"], list)):
        return False
    ss = v.get("sections")
    if not isinstance(ss, list) or len(ss) != len(p["sections"]):
        return False
    for r, s in zip(p["sections"], ss):
        if not (isinstance(r, dict) and set(r) == PART_ROW_KEYS and all(r[k] == s[k] for k in ("start", "end", "label"))
                and all(r[k] is None or _num(r[k], 0, 1) for k in ("vocals", "bass", "drums"))
                and all(r[k] is None or _num(r[k], 0, PART_RATE_MAX) for k in ("bass_notes_per_s", "other_notes_per_s", "drum_hits_per_s"))):
            return False
    return True


def _rhythm_v90_ok(v, dur):
    """Worker 0.9.0: the optional drum layer, summary and rows together."""
    if ("drums" in v) != ("drum_hits" in v):
        return False
    if "drums" not in v:
        return True
    d, hits = v["drums"], v["drum_hits"]
    if not isinstance(hits, list) or len(hits) > DRUM_MAX_HITS or not isinstance(d, dict):
        return False
    prev, counts = 0, dict.fromkeys(DRUM_CLASSES, 0)
    for r in hits:
        if not (isinstance(r, dict) and set(r) == {"time", "class"} and _num(r["time"], 0, dur) and r["time"] >= prev
                and r["class"] in counts):
            return False
        prev = r["time"]
        counts[r["class"]] += 1
    if d.get("status") == "skipped":
        return set(d) == {"status", "reason"} and d["reason"] in DRUM_FAIL_REASONS | BASS_FINAL_REASONS and hits == []
    return (set(d) == {"status", "method", "reliability", "hit_counts"} and d["status"] == "done"
            and d["reliability"] == "reference_candidate" and _short_str(d["method"], 200) and d["hit_counts"] == counts)


def _rhythm_v110_ok(v):
    """Worker 0.11.0: the optional record of the section model, and rows that carry a function only when it gave them."""
    m = v.get("sections_model")
    rows = v.get("sections") or []
    has_fn = any("function" in r for r in rows)
    if "sections_model" not in v:
        return not has_fn
    if not isinstance(m, dict):
        return False
    if m.get("status") == "done":
        return set(m) == {"status"} and bool(rows) and all("function" in r for r in rows)
    return (m.get("status") == "skipped" and set(m) == {"status", "reason"} and not has_fn
            and m["reason"] in SECTIONS_MODEL_FAIL_REASONS | BASS_FINAL_REASONS)


def _stem_can_improve(v, key, failures, separable):
    """With a stem layer configured and an upload that can give one: a stored result with no such layer, a failed one, or a
    16 kHz one. A 16 kHz upload never recomputes for this: it cannot give the layer and would replace a stored vocal melody."""
    if not separable:
        return False
    b = v.get(key)
    return not isinstance(b, dict) or b.get("reason") in failures or b.get("reason") == "canonical_16k"


def _bass_can_improve(v, separable):
    return _stem_can_improve(v, "bass", BASS_FAIL_REASONS, separable)


def _other_can_improve(v, separable):
    return _stem_can_improve(v, "other", OTHER_FAIL_REASONS, separable)


def _drums_can_improve(v, separable):
    return _stem_can_improve(v, "drums", DRUM_FAIL_REASONS, separable)


def _sections_can_improve(v, separable):
    return _stem_can_improve(v, "sections_model", SECTIONS_MODEL_FAIL_REASONS - {"too_many_sections"}, separable)  # same audio, same count


def _stereo_can_improve(v, separable):
    """A stored failed or 16 kHz mix or position field, when this upload can give it. Same audio: mono_source stays."""
    if not separable:
        return False
    for key in ("mix", "part_positions", "tone"):
        f = v.get(key)
        if isinstance(f, dict) and (f.get("reason") in TONE_FAIL_REASONS or f.get("reason") == "canonical_16k"):
            return True
    return False


def _has_separated_layer(v):
    """True when a stored result holds anything made from stems, which an upload without the band cannot give again."""
    m = v.get("melody")
    sp = m.get("separation") if isinstance(m, dict) else None
    return ((isinstance(sp, dict) and sp.get("status") == "done")
            or any(isinstance(v.get(k), dict) and v[k].get("status") == "done" for k in ("bass", "other", "drums", "sections_model", "parts", "mix", "part_positions", "tone")))


def _rhythm_retry(v):
    """True when a stored file with the current worker must still be recomputed: an optional stage (tempo_cnn or,
    worker 0.4.0+, melody, or 0.4.3 lyric_repeats, or 0.5.0 genre) ended in an outcome whose cause can change
    without a worker change."""
    m, ly, g = v.get("melody"), v.get("lyric_repeats"), v.get("genre")
    sp = m.get("separation") if isinstance(m, dict) else None
    return (_tempo_cnn_retry(v) or (isinstance(m, dict) and m.get("status") == "failed"
                                    and m.get("code") in MELODY_RETRY_CODES)
            or (isinstance(sp, dict) and sp.get("reason") in SEP_RETRY_REASONS)
            or (isinstance(ly, dict) and ly.get("status") == "unavailable" and ly.get("reason") in LYRIC_RETRY_REASONS)
            or (isinstance(g, dict) and ((g.get("status") == "unavailable" and g.get("reason") not in GENRE_FINAL_REASONS)
                                         or (g.get("status") == "failed" and g.get("code") in TEMPO_CNN_RETRY_CODES))))


def _melody_can_improve(v):
    """A stored melody that never had the chance of a separated vocal; this upload has it."""
    m = v.get("melody")
    sp = m.get("separation") if isinstance(m, dict) else None
    return isinstance(sp, dict) and sp.get("reason") in ("not_configured", "canonical_16k")


def _singing_can_improve(v, separable):
    """With the singing model configured and an upload that can give a vocal: a stored melody that never had it, or whose
    run failed in a way that can change (models_invalid, notes_failed). A 16 kHz upload never recomputes for this."""
    if not separable:
        return False
    m = v.get("melody")
    if not isinstance(m, dict) or m.get("status") != "done":
        return False
    sg = m.get("singing")
    return not isinstance(sg, dict) or sg.get("reason") in SING_RETRY_REASONS or sg.get("reason") == "canonical_16k"


def _tempo_cnn_retry(v):
    """True when a stored file with the current worker must still be recomputed for its optional tempo_cnn field."""
    t = v.get("tempo_cnn")
    if not isinstance(t, dict):
        return False
    return t.get("status") == "unavailable" or (t.get("status") == "failed" and t.get("code") in TEMPO_CNN_RETRY_CODES)


def bars_view(bars):
    """Summary form of stored bars: rows only from /timeline layer=bars (bar_count instead)."""
    if bars is None:
        return None
    return dict({k: x for k, x in bars.items() if k != "rows"}, bar_count=len(bars["rows"]))


QUICK_NOTE = ("rhythm=quick: no stem separation, so bass, other, drums, parts, bars, downbeats, swing, mix, part "
              "positions, tone and sung notes were not measured, sections come from the beat method and melody from "
              "the mix; a later rhythm=1 run on the full-band audio replaces this result")


def rhythm_summary(v, overview=None):
    """The summary below, plus profile + profile_note only on a rhythm=quick result (other bodies unchanged)."""
    out = _rhythm_summary(v, overview)
    if v.get("profile") == "quick":
        out.update(profile="quick", profile_note=QUICK_NOTE)
    return out


def _rhythm_summary(v, overview=None):
    """Compact view for analyze and /report; beats and chord rows stay in the stored file (/timeline).
    Fields from worker 0.2.0 (bpm_alternatives[_note], key_sections, key_change_count, chords_grid) and 0.2.1
    (double_tempo_evidence[_note]) and 0.2.2 (tempo_cnn) and 0.3.0 (energy_summary, sections, sections_method,
    sections_reliability) and 0.4.0 (melody) and 0.4.3 (lyric_repeats) and 0.5.0 (genre) are null when the stored
    file predates them. Energy values, melody notes and genre windows come only from /timeline; structure_debug
    stays in the stored file.
    `overview` is computed by the caller (App.rhythm_overview, read time, never stored); null before 0.4.3."""
    return {"status": "done", "bpm": v["bpm"], "bpm_confidence": v["bpm_confidence"],
            "bpm_confidence_band": confidence_band(v["bpm_confidence"]),
            "bpm_confidence_scale": v.get("bpm_confidence_scale"), "bpm_estimates": v.get("bpm_estimates", []),
            "bpm_alternatives": v.get("bpm_alternatives"), "bpm_alternatives_note": v.get("bpm_alternatives_note"),
            "double_tempo_evidence": v.get("double_tempo_evidence"),
            "double_tempo_evidence_note": v.get("double_tempo_evidence_note"), "tempo_cnn": v.get("tempo_cnn"),
            "key": v["key"], "key_sections": v.get("key_sections"), "key_change_count": v.get("key_change_count"),
            "beat_count": len(v["beats"]), "chord_segment_count": len(v["chords"]),
            "chords_grid": v.get("chords_grid"), "chords_reliability": v.get("chords_reliability"),
            "chord_vocabulary": v.get("chord_vocabulary"),
            "energy_summary": v.get("energy_summary"), "sections": v.get("sections"),
            "sections_method": v.get("sections_method"), "sections_reliability": v.get("sections_reliability"),
            "sections_model": v.get("sections_model"),
            "melody": v.get("melody"), "bass": v.get("bass"), "other": v.get("other"), "drums": v.get("drums"), "parts": v.get("parts"), "bars": bars_view(v.get("bars")), "swing": v.get("swing"), "mix": v.get("mix"), "part_positions": v.get("part_positions"), "tone": v.get("tone"), "lyric_repeats": v.get("lyric_repeats"), "overview": overview,
            "genre": genre_view(v.get("genre")),
            "downbeats": v["downbeats"],
            "duration": v["duration"], "tool": v["tool"],
            "warnings": v["warnings"]}


# ---------------------------------------------------------------- section overview (read time, never stored)
# Evidence (8 real J-pop songs, stored 0.4.3 structure_debug): sweeping the Foote threshold 0.04-0.20, audio
# boundaries agree with lyric_repeats block edges (+-5 s) only at the chance level implied by boundary density, so
# the lyric repeats are the backbone and the audio sections only split long gaps (or stand in when no repeats).
OV_MAX_SEGMENTS = 40
OV_MAX_GAP = 40.0  # s; a longer non-repeat piece is split at the audio section boundary nearest its middle
OV_MIN_PIECE = 4.0  # s; shorter non-repeat pieces join a neighbour (the lyric blocks' own minimum)
OV_SILENCE_RUN = 4.0  # s of consecutive 1 s windows below silence_db that cut a gap
OV_WORDLESS_RUN = 8.0  # s of consecutive 1 s windows touched by no ASR word that cut a gap (about 4 bars)
OV_VOCAL_FRACTION = 0.25  # share of a piece's 1 s windows touched by a word (or, without words, by a melody note)
OV_SILENCE_DB = -60.0  # the worker's SILENCE_DB, used when energy_summary carries no silence_db
OV_TOP_CHORDS = 3
OV_QUIET_DB = -12.0  # heuristic: a repeat this far below the song median is flagged quiet (likely not a chorus)
OV_LONG_REPEAT = 45.0  # s; heuristic: a longer repeat segment is cut at its best internal point (overview only)
OV_MIN_REPEAT_PART = 8.0  # s; both parts of such a cut, else no cut
OV_STRUCTURES = ("lyric", "audio_labels", "unknown")
OV_KEYS = {"status", "source", "structure", "method", "reliability", "segment_count", "repeat_groups",
           "dropped_repeats", "segments", "notes"}
OV_SEG_KEYS = {"start", "end", "kind", "group", "internal", "label", "energy", "quiet", "chords", "melody", "words"}
OV_KINDS = {"lyric": ("repeat", "vocal", "instrumental", "silence", "gap"), "audio": ("section", "silence")}
OV_VOCAL_BY = ("words", "melody", "none")
OV_METHOD_LYRIC = ("lyric_repeats blocks; gaps: silence if >half 1 s windows < silence_db, else vocal if >=25%% "
                   "windows touched by %s; cuts: silence >=4 s, wordless >=8 s, sections if >40 s; <4 s merged")
OV_METHOD = {"lyric": {b: OV_METHOD_LYRIC % {"words": "ASR words", "melody": "melody notes",
                                             "none": "none (gap)"}[b] for b in OV_VOCAL_BY},
             "audio": "audio sections (Foote novelty, chroma+MFCC); lyric repeats unavailable or empty",
             "audio_model": "audio sections (All-In-One); lyric repeats unavailable or empty"}
OV_NOTES = ("estimate: repeat = repeated ASR text (chorus/hook candidate; quiet = <=-12 dB rel), verse/pre-chorus "
            "not distinguished, depends on ASR; audio sections near chance vs lyric edges on 8 songs")
OV_NOTES_MODEL = ("estimate: repeat = repeated ASR text (chorus/hook candidate; quiet = <=-12 dB rel), verse/pre-chorus "
                  "not distinguished, depends on ASR; audio sections from All-In-One, see sections for their functions")
OV_NOTES_VOICE_UNKNOWN = ("estimate: repeat = repeated ASR text (possible chorus/hook; quiet <=-12 dB rel); "
                          "gap = voice unknown when only punctuation or symbols support a vocal label; "
                          "verse/pre-chorus not distinguished")


def _ov_wquantile(pairs, q=0.5):
    """The worker's duration-weighted quantile (_wquantile): smallest midi whose cumulative weight reaches q."""
    pairs = sorted(pairs)
    total, acc = sum(w for _, w in pairs), 0.0
    for m, w in pairs:
        acc += w
        if acc >= q * total - 1e-12:
            return m
    return pairs[-1][0]


def _ms(x):
    """Seconds -> integer milliseconds (the stored timestamp precision): durations and ties compare exactly, so
    18.4 - 14.4 is a 4 s gap, not 3.9999999999999982."""
    return int(round(x * 1000))


def _ov_len(s):
    return _ms(s["end"]) - _ms(s["start"])


def _ov_runs(flags, step, a, b, min_len):
    """Edges of runs of True windows (window i = [i*step, (i+1)*step)) clipped to [a, b], kept when >= min_len."""
    cuts, i, n = [], 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flags[j + 1]:
            j += 1
        s, e = max(a, i * step), min(b, (j + 1) * step)
        if _ms(e) - _ms(s) >= _ms(min_len):
            cuts += [s, e]
        i = j + 1
    return cuts


def _ov_coalesce(segs):
    """Adjacent non-repeat pieces of the same kind join while the joined piece stays <= 40 s (so a cut that only
    separated two pieces of one kind disappears, but a long-gap split at a section boundary stays)."""
    out = []
    for s in segs:
        p = out[-1] if out else None
        if (p is not None and p["kind"] == s["kind"] != "repeat" and p["label"] == s["label"]
                and _ms(s["end"]) - _ms(p["start"]) <= _ms(OV_MAX_GAP)):
            p["end"] = s["end"]
        else:
            out.append(dict(s))
    return out


class _Ov:
    """One overview computation over a validated stored rhythm dict and (optionally) the verified ASR words."""

    def __init__(self, v, words):
        self.v, self.dur = v, float(v["duration"])
        e = v.get("energy") or {"step_s": 1.0, "values": []}
        self.step, self.values = float(e["step_s"]), e["values"]
        es = v.get("energy_summary")
        self.sil_db = es["silence_db"] if isinstance(es, dict) and "silence_db" in es else OV_SILENCE_DB
        self.silent = [x < self.sil_db for x in self.values]
        m = v.get("melody")
        self.melody = m if isinstance(m, dict) and m.get("status") == "done" else None
        self.notes = sorted(((n["start"], n["end"], n["midi"]) for n in v.get("melody_notes", [])), key=lambda n: n[:2]) \
            if self.melody else []
        self.note_starts = [n[0] for n in self.notes]
        self.words = None if words is None else sorted((float(w[1]), float(w[2])) for w in words)
        self.word_starts = None if self.words is None else [w[0] for w in self.words]
        self.vocal_by = "words" if self.words is not None else "melody" if self.melody else "none"
        self.touched = self._touched([(max(s, 0.0), max(s, e)) for s, e in self.words] if self.words is not None
                                     else [(s, e) for s, e, _ in self.notes])
        self.lexical_spans = [
            (float(s), float(e)) for text, s, e in words
            if any(c.isalnum() for c in unicodedata.normalize("NFKC", text))
        ] if words is not None else None
        self.chords = sorted((c["start"], c["end"], c["label"]) for c in v["chords"])
        self.dropped = 0  # repeats turned into silence

    def _touched(self, spans):
        n, out = len(self.values), [False] * len(self.values)
        for s, e in spans:
            i0 = int(s // self.step)
            i1 = max(i0, int(math.ceil(e / self.step)) - 1) if e > s else i0
            for i in range(max(i0, 0), min(i1, n - 1) + 1):
                out[i] = True
        return out

    def windows(self, a, b):
        """Indices of energy windows whose midpoint lies in [a, b) ([a, b] for the piece ending the song)."""
        last = b >= self.dur - 1e-9
        return [i for i in range(len(self.values))
                if a <= (i + 0.5) * self.step < b or (last and (i + 0.5) * self.step == b)]

    # ---- segmentation
    def lyric(self, blocks):
        segs, t = [], 0.0
        for k, (s, e, g) in enumerate(blocks):
            if s > t + 1e-9:
                segs += self.gap(t, s)
            segs.append({"start": s, "end": e, "kind": "repeat", "group": g, "label": None, "block": k})
            t = e
        if self.dur > t + 1e-9:
            segs += self.gap(t, self.dur)
        segs = self.merge(segs)
        for x in segs:  # a mostly silent repeat (ASR text over a silent tail or fade) is no chorus candidate
            if x["kind"] == "repeat" and self.mostly_silent(x["start"], x["end"]):
                x["kind"], x["group"] = "silence", None
                self.dropped += 1
        return self.split_long_repeats(_ov_coalesce(segs))

    def split_long_repeats(self, segs):
        """Heuristic, overview only (lyric_repeats unchanged): a repeat segment > 45 s is cut at its best internal
        point, both parts >= 8 s and keeping kind, group and block; repeated while > 45 s and < 40 segments. Cut:
        the middle of the longest run of 1 s windows inside it touched by no word (ties earliest), else the middle
        of the largest gap in the union of the word spans reaching into it (ties earliest), else the audio section
        boundary nearest its middle; with no candidate meeting the 8 s rule (or no words for the first two), no cut."""
        out, todo = [], list(reversed(segs))
        while todo:
            x = todo.pop()
            c = self.repeat_cut(x) if x["kind"] == "repeat" and len(out) + len(todo) + 1 < OV_MAX_SEGMENTS else None
            if c is None:
                out.append(x)
                continue
            todo += [dict(x, start=c), dict(x, end=c)]
        return out

    def repeat_cut(self, x):
        a, b = _ms(x["start"]), _ms(x["end"])
        if b - a <= _ms(OV_LONG_REPEAT):
            return None
        lo, hi = a + _ms(OV_MIN_REPEAT_PART), b - _ms(OV_MIN_REPEAT_PART)

        def ok(t):
            return lo <= t <= hi
        step = _ms(self.step)
        if self.words is not None:
            best, i, n = None, 0, len(self.touched)  # (length, -start_ms) over word-free runs of whole windows
            while i < n:
                if self.touched[i] or i * step < a or (i + 1) * step > b:
                    i += 1
                    continue
                j = i
                while j + 1 < n and not self.touched[j + 1] and (j + 2) * step <= b:
                    j += 1
                mid = (i * step + (j + 1) * step) // 2
                if ok(mid) and (best is None or (j - i + 1, -i) > best[0]):
                    best = ((j - i + 1, -i), mid)
                i = j + 1
            if best is not None:
                return best[1] / 1000.0
            # gaps in the union of every word span reaching into the repeat (a word starting earlier counts too):
            # measured from the furthest end of all earlier words, so no cut falls inside a still-active word
            gaps, cov = [], None
            for s0, e0 in self.words:  # sorted by start
                ws, we = _ms(s0), max(_ms(s0), _ms(e0))
                if ws >= b or we < a:
                    continue
                if cov is not None and ws > cov:
                    gaps.append((ws - cov, -cov, (cov + ws) // 2))
                cov = we if cov is None else max(cov, we)
            gaps = [g for g in gaps if ok(g[2])]
            if gaps:
                return max(gaps)[2] / 1000.0  # largest, ties earliest
        bounds = [_ms(r["start"]) for r in (self.v.get("sections") or [])[1:]]
        inner = [t for t in bounds if ok(t)]
        if inner:
            return min(inner, key=lambda t: (abs(2 * t - a - b), t)) / 1000.0
        return None

    def mostly_silent(self, a, b):
        idx = self.windows(a, b)
        return bool(idx) and 2 * sum(self.silent[i] for i in idx) > len(idx)

    def gap(self, a, b):
        cuts = {a, b}
        cuts.update(_ov_runs(self.silent, self.step, a, b, OV_SILENCE_RUN))
        if self.words is not None:
            cuts.update(_ov_runs([not x for x in self.touched], self.step, a, b, OV_WORDLESS_RUN))
        pts = sorted(x for x in cuts if a <= x <= b)
        pieces = [(x, y) for x, y in zip(pts, pts[1:]) if _ms(y) > _ms(x)]
        bounds = sorted({r["start"] for r in (self.v.get("sections") or [])[1:]})
        out, todo = [], list(reversed(pieces))
        while todo:
            x, y = todo.pop()
            if _ms(y) - _ms(x) > _ms(OV_MAX_GAP):
                lo, hi = _ms(x) + _ms(OV_MIN_PIECE), _ms(y) - _ms(OV_MIN_PIECE)
                inner = [c for c in bounds if lo <= _ms(c) <= hi]
                if inner:
                    c = min(inner, key=lambda c: (abs(_ms(c) * 2 - _ms(x) - _ms(y)), c))
                    todo += [(c, y), (x, c)]
                    continue
            out.append({"start": x, "end": y, "kind": self.classify(x, y), "group": None, "label": None})
        return _ov_coalesce(out)

    def classify(self, a, b):
        if self.mostly_silent(a, b):
            return "silence"
        idx = self.windows(a, b)
        if self.vocal_by == "none":
            return "gap"
        frac = sum(self.touched[i] for i in idx) / len(idx) if idx else 0.0
        return "vocal" if frac >= OV_VOCAL_FRACTION else "instrumental"

    def merge(self, segs):
        """A non-repeat piece < 4 s joins its longer non-repeat neighbour (ties: the earlier one), else (only repeat
        neighbours) the previous segment (the next one at the song start), keeping the receiver's kind and group;
        shortest first, ties earliest. Then, while > 40 segments, the shortest non-repeat one joins by the same rule;
        finally same-kind neighbours coalesce (_ov_coalesce)."""
        def pick(limit):
            cand = [k for k, s in enumerate(segs) if s["kind"] != "repeat" and (limit is None or _ov_len(s) < limit)]
            return min(cand, key=lambda k: (_ov_len(segs[k]), k)) if cand else None

        while len(segs) > 1:
            k = pick(_ms(OV_MIN_PIECE)) if len(segs) <= OV_MAX_SEGMENTS else pick(None)
            if k is None:
                break
            nb = [j for j in (k - 1, k + 1) if 0 <= j < len(segs) and segs[j]["kind"] != "repeat"]
            if nb:
                j = max(nb, key=lambda j: (_ov_len(segs[j]), -j))
            else:
                j = k - 1 if k > 0 else k + 1
            r = segs[j]
            r["start"], r["end"] = min(r["start"], segs[k]["start"]), max(r["end"], segs[k]["end"])
            del segs[k]
        return _ov_coalesce(segs)

    def audio(self):
        ss = self.v["sections"]
        segs = []
        for k, r in enumerate(ss):
            sil = r["label"] == SILENCE_LABEL
            segs.append({"start": 0.0 if k == 0 else r["start"], "end": self.dur if k == len(ss) - 1 else ss[k + 1]["start"],
                         "kind": "silence" if sil else "section", "group": None, "label": r["label"]})
        return segs

    # ---- per segment measures
    def energy(self, a, b, kind):
        vals = [self.values[i] for i in self.windows(a, b)]
        loud = [x for x in vals if x >= self.sil_db]
        use = loud if kind != "silence" and loud else vals
        return round(sum(use) / len(use), 2) if use else None

    def chord_info(self, a, b):
        acc, first = {}, {}
        prev, changes = None, 0
        for s, e, lab in self.chords:
            if prev is not None and lab != prev and a < s < b:
                changes += 1
            prev = lab
            o = _ms(min(e, b)) - _ms(max(s, a))  # integer ms: equal stored durations tie exactly
            if o > 0:
                acc[lab] = acc.get(lab, 0) + o
                first.setdefault(lab, max(s, a))
        top = sorted(acc, key=lambda lab: (-acc[lab], first[lab], lab))[:OV_TOP_CHORDS]
        return {"top": [{"label": lab, "share": round(min(1.0, acc[lab] / (_ms(b) - _ms(a))), 3)} for lab in top],
                "changes_per_min": round(changes * 60.0 / (b - a), 2)}

    def _in(self, starts, a, b):
        lo = bisect.bisect_left(starts, a)
        hi = bisect.bisect_right(starts, b) if b >= self.dur - 1e-9 else bisect.bisect_left(starts, b)
        return lo, hi

    def melody_info(self, a, b):
        if not self.melody:
            return None
        lo, hi = self._in(self.note_starts, a, b)
        inside = self.notes[lo:hi]
        if not inside:
            return {"note_count": 0, "low": None, "high": None, "median_midi": None, "rel_semitones": None}
        med = _ov_wquantile([(m, max(e - s, 1e-6)) for s, e, m in inside])
        song = self.melody["median_midi"]
        return {"note_count": len(inside), "low": min(n[2] for n in inside), "high": max(n[2] for n in inside),
                "median_midi": med, "rel_semitones": None if song is None else med - song}

    def words_info(self, a, b):
        if self.word_starts is None:
            return None
        lo, hi = self._in(self.word_starts, a, b)
        return {"count": hi - lo}

    def build(self):
        ly = self.v["lyric_repeats"]
        blocks = []
        if ly.get("status") == "done":
            for b in ly["blocks"]:
                s, e = round(min(b["start"], self.dur), 3), round(min(b["end"], self.dur), 3)
                if e > s and (not blocks or s >= blocks[-1][1]):
                    blocks.append((s, e, b["group"]))
        model = (self.v.get("sections_model") or {}).get("status") == "done"
        if blocks:
            source, segs, method = "lyric", self.lyric(blocks), OV_METHOD["lyric"][self.vocal_by]
        elif self.v.get("sections"):
            source, segs, method = "audio", self.audio(), OV_METHOD["audio_model" if model else "audio"]
        else:
            return {"status": "failed", "code": "no_structure"}
        blocks_of = {}  # group -> its surviving (not dropped) blocks; one = internal (weaker cross-section evidence)
        for x in segs:
            if x["kind"] == "repeat":
                blocks_of.setdefault(x["group"], set()).add(x["block"])
        letters = {x["label"] for x in segs if x["kind"] == "section"}
        structure = "lyric" if source == "lyric" else "audio_labels" if len(letters) >= 2 else "unknown"
        loud = sorted(x for x in self.values if x >= self.sil_db)
        median = None
        if loud:
            h = len(loud) // 2
            median = loud[h] if len(loud) % 2 else (loud[h - 1] + loud[h]) / 2
        out = []
        voice_unknown = False
        for s in segs:
            a, b = s["start"], s["end"]
            kind = s["kind"]
            if kind == "vocal" and self.lexical_spans is not None:
                idx = self.windows(a, b)
                if (idx and not any(s0 < b and (s0 >= a or e0 > a) for s0, e0 in self.lexical_spans)
                        and sum(self.touched[i] for i in idx) / len(idx) >= OV_VOCAL_FRACTION):
                    kind = "gap"
                    voice_unknown = True
            mean = self.energy(a, b, s["kind"])
            rel = None if mean is None or median is None else round(mean - median, 2)
            out.append({"start": round(a, 3), "end": round(b, 3), "kind": kind, "group": s["group"],
                        "internal": s["kind"] == "repeat" and len(blocks_of[s["group"]]) == 1, "label": s["label"], "energy": {"mean_db": mean, "rel_db": rel, "rank": None},
                        "quiet": s["kind"] == "repeat" and rel is not None and rel <= OV_QUIET_DB,
                        "chords": self.chord_info(a, b), "melody": self.melody_info(a, b), "words": self.words_info(a, b)})
        ranked = sorted((k for k, s in enumerate(out) if s["kind"] != "silence" and s["energy"]["mean_db"] is not None),
                        key=lambda k: (-out[k]["energy"]["mean_db"], k))
        for r, k in enumerate(ranked, 1):
            out[k]["energy"]["rank"] = r
        secs = {}
        for s in out:
            if s["kind"] == "repeat" and not s["internal"]:
                secs[s["group"]] = secs.get(s["group"], 0) + _ms(s["end"]) - _ms(s["start"])
        return {"status": "done", "source": source, "structure": structure, "method": method,
                "reliability": "low_reference", "segment_count": len(out),
                "repeat_groups": [{"group": g, "count": len(blocks_of[g]), "seconds": secs[g] / 1000.0}
                                  for g in sorted(secs)],
                "dropped_repeats": self.dropped, "segments": out,
                "notes": OV_NOTES_VOICE_UNKNOWN if voice_unknown else (OV_NOTES_MODEL if model else OV_NOTES)}


def overview_words(payload, sha, v):
    """The ASR words for the overview: the --words-stdin payload bytes (App.lyric_words) when they are the words the
    stored lyric_repeats came from (same words_sha256), else None. Returns [[text, start, end], ...] or None."""
    if not isinstance(v.get("words_sha256"), str) or v["words_sha256"] != sha:
        return None
    w = json.loads(payload.decode("ascii")).get("words")
    return w if isinstance(w, list) else None


def rhythm_overview(v, words):
    """Section overview of a validated stored rhythm dict (null before worker 0.4.3). Pure and deterministic; any
    internal error or a result failing its own shape check gives {status: failed, code} and never raises."""
    if not isinstance(v, dict) or "lyric_repeats" not in v:
        return None
    try:
        o = _Ov(v, words).build()
        if not overview_ok(o, v["duration"]):
            return {"status": "failed", "code": "invalid_overview"}
        return o
    except Exception:  # noqa: BLE001  (the overview must never break /report or analyze)
        return {"status": "failed", "code": "internal_error"}


def _int(x, lo, hi):
    return isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi


def overview_ok(o, dur):
    """Strict shape of an overview: exact keys, kinds per source, contiguous 0..duration, <= 40 segments, groups only
    on repeats, labels only on audio segments, ranks a permutation over non-silent measured segments, bounded."""
    if not isinstance(o, dict) or o.get("status") not in ("done", "failed"):
        return False
    if o["status"] == "failed":
        return set(o) == {"status", "code"} and isinstance(o["code"], str) and bool(RE_CODE.match(o["code"]))
    if set(o) != OV_KEYS or o["source"] not in OV_KINDS or o["reliability"] != "low_reference":
        return False
    if o["structure"] not in OV_STRUCTURES or (o["structure"] == "lyric") != (o["source"] == "lyric"):
        return False
    if not (_short_str(o["method"], 200) and _short_str(o["notes"], 200)):
        return False
    segs = o["segments"]
    if not (isinstance(segs, list) and 1 <= len(segs) <= OV_MAX_SEGMENTS and o["segment_count"] == len(segs)):
        return False
    t, ranks, groups, internal = 0.0, [], {}, {}
    mel = [s.get("melody") is None for s in segs if isinstance(s, dict)]
    wrd = [s.get("words") is None for s in segs if isinstance(s, dict)]
    if len(set(mel)) > 1 or len(set(wrd)) > 1:  # melody / words present for every segment or none
        return False
    for s in segs:
        if not (isinstance(s, dict) and set(s) == OV_SEG_KEYS and s["kind"] in OV_KINDS[o["source"]]
                and _num(s["start"], 0, 601) and _num(s["end"], 0, 601) and s["start"] == t and s["end"] > s["start"]):
            return False
        t = s["end"]
        if (s["kind"] == "repeat") != _int(s["group"], 0, LYRIC_MAX_BLOCKS - 1) or (s["kind"] != "repeat"
                                                                                  and s["group"] is not None):
            return False
        if not isinstance(s["internal"], bool) or (s["internal"] and s["kind"] != "repeat"):
            return False
        if s["kind"] == "repeat" and internal.setdefault(s["group"], s["internal"]) is not s["internal"]:
            return False  # one flag per group
        if o["source"] == "audio":
            if not (s["label"] == SILENCE_LABEL if s["kind"] == "silence"
                    else isinstance(s["label"], str) and bool(RE_SECTION_LABEL.match(s["label"]))):
                return False
        elif s["label"] is not None:
            return False
        e = s["energy"]
        if not (isinstance(e, dict) and set(e) == {"mean_db", "rel_db", "rank"} and _opt_num(e["mean_db"], *ENERGY_DB)
                and _opt_num(e["rel_db"], -120, 120) and (e["mean_db"] is not None or e["rel_db"] is None)):
            return False
        if s["quiet"] is not (s["kind"] == "repeat" and e["rel_db"] is not None and e["rel_db"] <= OV_QUIET_DB):
            return False
        if s["kind"] == "silence" or e["mean_db"] is None:
            if e["rank"] is not None:
                return False
        elif not _int(e["rank"], 1, OV_MAX_SEGMENTS):
            return False
        else:
            ranks.append(e["rank"])
        c = s["chords"]
        if not (isinstance(c, dict) and set(c) == {"top", "changes_per_min"} and _num(c["changes_per_min"], 0, 1e6)
                and isinstance(c["top"], list) and len(c["top"]) <= OV_TOP_CHORDS
                and all(isinstance(x, dict) and set(x) == {"label", "share"} and _short_str(x["label"], 16)
                        and _num(x["share"], 0, 1) for x in c["top"])
                and len({x["label"] for x in c["top"]}) == len(c["top"])
                and all(b["share"] <= a["share"] for a, b in zip(c["top"], c["top"][1:]))):
            return False
        m = s["melody"]
        if m is not None:
            if not (isinstance(m, dict) and set(m) == {"note_count", "low", "high", "median_midi", "rel_semitones"}
                    and _count(m["note_count"], MELODY_MAX_NOTES)):
                return False
            if m["note_count"] == 0:
                if any(m[k] is not None for k in ("low", "high", "median_midi", "rel_semitones")):
                    return False
            elif not (_midi(m["low"]) and _midi(m["high"]) and _midi(m["median_midi"])
                      and m["low"] <= m["median_midi"] <= m["high"]
                      and (m["rel_semitones"] is None or _int(m["rel_semitones"], -127, 127))):
                return False
        w = s["words"]
        if w is not None and not (isinstance(w, dict) and set(w) == {"count"} and _count(w["count"], LYRIC_MAX_WORDS)):
            return False
        if s["kind"] == "repeat" and not s["internal"]:
            c0, t0 = groups.get(s["group"], (0, 0.0))
            groups[s["group"]] = (c0 + 1, t0 + s["end"] - s["start"])
    if not (_int(o["dropped_repeats"], 0, LYRIC_MAX_BLOCKS) and (o["source"] == "lyric" or o["dropped_repeats"] == 0)):
        return False
    if abs(t - round(dur, 3)) > 0.001 or sorted(ranks) != list(range(1, len(ranks) + 1)):
        return False
    if o["source"] == "audio":
        letters = {s["label"] for s in segs if s["kind"] == "section"}
        if o["structure"] != ("audio_labels" if len(letters) >= 2 else "unknown"):
            return False
    rg = o["repeat_groups"]  # groups with >= 2 surviving blocks (count = blocks <= its segments, a long block may be cut)
    return (isinstance(rg, list) and [x.get("group") if isinstance(x, dict) else None for x in rg] == sorted(groups)
            and all(set(x) == {"group", "count", "seconds"} and _int(x["count"], 2, groups[x["group"]][0])
                    and _num(x["seconds"], 0, 601) and abs(x["seconds"] - groups[x["group"]][1]) <= 0.002 for x in rg))


def rhythm_page(rid, v, layer, start, end, cursor, limit):
    """Native timeline rules: rows overlapping [start, end], cursor <= total, <= limit rows under the page byte cap."""
    if layer == "beats":
        rows = [{"time": t} for t in v["beats"] if start <= t <= end]
    elif layer == "energy":  # a value covers [time, time + step_s); rows whose time lies in [start, end]
        e = v["energy"]
        rows = [{"time": round(i * e["step_s"], 3), "value": x} for i, x in enumerate(e["values"])
                if start <= i * e["step_s"] <= end]
    elif layer == "drums":  # hits whose time lies in [start, end]
        rows = [h for h in v["drum_hits"] if start <= h["time"] <= end]
    elif layer == "bars":  # bars overlapping [start, end]
        rows = [r for r in v["bars"]["rows"] if r["end"] >= start and r["start"] <= end]
    elif layer == "genre":  # 30 s windows overlapping [start, end]; none unless the genre status is done
        g = v["genre"]
        rows = [w for w in g.get("windows", []) if w["end"] >= start and w["start"] <= end]
    else:
        src = {"melody": "melody_notes", "bass": "bass_notes", "other": "other_notes"}.get(layer, "chords")
        src = v[src]
        rows = [c for c in src if c["end"] >= start and c["start"] <= end]
    if cursor > len(rows):
        raise HttpError("invalid_argument")
    page, size = [], 2
    for r in rows[cursor:cursor + limit]:
        size += len(json.dumps(r, separators=(",", ":"))) + 1
        if size > MAX_WIRE_PAGE:
            break
        page.append(r)
    nxt = cursor + len(page)
    out = {"result_id": rid, "clock": "seconds_from_audio_start", "range": {"start": start, "end": end},
           "cursor": cursor, "total": len(rows), "layer": layer, layer: page, "layer_status": "done",
           "warnings": v["warnings"], "truncated": nxt < len(rows), "continuation": nxt if nxt < len(rows) else None}
    if layer == "chords":
        out["reliability"] = v.get("chords_reliability", "low_reference")
    elif layer == "energy":
        out["unit"], out["step_s"] = v["energy"]["unit"], v["energy"]["step_s"]
    elif layer == "bass":
        out["layer_status"] = v["bass"]["status"]
        out["reliability"] = "low_reference"
    elif layer == "other":
        out["layer_status"] = v["other"]["status"]
        out["reliability"] = "low_reference"
    elif layer == "drums":
        out["layer_status"] = v["drums"]["status"]
        out["reliability"] = "reference_candidate"
    elif layer == "bars":
        out["layer_status"] = v["bars"]["status"]
        out["reliability"] = v["bars"]["reliability"]
    elif layer == "melody":
        out["layer_status"] = v["melody"]["status"]
        out["reliability"] = "low_reference"
    elif layer == "genre":
        out["layer_status"] = v["genre"]["status"]
        out["reliability"] = "model_estimate"
    return out


# ---------------------------------------------------------------- application core

class Job:
    def __init__(self, client, rid, op, kind, src, language, emotion_mode, name, upload_sha256):
        self.client, self.rid, self.op, self.kind, self.src = client, rid, op, kind, src
        self.language, self.emotion_mode, self.name, self.upload_sha256 = language, emotion_mode, name, upload_sha256
        self.linked = False
        self.ml_started = False  # set just before `ml analyze` is spawned
        self.rhythm = False  # opt-in rhythm layer (POST /analyze?rhythm=1)
        self.quick = False  # rhythm=quick: the worker without stems (never replaces a stored result with a stem-made layer)
        self.deadline = None  # monotonic time by which the waiting handler gives up (handler_total)
        self.done = threading.Event()
        self.status, self.body, self.raw = 500, None, None


class Engine:
    """The analyze pipeline and the rhythm layer without TLS, network, nonces or receipts. App (the LAN server)
    builds on it; local_cli.py uses it directly on this Mac (same OS user, like the `ml` wrapper)."""

    def __init__(self, cfg, log_name, clock=time.time):
        """For local_cli.py. App sets the same attributes in its own __init__ (TLS, journals, gate)."""
        self.cfg, self.limits, self.clock = cfg, cfg["limits"], clock
        root = cfg["root"]
        self.uploads, self.logs = root + "/uploads", root + "/logs"
        for d in (self.uploads, self.logs):
            if not os.path.isdir(d):
                os.mkdir(d, 0o700)
            st = os.lstat(d)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise ConfigError(d + " must be a 0700 directory owned by this user")
        if os.stat(self.uploads).st_dev != os.stat(cfg["inbox"]).st_dev:
            raise ConfigError("uploads and inbox must be on the same filesystem (hard-link promotion)")
        self.log = JsonLog(self.logs + "/" + log_name, self.limits["log_bytes"], self.limits["log_files"])
        self.stopping = False
        self._rhythm_init()

    def _rhythm_init(self):
        root = self.cfg["root"]
        self.rhythm_dir = root + "/" + RHYTHM_DIR
        self.team_lock = root + "/.analyze.lock"  # the same flock the `ml` wrapper takes for analyze
        self.rhythm_worker_sha256 = None  # identity of the configured worker file, hashed once at start
        if self.cfg.get("rhythm"):
            self._rhythm_dir_ok(create=True)
            self.rhythm_worker_sha256 = file_sha256(self.cfg["rhythm"]["worker"])
            if "allin1_model" in self.cfg["rhythm"]:
                self.sections_cache = root + "/" + SECTIONS_CACHE_DIR
                self._rhythm_dir_ok(create=True, d=self.sections_cache)

    # rhythm layer
    def _rhythm_dir_ok(self, create=False, d=None):
        d = d or self.rhythm_dir
        if create and not os.path.lexists(d):
            os.mkdir(d, 0o700)
        st = os.lstat(d)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ConfigError(d + " must be a 0700 directory owned by this user")

    def rhythm_path(self, rid):
        return self.rhythm_dir + "/" + rid + ".json"

    def load_rhythm(self, rid):
        """Returns (state, value): ("missing", None), ("invalid", None) or ("ok", dict)."""
        if not RE_HEX64.match(rid or ""):
            return "missing", None
        try:
            fd = os.open(self.rhythm_path(rid), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return "missing", None
        except OSError:
            return "invalid", None
        try:
            with os.fdopen(fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    return "invalid", None
                data = f.read(self.limits["rhythm_output"] + 1)
        except OSError:
            return "invalid", None
        if len(data) > self.limits["rhythm_output"]:
            return "invalid", None
        try:
            v = check_rhythm(loads_rhythm(data))
        except ValueError:
            v = None
        return ("ok", v) if v is not None else ("invalid", None)

    def store_rhythm(self, rid, v):
        """Atomic durable write: tmp (0600, O_EXCL) + full fsync, replace, directory fsync."""
        self._rhythm_dir_ok(create=True)
        path = self.rhythm_path(rid)
        tmp = "%s.%s.tmp" % (path, secrets.token_hex(8))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            try:
                write_all(fd, json.dumps(v, separators=(",", ":")).encode("utf-8"))
                full_fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        fsync_dir(self.rhythm_dir)

    def rhythm_status(self):
        rh = self.cfg.get("rhythm")
        if not rh:
            return None
        return {"configured": True, "python_present": os.path.isfile(rh["python"]),
                "worker_present": os.path.isfile(rh["worker"]), "worker_sha256": self.rhythm_worker_sha256,
                "separation_configured": "demucs" in rh, "chord_model_configured": "chord_model" in rh,
                "bass_configured": "basic_pitch_python" in rh, "drums_configured": "drum_model" in rh,
                "sections_configured": "allin1_model" in rh, "singing_configured": "yourmt3_python" in rh,
                "earcheck_available": os.path.isfile(EAR_CHECK)}

    def lyric_words(self, rid):
        """(--words-stdin bytes, their SHA-256) from the ml result file <root>/results/<rid>.json (read-only)."""
        invalid = {"words": None, "reason": "result_invalid"}
        try:
            fd = os.open(self.cfg["root"] + "/results/" + rid + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            fd, payload = None, lyric_words_payload(None, rid)
        except OSError as e:  # a symlink (O_NOFOLLOW) is refused for good; other errors may pass on a later run
            fd, payload = None, invalid if e.errno == errno.ELOOP else {"words": None, "reason": "result_unreadable"}
        if fd is not None:
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):  # e.g. a directory or FIFO
                    os.close(fd)
                    payload = invalid
                else:
                    with os.fdopen(fd, "rb") as f:
                        payload = lyric_words_payload(f.read(ML_RESULT_MAX + 1), rid)
            except OSError:
                payload = {"words": None, "reason": "result_unreadable"}
        b = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return b, hashlib.sha256(b).hexdigest()

    def read_next(self, rid):
        """Where an LLM goes after this summary; only for a stored result (the routes read the stored file)."""
        out = {"sheet": "GET /sheet/%s" % rid,
               "zoom": "GET /sheet/%s?start=<s>&end=<s> (at most %.0f s, bar by bar)" % (rid, sheet.ZOOM_MAX)}
        if self.cfg.get("rhythm") and os.path.isfile(EAR_CHECK):  # the same condition as POST /earcheck
            out["earcheck"] = ("POST /earcheck/%s?start=<s>&end=<s>, body = the same audio again (at most %.0f s; "
                               "left ear the original, right ear a synth of this result)" % (rid, EARCHECK_MAX_SPAN))
        out["note"] = "the sheet is a compact text reading of this summary for an LLM; read it first, then zoom or drill down"
        return out

    def rhythm_overview(self, rid, v, words=None, words_sha=None):
        """overview for the summary: computed at read time from the stored dict plus, for its word counts, the ASR
        words of <root>/results/<rid>.json when they still hash to the stored words_sha256. Never raises."""
        if not isinstance(v, dict) or "lyric_repeats" not in v:
            return None
        try:
            if words is None:
                words, words_sha = self.lyric_words(rid)
            w = overview_words(words, words_sha, v)
        except Exception:  # noqa: BLE001
            return {"status": "failed", "code": "internal_error"}
        return rhythm_overview(v, w)

    def run_rhythm(self, job, result_id, wav):
        """Rhythm layer after a successful analyze. Never raises; failure never fails the analyze."""
        if not result_id:
            return {"status": "failed", "code": "no_result_id"}
        state, stored = self.load_rhythm(result_id)
        cur = self.rhythm_worker_sha256
        words, words_sha = self.lyric_words(result_id)
        # reuse needs the same worker file and the same ASR words (a re-run ASR can change them for this result_id)
        separable = "demucs" in self.cfg["rhythm"] and not (job.kind == "wav" and strict_wav_check(job.src))
        quick = job.quick and separable  # stems were possible and are skipped: the stored file says profile quick
        separable = separable and not job.quick  # quick runs like a 16 kHz upload: no stems, and no stem replaced
        # a 16 kHz upload must not replace stems with nothing, whatever else would recompute: it serves the stored file as it is
        if state == "ok" and "demucs" in self.cfg["rhythm"] and not separable and _has_separated_layer(stored):
            return dict(rhythm_summary(stored, self.rhythm_overview(result_id, stored, words, words_sha)),
                        read_next=self.read_next(result_id), cache_hit=True, stored=True)
        if (state == "ok" and cur is not None and stored.get("worker_sha256") == cur
                and stored.get("words_sha256") == words_sha and not _rhythm_retry(stored)
                and not (separable and _melody_can_improve(stored))
                and not (separable and (stored.get("parts") or {}).get("reason") == "levels_failed")
                and not _stereo_can_improve(stored, separable)
                and not ("chord_model" in self.cfg["rhythm"] and stored.get("chords_reliability") != "reference_candidate")
                and not ("basic_pitch_python" in self.cfg["rhythm"] and (_bass_can_improve(stored, separable)
                                                                         or _other_can_improve(stored, separable)))
                and not ("drum_model" in self.cfg["rhythm"] and _drums_can_improve(stored, separable))
                and not ("allin1_model" in self.cfg["rhythm"] and _sections_can_improve(stored, separable))
                and not ("yourmt3_python" in self.cfg["rhythm"] and _singing_can_improve(stored, separable))):
            return dict(rhythm_summary(stored, self.rhythm_overview(result_id, stored, words, words_sha)),
                        read_next=self.read_next(result_id), cache_hit=True, stored=True)
        # absent (worker 0.1.0/0.2.0 files), different or unverifiable worker identity: recompute; the stored file
        # is replaced only after a successful run, so /report and /timeline keep the old one until then
        if self.stopping:
            return {"status": "failed", "code": "shutting_down"}
        timeout = self.limits["rhythm_timeout"]
        if job.deadline is not None:
            timeout = min(timeout, job.deadline - time.monotonic() - 5.0)
        if timeout < 1.0:
            return {"status": "timeout", "code": "no_time_left"}
        rh = self.cfg["rhythm"]
        try:
            lfd = os.open(self.team_lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError:
            return {"status": "failed", "code": "lock_unavailable"}
        try:
            try:
                fcntl.flock(lfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy", "code": "busy"}
            except OSError:
                return {"status": "failed", "code": "lock_unavailable"}
            res = self._spawn_rhythm(rh, wav, lfd, timeout, words, job, separable)
        finally:
            os.close(lfd)  # the worker shares this lock while it lives; released when both have closed it
        self.log.event("rhythm_exit", id=job.rid, status=res.get("status"), code=res.get("code"))
        if res.get("status") != "done":
            return res
        data = res.pop("_data")
        if cur is not None:
            data["worker_sha256"] = cur
        data["words_sha256"] = words_sha
        if quick:
            data["profile"] = "quick"
        try:
            self.store_rhythm(result_id, data)
            stored = True
        except OSError:
            self.log.event("rhythm_store_failed", id=job.rid)
            stored = False
        out = dict(rhythm_summary(data, self.rhythm_overview(result_id, data, words, words_sha)), cache_hit=False,
                   stored=stored)
        if stored:
            out["read_next"] = self.read_next(result_id)
        return out

    def _spawn_rhythm(self, rh, wav, lock_fd, timeout, words, job, separable):
        lim = self.limits
        with tempfile.TemporaryFile() as inp, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            inp.write(words)  # the ASR words for lyric_repeats, on stdin (no named file)
            inp.flush()
            inp.seek(0)
            try:
                sep = []
                if "demucs" in rh:
                    sep = ["--demucs", rh["demucs"], "--rmvpe-model", rh["rmvpe"],
                           "--melody-scratch", self.uploads + "/" + job.op + "/" + SEP_DIR]
                    if separable:
                        sep += ["--melody-source", job.src]
                    if "basic_pitch_python" in rh:
                        sep += ["--basic-pitch-python", rh["basic_pitch_python"]]
                    if "drum_model" in rh:
                        sep += ["--drum-model", rh["drum_model"]]
                    if "allin1_model" in rh:
                        sep += ["--sections-python", rh["allin1_python"], "--sections-model", rh["allin1_model"],
                                "--sections-cache", self.sections_cache]
                    if "yourmt3_python" in rh:
                        sep += ["--singing-python", rh["yourmt3_python"]]
                if "chord_model" in rh:
                    sep += ["--chord-model", rh["chord_model"]]
                p = self.popen([rh["python"], "-I", rh["worker"], "--words-stdin", "--time-budget", "%.0f" % max(timeout, 1.0)]
                               + sep + [wav], stdin=inp, stdout=out,
                               stderr=err, env=self.child_env(), cwd="/", close_fds=True, pass_fds=(lock_fd,),
                               shell=False, start_new_session=True)
            except OSError:
                return {"status": "failed", "code": "server_misconfigured"}
            try:
                p.wait(timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                p.wait()
                return {"status": "timeout", "code": "timeout"}
            if p.returncode != 0:
                code = last_stderr_code(err, lim["stderr_line"])
                return {"status": "failed", "code": code if re.match(r"\A[a-z_]{1,64}\Z", code) else "internal_error"}
            err.seek(0)
            for line in err.read(16 * 1024).decode("utf-8", "replace").splitlines():  # e.g. the singing full-decode fallback
                if line.startswith("sing_notes: "):
                    self.log.event("rhythm_note", id=job.rid, line=line[:200])
            if os.fstat(out.fileno()).st_size > lim["rhythm_output"]:
                return {"status": "failed", "code": "invalid_output"}
            out.seek(0)
            try:
                v = check_rhythm(loads_rhythm(out.read()))
            except ValueError:
                v = None
            if v is None:
                return {"status": "failed", "code": "invalid_output"}
            for k in ("status", "cache_hit", "stored", "code", "worker_sha256", "words_sha256", "profile"):  # server-owned
                v.pop(k, None)
            return {"status": "done", "_data": v}

    def attach_rhythm(self, job, body, result_id):
        """body["rhythm"] after a successful analyze (the rhythm layer is an add-on on the same WAV)."""
        try:
            body["rhythm"] = self.run_rhythm(job, result_id, self.cfg["inbox"] + "/api-" + job.op + ".wav")
        except Exception as e:
            self.log.event("rhythm_error", id=job.rid, kind=type(e).__name__)
            body["rhythm"] = {"status": "failed", "code": "internal_error"}

    # reads (GET /report, /timeline, /sheet); raise HttpError
    def report_extra(self, rid):
        """The ("rhythm", summary) merged into `ml report`, or None when nothing is stored."""
        st, v = self.load_rhythm(rid)
        extra = None
        if st == "ok":
            extra = ("rhythm", dict(rhythm_summary(v, self.rhythm_overview(rid, v)), read_next=self.read_next(rid)))
        elif st == "invalid":
            extra = ("rhythm", {"status": "failed", "code": "invalid_result"})
        return extra

    def read_ml(self, args, extra=None):
        """A read-only `ml` call: (native bytes, None), or (None, dict) with extra merged in."""
        ok, data, code, detail = self.run_ml(args, self.limits["read_timeout"])
        if not ok:
            raise HttpError(code, detail=detail)
        if extra is None or extra[1] is None:
            return data, None  # native JSON bytes unchanged
        v = json.loads(data.decode("utf-8"))  # run_ml already checked it is a JSON object
        v[extra[0]] = extra[1]
        return None, v

    def timeline(self, rid, q):
        """("page", body) for a rhythm layer, or ("ml", argv) for the native words/emotion timeline."""
        args = ["timeline", rid]
        if q.get("layer") in RHYTHM_LAYERS:
            return "page", self.rhythm_timeline(rid, q)
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
        return "ml", args

    def rhythm_timeline(self, rid, q):
        """layer=beats|chords|energy|melody|genre|bass|other|drums|bars, served from the stored rhythm file with the native range/limit/cursor rules."""
        vals = {}
        for k, default in (("start", 0.0), ("end", 600.0)):
            if k in q:
                if not RE_NUM.match(q[k]):
                    raise HttpError("bad_request")
                vals[k] = float(q[k])
                if not 0 <= vals[k] <= 600:
                    raise HttpError("invalid_argument")
            else:
                vals[k] = default
        if vals["end"] < vals["start"]:
            raise HttpError("invalid_argument")
        limit = 100
        if "limit" in q:
            if not RE_LIMIT.match(q["limit"]) or not 1 <= int(q["limit"]) <= 200:
                raise HttpError("bad_request")
            limit = int(q["limit"])
        cursor = 0
        if "cursor" in q:
            if not RE_CURSOR.match(q["cursor"]):
                raise HttpError("bad_request")
            cursor = int(q["cursor"])
        st, v = self.load_rhythm(rid)
        if st == "missing":
            raise HttpError("rhythm_not_found")
        if st != "ok":
            raise HttpError("invalid_result")
        if q["layer"] == "energy" and "energy" not in v:  # stored before worker 0.3.0: that layer is not stored
            raise HttpError("rhythm_not_found")
        if q["layer"] == "melody" and "melody_notes" not in v:  # stored before worker 0.4.0
            raise HttpError("rhythm_not_found")
        if q["layer"] == "genre" and "genre" not in v:  # stored before worker 0.5.0
            raise HttpError("rhythm_not_found")
        if q["layer"] == "bass" and "bass_notes" not in v:  # not configured, or stored before worker 0.8.0
            raise HttpError("rhythm_not_found")
        if q["layer"] == "other" and "other_notes" not in v:  # not configured, or stored before worker 0.12.0
            raise HttpError("rhythm_not_found")
        if q["layer"] == "drums" and "drum_hits" not in v:  # not configured, or stored before worker 0.9.0
            raise HttpError("rhythm_not_found")
        if q["layer"] == "bars" and "bars" not in v:  # no section model, or stored before worker 0.14.0
            raise HttpError("rhythm_not_found")
        return rhythm_page(rid, v, q["layer"], vals["start"], vals["end"], cursor, limit)

    def rhythm_or_error(self, rid):
        st, v = self.load_rhythm(rid)
        if st == "missing":
            raise HttpError("rhythm_not_found")
        if st != "ok":
            raise HttpError("invalid_result")
        return v

    def sheet_view(self, rid, q):
        """The GET /sheet body for parsed query q (format, start, end, view, layers)."""
        fmt = q.get("format", "text")
        if fmt == "html":
            raise HttpError("bad_request", detail=HTML_DETAIL)
        if fmt == "png":
            if set(q) != {"format"}:
                raise HttpError("bad_request", detail=ROLL_DETAIL)
            return self.roll_index(rid, self.rhythm_or_error(rid))
        if fmt not in ("text", "json") or ("start" in q) != ("end" in q):
            raise HttpError("bad_request")
        sel = None  # chosen per-section layers: view=brief = none, layers=a,b = those; not with a zoom span
        if "view" in q or "layers" in q:
            if "start" in q or ("view" in q and "layers" in q) or q.get("view", "brief") != "brief":
                raise HttpError("bad_request", detail=VIEW_DETAIL)
            sel = q["layers"].split(",") if "layers" in q else []
            if not all(n in sheet.SECTION_LAYERS for n in sel) or len(set(sel)) != len(sel):
                raise HttpError("bad_request", detail=VIEW_DETAIL)
        span = None
        if "start" in q:  # zoom: bar-by-bar reading of at most sheet.ZOOM_MAX s
            for k in ("start", "end"):
                if not RE_NUM.match(q[k]) or q[k].startswith("-"):  # also "-0"
                    raise HttpError("bad_request")
            span = float(q["start"]), float(q["end"])
            if not 0 < round(span[1] - span[0], 6) <= sheet.ZOOM_MAX:  # round: 2.2 to 32.2 is 30 s
                raise HttpError("bad_request", detail=SPAN_DETAIL % sheet.ZOOM_MAX)
        v = self.rhythm_or_error(rid)
        if span is not None:
            if span[0] >= v["duration"]:
                raise HttpError("bad_request")
            z = json.loads(json.dumps(sheet.zoom(v, span[0], span[1], rid)))
            if fmt == "json":
                return {"result_id": rid, "zoom": z}
            return {"result_id": rid, "format": z["format"], "text": sheet.render_zoom(z)}
        sh = json.loads(json.dumps(sheet.build(v, rid)))  # the text is rendered from the JSON form only
        if sel is not None:
            sh = sheet.select(sh, sel)
        if fmt == "json":
            return {"result_id": rid, "sheet": sh}
        return {"result_id": rid, "format": sh["format"], "text": sheet.render(sh)}


    def popen(self, argv, **kw):
        """Every child of the pipeline starts here (ml, afconvert, rhythm worker, ear check); local_cli.py tracks
        them so a signal can stop them."""
        return subprocess.Popen(argv, **kw)

    def child_env(self):
        return {"PATH": CHILD_ENV_PATH, "HOME": HOME, "LANG": "en_US.UTF-8",
                "TMPDIR": os.environ.get("TMPDIR", "/tmp")}

    # wrapper calls
    def run_ml(self, args, timeout):
        """Returns (ok, stdout_bytes or None, error_code, detail: ml's fixed reason or None). timeout None = never kill
        (analyze)."""
        lim = self.limits
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                p = self.popen([self.cfg["ml"]] + list(args), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                               env=self.child_env(), cwd="/", close_fds=True, shell=False,
                               start_new_session=True)
            except OSError:
                return False, None, "server_misconfigured", None
            if timeout is None:
                p.wait()
            else:
                try:
                    p.wait(timeout)
                except subprocess.TimeoutExpired:
                    self.stop_ml(p)
                    return False, None, "timeout", None
            if p.returncode != 0:
                return (False, None) + ml_error(err, lim["stderr_line"])
            size = os.fstat(out.fileno()).st_size
            if size > lim["ml_stdout"]:
                return False, None, "invalid_output", None
            out.seek(0)
            data = out.read().strip()
            if not data:
                return False, None, "empty_output", None
            try:
                v = json.loads(data.decode("utf-8"))
            except ValueError:
                return False, None, "invalid_output", None
            if not isinstance(v, dict):
                return False, None, "invalid_output", None
            return True, data, None, None

    def stop_ml(self, p):
        """Stop an `ml` child: SIGINT first, so ml's own `finally` kills its inference child (a separate session that
        holds the team lock), then after ml_stop_grace SIGKILL to ml's process group. ml's cancel can wait up to 30 s
        for its probe; the grace covers that."""
        try:
            os.kill(p.pid, signal.SIGINT)
        except OSError:
            pass
        try:
            p.wait(self.limits["ml_stop_grace"])
            return  # reaped: its pid (= group id) may be reused now, so no killpg after this
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(p.pid, signal.SIGKILL)  # ml ignored SIGINT or hung in its cleanup: as before, the whole group
        except OSError:
            pass
        p.wait()

    def convert(self, src, dst, listen=False):
        """afconvert under limit_exec.py (listen: 44.1 kHz for the ear check); returns (returncode or None on timeout)."""
        argv = [PYTHON, "-I", self.cfg["limit_exec"]] + (["--listen"] if listen else []) + [src, dst]
        return self._run_capped(argv, self.limits["convert_timeout"])

    def _run_capped(self, argv, timeout):
        """Run argv in its own session (cwd /, child env); stdout+stderr kept only up to convert_output and dropped;
        process group killed on timeout. Returns returncode, or None on timeout."""
        p = self.popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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
            p.wait(timeout)
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

    def team_flock(self):
        """The team flock, non-blocking: held by `ml analyze` -> busy, as /analyze gets. Returns the fd to close."""
        try:
            lfd = os.open(self.team_lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise HttpError("storage_error")
        try:
            fcntl.flock(lfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lfd)
            raise HttpError("busy")
        except OSError:
            os.close(lfd)
            raise HttpError("storage_error")
        return lfd

    def roll_index(self, rid, v):
        """GET /sheet?format=png: the PNG pages of a stored result (no audio needed to list them)."""
        return {"result_id": rid, "format": "png", "available": "roll_python" in (self.cfg.get("rhythm") or {}),
                "pages": sheet.pages(v), "render": ROLL_RENDER}

    def roll_audio(self, src, listen, v):
        """Convert the analysed audio (any upload kind) to 44.1 kHz mono s16 at listen; it must be this result's audio
        (duration within ROLL_DURATION_TOL), so the spectrogram never shows another file under these notes."""
        if not (self.cfg.get("rhythm") or {}).get("roll_python") or not os.path.isfile(ROLL):
            raise HttpError("roll_unavailable")
        rc = self.convert(src, listen, listen=True)
        self.log.event("convert_exit", rc=rc, use="roll")
        if rc is None:
            raise HttpError("timeout")
        data = pcm16_wav_data(listen, LISTEN_MAX_BYTES, LISTEN_RATE, 1) if rc == 0 and os.path.isfile(listen) else None
        duration = data / (2.0 * LISTEN_RATE) if data else 0.0
        if not 0 < duration <= 600:
            raise HttpError("invalid_audio")
        if abs(duration - v["duration"]) > ROLL_DURATION_TOL:
            raise HttpError("invalid_argument", detail=ROLL_AUDIO_DETAIL)

    def roll_job(self, v, q):
        """The render a POST /sheet?format=png query (or the CLI's options) asks for, validated against stored result v:
        {"page": N, "marks": bool} or {"poster": (start, end), "title", "credit"}."""
        if q.get("style") == "poster":
            if not set(q) <= {"format", "style", "start", "end", "title", "credit"} or "start" not in q or "end" not in q:
                raise HttpError("bad_request", detail=ROLL_DETAIL)
            for k in ("start", "end"):
                if not RE_NUM.match(q[k]) or q[k].startswith("-"):
                    raise HttpError("bad_request", detail=ROLL_DETAIL)
            a, b = float(q["start"]), float(q["end"])
            if b > v["duration"] + 0.01:  # a rounded end just past the song end is the song end
                raise HttpError("invalid_argument", detail=ROLL_POSTER_DETAIL)
            b = min(b, v["duration"])  # roll.py refuses an end past the duration; the span is checked on this end
            if not ROLL_POSTER_MIN <= round(b - a, 6) <= ROLL_POSTER_MAX:
                raise HttpError("invalid_argument", detail=ROLL_POSTER_DETAIL)
            text = [q.get(k, "") for k in ("title", "credit")]
            if not all(len(t) <= ROLL_TEXT_MAX and t.isprintable() for t in text):
                raise HttpError("bad_request", detail=ROLL_TEXT_DETAIL)
            return {"poster": (a, b), "title": text[0], "credit": text[1]}
        if (not set(q) <= {"format", "page", "marks"} or not RE_PAGE.match(q.get("page", ""))
                or q.get("marks", "1") != "1"):
            raise HttpError("bad_request", detail=ROLL_DETAIL)
        if int(q["page"]) > len(sheet.pages(v)):
            raise HttpError("invalid_argument", detail="page is past the last page (GET /sheet/<id>?format=png lists them)")
        return {"page": int(q["page"]), "marks": "marks" in q}

    def roll_render(self, rid, listen, job, d):
        """roll.py under limit_exec.py --roll / --roll-marks / --roll-poster with rhythm roll_python. Returns (PNG
        bytes, marks dict or None); never a partial image. Outputs go to d (the request's upload directory)."""
        out, mk = d + "/roll.png", d + "/roll-marks.json"
        head = [PYTHON, "-I", self.cfg["limit_exec"]]
        tail = [self.cfg["rhythm"]["roll_python"], ROLL, self.rhythm_path(rid), listen]
        if "poster" in job:
            argv = head + ["--roll-poster"] + tail + ["%.6f" % job["poster"][0], "%.6f" % job["poster"][1], out,
                                                      job["title"], job["credit"]]
        else:
            argv = head + (["--roll-marks"] if job["marks"] else ["--roll"]) + tail + [str(job["page"]), out] + (
                [mk] if job["marks"] else [])
        t0 = time.monotonic()
        rc = self._run_capped(argv, self.limits["roll_timeout"])
        self.log.event("roll_exit", result_id=rid, mode=argv[3], page=job.get("page"), rc=rc,
                       seconds=round(time.monotonic() - t0, 3))
        if rc is None:
            raise HttpError("timeout")
        try:
            with open(out, "rb") as f:
                png = f.read(self.limits["response_max"] + 1)
            marks = None
            if job.get("marks"):
                with open(mk, "rb") as f:
                    marks = json.loads(f.read(self.limits["response_max"] + 1))
        except (OSError, ValueError):
            png = b""
        if rc != 0 or not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > self.limits["response_max"]:
            raise HttpError("roll_failed")
        if job.get("marks") and not (isinstance(marks, dict) and isinstance(marks.get("suspects"), list)
                                     and isinstance(marks.get("missed"), list)):
            raise HttpError("roll_failed")
        return png, marks

    def view_job(self, q):
        """The POST /sheet?format=html query (or the CLI's options): {"marks": bool}."""
        if not set(q) <= {"format", "marks"} or q.get("marks", "1") != "1":
            raise HttpError("bad_request", detail=HTML_DETAIL)
        return {"marks": "marks" in q}

    def view_render(self, rid, upload, listen, job, d):
        """The upload as AAC (limit_exec.py --aac), then view.py under limit_exec.py --view / --view-marks with rhythm
        roll_python. Returns the HTML bytes; never a partial file. Outputs go to d (the request's upload directory)."""
        if not os.path.isfile(VIEW):
            raise HttpError("roll_unavailable")
        aac, out = d + "/view.m4a", d + "/view.html"
        head = [PYTHON, "-I", self.cfg["limit_exec"]]
        rc = self._run_capped(head + ["--aac", upload, aac], self.limits["convert_timeout"])
        self.log.event("convert_exit", rc=rc, use="view")
        if rc is None:
            raise HttpError("timeout")
        if rc != 0 or not os.path.isfile(aac):
            raise HttpError("invalid_audio")
        argv = head + ["--view-marks" if job["marks"] else "--view", self.cfg["rhythm"]["roll_python"], VIEW,
                       self.rhythm_path(rid), listen, aac, out]
        t0 = time.monotonic()
        rc = self._run_capped(argv, self.limits["view_timeout"])
        self.log.event("view_exit", result_id=rid, marks=job["marks"], rc=rc, seconds=round(time.monotonic() - t0, 3))
        if rc is None:
            raise HttpError("timeout")
        try:
            with open(out, "rb") as f:
                html = f.read(VIEW_MAX + 1)
        except OSError:
            html = b""
        if rc != 0 or not html.startswith(b"<!doctype html>") or len(html) > VIEW_MAX:
            raise HttpError("roll_failed")
        return html

    # analyze job (staged upload -> inbox link -> ml analyze)
    def _job(self, job):
        def fail(code, detail=None, hint=None):
            st, b = error_body(code, detail=detail, hint=hint, request_id=job.rid)
            return st, b, None, {"state": "failed", "http_status": st, "error_code": code}
        wav = job.src
        # MP3/M4A always convert; a RIFF/WAVE upload converts only when it is not already canonical
        # (e.g. 22.05/44.1 kHz, stereo, 24-bit, float). Canonical WAV keeps the fast path (no afconvert).
        if job.kind in ("mp3", "m4a") or (job.kind == "wav" and not strict_wav_check(job.src)):
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
        job.ml_started = True
        mode = job.emotion_mode
        ok, data, code, detail = self.run_ml(["analyze", dst_name, "--language", job.language, "--emotion-mode", mode], None)
        self.log.event("analyze_exit", id=job.rid, ok=ok, code=code)
        # ml's own empty_output (detail set): no words (none recognized, or ASR failed) and no emotion layer. Music mood
        # needs no words, so with the rhythm layer requested ml runs once more with emotion_mode music (no loop; its
        # failure is the error body).
        no_words = not ok and code == "empty_output" and detail and mode != "music"
        if no_words and job.rhythm and not self.stopping:
            mode = "music"
            ok, data, code, detail = self.run_ml(["analyze", dst_name, "--language", job.language, "--emotion-mode", mode],
                                                 None)
            self.log.event("analyze_exit", id=job.rid, ok=ok, code=code, fallback=mode)
        if not ok:
            hint = NO_WORDS_HINT if code == "empty_output" and detail and mode != "music" else None
            return fail(code, detail, hint)
        native = json.loads(data.decode("utf-8"))
        if mode != job.emotion_mode:
            native["emotion_fallback"] = mode
        native["request_id"], native["upload_sha256"], native["name"] = job.rid, job.upload_sha256, job.name
        rid = native.get("result_id")
        rid = rid if isinstance(rid, str) and RE_HEX64.match(rid) else None
        return 200, native, None, {"state": "done", "http_status": 200, "result_id": rid, "error_code": None}

    def cleanup(self, op, linked):
        """Request-owned paths only, after owning processes exited. No directory sweeps."""
        d = self.uploads + "/" + op
        shutil.rmtree(d + "/" + SEP_DIR, ignore_errors=True)  # left behind only when the worker was killed
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


class App(Engine):
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
        for name, st in (("nonces", self.nonces), ("receipts", self.receipts)):
            if st.torn_tail:
                self.log.event("journal_tail_truncated", journal=name)
        for r in self.receipts.recovered:
            # Crash leftover: outcome unknown; paths kept until the owner verifies the job and descendants ended.
            self.log.event("receipt_unknown", id=r["request_id"], client=r["client"],
                           paths=[self.uploads + "/" + str(r["op"]) + "/", cfg["inbox"] + "/api-" + str(r["op"]) + ".wav"])
        self.gate = threading.Lock()
        self.conn_lock, self.conns = threading.Lock(), 0
        swept = 0
        for name in os.listdir(self.uploads):  # ear-check and sheet crash leftovers: no receipt can own them
            if RE_EARCHECK_OP.match(name) and stat.S_ISDIR(os.lstat(self.uploads + "/" + name).st_mode):
                self.cleanup(name, False)
                swept += 1
        if swept:
            self.log.event("earcheck_leftovers_removed", count=swept)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.options |= getattr(ssl, "OP_NO_COMPRESSION", 0) | getattr(ssl, "OP_NO_RENEGOTIATION", 0)
        ctx.load_cert_chain(cfg["cert"], cfg["key"])
        self.ssl_ctx = ctx
        self.allow_net = cfg["allow"]
        self.jobs = []
        self.job_lock, self.stopping = threading.Lock(), False
        self._rhythm_init()

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

    def close(self, grace=None):
        """Graceful stop. Listeners must already be stopped. Refuse new launches, wait up to `grace` seconds
        (default stop_grace: the 600 s analyze bound plus margin) for active jobs and connections, then mark
        receipts still running as unknown (interrupted_by_stop, paths kept) and close the stores."""
        grace = self.limits["stop_grace"] if grace is None else grace
        with self.job_lock:
            self.stopping = True
        self.log.event("stopping", jobs=len(self.jobs))
        end = time.monotonic() + grace
        while time.monotonic() < end:
            with self.conn_lock:
                conns = self.conns
            if not self.jobs and not conns:
                break
            time.sleep(0.05)
        try:
            marked = self.receipts.mark_running_unknown(self.clock(), "interrupted_by_stop")
        except Exception:
            marked = []
            self.log.event("receipt_write_failed", id=None)
        for r, durable in marked:
            self.log.event("receipt_unknown", reason="stop", id=r["request_id"], client=r["client"], durable=durable,
                           paths=[self.uploads + "/" + str(r["op"]) + "/", self.cfg["inbox"] + "/api-" + str(r["op"]) + ".wav"])
        self.nonces.close()
        self.receipts.close()
        self.log.event("stopped", interrupted=len(marked))
        self.log.close()

    def earcheck_synth(self, result, src, start, end, out, layers):
        """ear_check.py under limit_exec.py --earcheck with the rhythm python (numpy); returns rc or None on timeout."""
        argv = [PYTHON, "-I", self.cfg["limit_exec"], "--earcheck", self.cfg["rhythm"]["python"], EAR_CHECK, result,
                src, "%.6f" % start, "%.6f" % end, out, ",".join(layers)]
        return self._run_capped(argv, self.limits["earcheck_timeout"])

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
            # After launch the outcome exists but is not recorded: never say "nothing launched".
            code = "outcome_unknown" if job.ml_started else "storage_error"
            self.log.event("receipt_write_failed", id=job.rid, launched=job.ml_started)
            try:
                durable = self.receipts.mark_unknown(job.client, job.rid, self.clock(), code)
            except Exception:
                durable = False
            self.log.event("receipt_unknown", reason="final_write_failed", id=job.rid, client=job.client,
                           durable=durable)
            status, body, raw = error_body(code, request_id=job.rid) + (None,)
        else:
            if job.rhythm and status == 200 and isinstance(body, dict):
                # The receipt already says done (ASR outcome); the rhythm layer is an add-on on the same WAV.
                self.attach_rhythm(job, body, rec.get("result_id"))
        self.cleanup(job.op, job.linked)
        self.gate.release()
        job.status, job.body, job.raw = status, body, raw
        job.done.set()
        try:
            self.jobs.remove(job)
        except ValueError:
            pass


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
                self.send_error_code(e.code, detail=e.detail)
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
    def send_raw(self, status, body_bytes, content_type="application/json", max_bytes=None):
        lim = self.server.app.limits
        if len(body_bytes) > (max_bytes or lim["response_max"]):
            status, obj = error_body("invalid_output")
            body_bytes, content_type = json.dumps(obj).encode(), "application/json"
        phrase = http.HTTPStatus(status).phrase
        head = ("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                "Cache-Control: no-store\r\nConnection: close\r\n\r\n" % (status, phrase, content_type,
                                                                            len(body_bytes))).encode()
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
        m = ROUTE_EARCHECK.match(path)
        if m:
            self.info["route"] = "/earcheck/:id"
            if method != "POST":
                raise HttpError("method_not_allowed")
            return self.do_earcheck(app, m.group(1), nonce, query, cl, rest, vals["x-ml-body-sha256"], expect_continue)
        m = ROUTE_SHEET.match(path)
        if m and method == "POST":
            self.info["route"] = "/sheet/:id"
            return self.do_roll(app, m.group(1), nonce, query, cl, rest, vals["x-ml-body-sha256"], expect_continue)
        if method != "GET":
            raise HttpError("method_not_allowed")
        if path == "/status":
            self.info["route"] = "/status"
            if "?" in target:
                raise HttpError("bad_request")
            return self.read_call(app, ["status"], extra=("rhythm", app.rhythm_status()))
        m = ROUTE_REPORT.match(path)
        if m:
            self.info["route"] = "/report/:id"
            if "?" in target:
                raise HttpError("bad_request")
            return self.read_call(app, ["report", m.group(1)], extra=app.report_extra(m.group(1)))
        m = ROUTE_TIMELINE.match(path)
        if m:
            self.info["route"] = "/timeline/:id"
            q = parse_query(query, ("layer", "start", "end", "limit", "cursor"))
            kind, val = app.timeline(m.group(1), q)
            if kind == "page":
                return self.send_json(200, val)
            return self.read_call(app, val)
        m = ROUTE_SHEET.match(path)
        if m:
            self.info["route"] = "/sheet/:id"
            q = parse_query(query, ("format", "start", "end", "view", "layers"))
            return self.send_json(200, app.sheet_view(m.group(1), q))
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

    def read_call(self, app, args, extra=None):
        data, v = app.read_ml(args, extra)
        if v is None:
            return self.send_raw(200, data)  # native JSON bytes unchanged
        self.send_json(200, v)

    def do_analyze(self, app, client, rid, query, cl, rest, body_sha, expect_continue):
        lim = app.limits
        q = parse_query(query, ("language", "emotion_mode", "name", "rhythm"))
        if q.get("language") not in ("en", "ko", "ja", "auto"):
            raise HttpError("bad_request")
        emotion_mode = q.get("emotion_mode", "none")
        if emotion_mode not in ("voice", "music", "none"):
            raise HttpError("bad_request")
        name = q.get("name")
        if name is not None and not valid_name(name):
            raise HttpError("bad_request")
        rhythm = q.get("rhythm", "0")
        if rhythm not in ("0", "1", "quick"):
            raise HttpError("bad_request")
        quick, rhythm = rhythm == "quick", rhythm != "0"
        if rhythm and not app.cfg.get("rhythm"):
            raise HttpError("rhythm_unavailable")
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
            job.rhythm, job.quick, job.deadline = rhythm, quick, self.t0 + lim["handler_total"]
            try:
                app.receipts.update(client, rid, app.clock(), upload_sha256=upload_sha)
            except OSError:
                self._prelaunch_fail(app, client, rid, op, "storage_error")
                return
            with app.job_lock:  # no launch once a graceful stop has begun
                if not app.stopping:
                    app.jobs.append(job)
                    th = threading.Thread(target=app.run_job, args=(job,), daemon=True)
                    th.start()
                    launched = True
            if not launched:
                self._prelaunch_fail(app, client, rid, op, "shutting_down")
                return
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

    def do_earcheck(self, app, result_id, rid, query, cl, rest, body_sha, expect_continue):
        """Synchronous, no receipt: nothing is stored and nothing runs after the reply, so a retry with a new nonce is
        always safe. Holds the API gate and the team flock, so it never runs beside an analysis."""
        lim = app.limits
        q = parse_query(query, ("start", "end", "layers"))
        if "start" not in q or "end" not in q:
            raise HttpError("bad_request")
        for k in ("start", "end"):
            if not RE_NUM.match(q[k]) or q[k].startswith("-"):  # also "-0"
                raise HttpError("bad_request")
        start, end = float(q["start"]), float(q["end"])
        if start < 0 or not 0 < round(end - start, 6) <= EARCHECK_MAX_SPAN:  # round: 2.2 to 32.2 is 30 s
            raise HttpError("bad_request", detail=SPAN_DETAIL % EARCHECK_MAX_SPAN)
        layers = EARCHECK_DEFAULT
        if "layers" in q:
            layers = tuple(q["layers"].split(","))
            if not all(x in EARCHECK_LAYERS for x in layers) or len(set(layers)) != len(layers):
                raise HttpError("bad_request")
        if not app.cfg.get("rhythm") or not os.path.isfile(EAR_CHECK):
            raise HttpError("earcheck_unavailable")
        if cl > lim["max_body"]:
            raise HttpError("payload_too_large")
        st, _ = app.load_rhythm(result_id)  # refused before any byte of the upload is read
        if st == "missing":
            raise HttpError("rhythm_not_found")
        if st != "ok":
            raise HttpError("invalid_result")
        if not app.gate.acquire(blocking=False):
            raise HttpError("busy")
        op, lfd = "ec-" + secrets.token_hex(16), None
        t0 = time.monotonic()
        try:
            with app.job_lock:
                if app.stopping:
                    raise HttpError("shutting_down")
            if shutil.disk_usage(app.cfg["root"]).free < lim["disk_floor"]:
                raise HttpError("insufficient_storage")
            app.log.event("earcheck_start", id=rid, op=op, result_id=result_id)
            if expect_continue:
                self.sock.settimeout(lim["send_timeout"])
                self.sock.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
            code, kind, _ = self.stage(app, op, cl, rest, body_sha)
            if code is not None:
                raise HttpError(code)
            lfd = app.team_flock()  # after the upload, so a Mac-side `ml analyze` is not blocked while the client sends
            d = app.uploads + "/" + op
            listen, out = d + "/listen.wav", d + "/earcheck.wav"
            rc = app.convert(d + "/upload." + kind, listen, listen=True)
            app.log.event("convert_exit", id=rid, rc=rc)
            if rc is None:
                raise HttpError("timeout")
            data = pcm16_wav_data(listen, LISTEN_MAX_BYTES, LISTEN_RATE, 1) if rc == 0 and os.path.isfile(listen) else None
            duration = data / (2.0 * LISTEN_RATE) if data else 0.0
            if not 0 < duration <= 600:
                raise HttpError("invalid_audio")
            if start >= duration:
                raise HttpError("invalid_argument")  # the span starts after the uploaded audio ends
            rc = app.earcheck_synth(app.rhythm_path(result_id), listen, start, end, out, layers)
            app.log.event("earcheck_exit", id=rid, result_id=result_id, rc=rc, seconds=round(time.monotonic() - t0, 3))
            if rc is None:
                raise HttpError("timeout")
            frames = int((end - start) * LISTEN_RATE) + 1
            data = pcm16_wav_data(out, 44 + frames * 4 + 64, LISTEN_RATE, 2) if rc == 0 and os.path.isfile(out) else None
            if not data or data > frames * 4:
                raise HttpError("earcheck_failed")
            with open(out, "rb") as f:
                wav = f.read()
        finally:
            if lfd is not None:
                os.close(lfd)
            app.cleanup(op, False)
            app.gate.release()
        self.send_json(200, {"result_id": result_id, "start": start, "end": end, "layers": list(layers),
                             "format": "wav", "sample_rate": LISTEN_RATE, "channels": 2, "bytes": len(wav),
                             "sha256": hashlib.sha256(wav).hexdigest(),
                             "audio_base64": base64.b64encode(wav).decode("ascii")})

    def do_roll(self, app, result_id, rid, query, cl, rest, body_sha, expect_continue):
        """POST /sheet/<id>?format=png&page=N (that page as image/png) or format=html[&marks=1] (the whole song as one
        offline HTML with the audio, view.py), body = the analysed audio. As /earcheck:
        synchronous, nothing stored, API gate and team flock held, upload removed before the reply."""
        q = parse_query(query, ("format", "page", "marks", "style", "start", "end", "title", "credit", "view", "layers"))
        if q.get("format") not in ("png", "html"):
            raise HttpError("method_not_allowed")  # the text and JSON sheets are GET only, as before
        if cl > app.limits["max_body"]:
            raise HttpError("payload_too_large")
        v = app.rhythm_or_error(result_id)  # refused before any byte of the upload is read
        html = q["format"] == "html"
        job = app.view_job(q) if html else app.roll_job(v, q)
        if not (app.cfg.get("rhythm") or {}).get("roll_python"):
            raise HttpError("roll_unavailable")
        if not app.gate.acquire(blocking=False):
            raise HttpError("busy")
        op, lfd = "rl-" + secrets.token_hex(16), None
        try:
            with app.job_lock:
                if app.stopping:
                    raise HttpError("shutting_down")
            if shutil.disk_usage(app.cfg["root"]).free < app.limits["disk_floor"]:
                raise HttpError("insufficient_storage")
            app.log.event("roll_start", id=rid, op=op, result_id=result_id, page=job.get("page"))
            if expect_continue:
                self.sock.settimeout(app.limits["send_timeout"])
                self.sock.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
            code, kind, _ = self.stage(app, op, cl, rest, body_sha)
            if code is not None:
                raise HttpError(code)
            lfd = app.team_flock()
            d = app.uploads + "/" + op
            app.roll_audio(d + "/upload." + kind, d + "/listen.wav", v)
            if html:
                body = app.view_render(result_id, d + "/upload." + kind, d + "/listen.wav", job, d)
            else:
                body = app.roll_render(result_id, d + "/listen.wav", job, d)[0]
        finally:
            if lfd is not None:
                os.close(lfd)
            app.cleanup(op, False)
            app.gate.release()
        if html:
            self.send_raw(200, body, "text/html; charset=utf-8", VIEW_MAX)
        else:
            self.send_raw(200, body, "image/png")

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
    except JournalCorrupt as e:
        sys.stderr.write("music-lens-lan-api: journal needs owner recovery: %s\n" % e)
        return 2
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
    stop(listeners)  # stop accepting first
    app.close()  # drain active jobs up to stop_grace, then mark leftovers unknown
    return 0


if __name__ == "__main__":
    sys.exit(main())
