#!/usr/bin/python3 -I
"""Music Lens local CLI: the LAN server's analyze + rhythm layer, report, timeline and sheet on this Mac, no network.

Trust boundary: the same OS user, like the `ml` wrapper (no TLS, no signing, no nonces, no receipts). It reads the
server's api.json through server.load_config and runs the same code paths as the server (server.Engine): the same
`ml analyze`, rhythm worker, team flock, results-rhythm directory and cache/reuse rules. Output is the server's JSON
body on stdout; errors are {"error": {...}} on stderr. Exit 0 ok, 1 error, 2 config or usage error. SIGTERM, SIGINT
and SIGHUP kill the children (process groups), clean up, and exit 1 with code "cancelled".

Usage:
  local_cli.py [--config PATH] analyze FILE --language {en,ko,ja,auto} [--emotion-mode {voice,music,none}]
               [--name NAME] [--no-rhythm | --quick]
  local_cli.py [--config PATH] report ID
  local_cli.py [--config PATH] timeline ID [--layer L] [--start S] [--end E] [--limit N] [--cursor C]
  local_cli.py [--config PATH] sheet ID [--start S --end E | --view brief | --layers L,...] [--format {text,json}]
"""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import signal
import stat
import sys
import threading

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:  # -I drops the script directory from sys.path; this directory is the trusted install dir
    sys.path.insert(0, HERE)
import server  # noqa: E402

LOG_NAME = "local.log"  # under <root>/logs, beside the server's api.log
CLIENT = "local"


class LocalEngine(server.Engine):
    """server.Engine plus the children it started, so SIGTERM/SIGINT/SIGHUP can stop them before cleanup."""

    def __init__(self, cfg, log_name):
        self.children, self.cancelled, self.interrupted = [], False, set()
        super().__init__(cfg, log_name)

    def popen(self, argv, **kw):
        if self.cancelled:  # no new child once a stop signal arrived
            raise OSError("cancelled")
        p = super().popen(argv, **kw)
        self.children.append(p)
        if self.cancelled:  # the signal landed while it was starting
            self.kill_children()
        return p

    def cancel(self, signum, frame):
        """Signal handler: stop each live child the way the server's timeout paths do (all children run with
        start_new_session=True). The blocked wait then returns, the pipeline fails as usual, and the normal cleanup
        removes uploads/<op>/ and the inbox link after the children are reaped. A second signal skips ml's grace."""
        again = self.cancelled
        self.cancelled = self.stopping = True  # stopping: run_rhythm launches nothing more
        self.kill_children(force=again)

    def kill_children(self, force=False):
        """ml: SIGINT now, SIGKILL to its group after ml_stop_grace (as Engine.stop_ml, without blocking this
        handler). Others (afconvert, rhythm worker): SIGKILL to the group at once."""
        for p in self.children:
            if p.returncode is not None:
                continue
            if p.args[0] == self.cfg["ml"] and not force:
                if p not in self.interrupted:
                    self.interrupted.add(p)
                    try:
                        os.kill(p.pid, signal.SIGINT)
                    except OSError:
                        pass
                    t = threading.Timer(self.limits["ml_stop_grace"], self.kill_group, (p,))
                    t.daemon = True
                    t.start()
            else:
                self.kill_group(p)

    @staticmethod
    def kill_group(p):
        if p.returncode is None:  # not reaped yet, so its pid (= group id) is still its own
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass


class Fail(Exception):
    def __init__(self, code, detail=None, **extra):
        super().__init__(code)
        self.code, self.detail, self.extra = code, detail, extra


def out_bytes(engine, data):
    """The server's response cap (send_raw): an oversized body becomes invalid_output."""
    if engine.cancelled:
        raise Fail("cancelled")
    if len(data) > engine.limits["response_max"]:
        raise Fail("invalid_output")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.flush()


def out_json(engine, obj):
    out_bytes(engine, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def stage(engine, op, path):
    """Copy FILE into uploads/<op>/ (0700) like the server stages an upload. Returns (kind, sha256)."""
    try:
        fd_in = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        raise Fail("invalid_path")
    try:
        st = os.fstat(fd_in)
        if not stat.S_ISREG(st.st_mode):
            raise Fail("invalid_path")
        if st.st_size > engine.limits["max_body"]:
            raise Fail("payload_too_large")
        d = engine.uploads + "/" + op
        try:
            os.mkdir(d, 0o700)
            fd = os.open(d + "/upload.part", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise Fail("storage_error")
        h, head, got = hashlib.sha256(), b"", 0
        try:
            while True:
                chunk = os.read(fd_in, 262144)
                if not chunk:
                    break
                got += len(chunk)
                if got > engine.limits["max_body"]:  # grew while copying
                    raise Fail("payload_too_large")
                h.update(chunk)
                if len(head) < 16:
                    head += chunk[:16 - len(head)]
                server.write_all(fd, chunk)
        finally:
            os.close(fd)
    finally:
        os.close(fd_in)
    kind = server.sniff(head)
    if kind is None:
        raise Fail("invalid_audio")
    os.rename(d + "/upload.part", d + "/upload." + kind)
    return kind, h.hexdigest()


def cmd_analyze(engine, ns):
    rhythm = not ns.no_rhythm
    if rhythm and not engine.cfg.get("rhythm"):
        raise Fail("rhythm_unavailable")
    if ns.name is not None and not server.valid_name(ns.name):
        raise Fail("invalid_argument")
    if shutil.disk_usage(engine.cfg["root"]).free < engine.limits["disk_floor"]:
        raise Fail("insufficient_storage")
    op = secrets.token_hex(16)
    job = None
    try:
        kind, upload_sha = stage(engine, op, ns.file)
        job = server.Job(CLIENT, op, op, kind, engine.uploads + "/" + op + "/upload." + kind, ns.language,
                         ns.emotion_mode, ns.name, upload_sha)
        job.rhythm, job.quick = rhythm, ns.quick
        try:
            status, body, _raw, rec = engine._job(job)
        except Exception as e:  # noqa: BLE001  never leak details (as App.run_job)
            engine.log.event("job_error", id=job.rid, kind=type(e).__name__)
            status, body = server.error_body("internal_error", request_id=job.rid)
            rec = {}
        if status == 200 and rhythm and isinstance(body, dict):
            engine.attach_rhythm(job, body, rec.get("result_id"))
    finally:
        engine.cleanup(op, job.linked if job is not None else False)
    if status != 200:
        err = body["error"]
        raise Fail(err["code"], err.get("detail"), **({"hint": err["hint"]} if "hint" in err else {}),
                   **{k: v for k, v in body.items() if k != "error"})
    out_json(engine, body)


def cmd_report(engine, ns):
    rid = check_id(ns.result_id)
    data, obj = engine.read_ml(["report", rid], engine.report_extra(rid))
    out_bytes(engine, data) if obj is None else out_json(engine, obj)


def cmd_timeline(engine, ns):
    rid = check_id(ns.result_id)
    q = {k: getattr(ns, k) for k in ("layer", "start", "end", "limit", "cursor") if getattr(ns, k) is not None}
    kind, val = engine.timeline(rid, q)
    if kind == "page":
        return out_json(engine, val)
    data, obj = engine.read_ml(val)
    out_bytes(engine, data)


def cmd_sheet(engine, ns):
    rid = check_id(ns.result_id)
    q = {k: getattr(ns, k) for k in ("format", "start", "end", "view", "layers") if getattr(ns, k) is not None}
    out_json(engine, engine.sheet_view(rid, q))


def check_id(rid):
    if not server.RE_HEX64.match(rid):
        raise Fail("invalid_id")
    return rid


def emit_error(code, detail=None, **extra):
    _status, body = server.error_body(code, detail=detail, **extra)
    sys.stderr.write(json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n")


class Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors as JSON on stderr, exit 2 (argparse's own code)
        emit_error("bad_request", detail=message[:500])
        sys.exit(2)


def parser():
    ap = Parser(prog="local_cli.py", description="Music Lens local CLI (no network)")
    ap.add_argument("--config", default=server.HOME + "/.music-lens/api.json")
    sub = ap.add_subparsers(dest="command", required=True, parser_class=Parser)
    a = sub.add_parser("analyze")
    a.add_argument("file")
    a.add_argument("--language", choices=("en", "ko", "ja", "auto"), required=True)
    a.add_argument("--emotion-mode", choices=("voice", "music", "none"), default="none")
    a.add_argument("--name")
    mode = a.add_mutually_exclusive_group()
    mode.add_argument("--no-rhythm", action="store_true", help="ASR (and emotion) only, like rhythm=0 on the server")
    mode.add_argument("--quick", action="store_true", help="rhythm layer without stems, like rhythm=quick on the server")
    r = sub.add_parser("report")
    r.add_argument("result_id")
    t = sub.add_parser("timeline")
    t.add_argument("result_id")
    t.add_argument("--layer", choices=("words", "emotion") + server.RHYTHM_LAYERS)
    for k in ("start", "end", "limit", "cursor"):
        t.add_argument("--" + k)
    s = sub.add_parser("sheet")
    s.add_argument("result_id")
    s.add_argument("--format", choices=("text", "json"))
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--view", help="brief: one line per section (as GET /sheet?view=brief)")
    s.add_argument("--layers", help="comma list of " + ",".join(server.sheet.SECTION_LAYERS) + " (as GET /sheet?layers=)")
    return ap


COMMANDS = {"analyze": cmd_analyze, "report": cmd_report, "timeline": cmd_timeline, "sheet": cmd_sheet}


def main(argv=None):
    ns = parser().parse_args(argv)
    try:
        engine = LocalEngine(server.load_config(ns.config), LOG_NAME)
    except (server.ConfigError, OSError) as e:
        emit_error("server_misconfigured", detail=str(e) if isinstance(e, server.ConfigError) else type(e).__name__)
        return 2
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, engine.cancel)
    try:
        COMMANDS[ns.command](engine, ns)
        return 0
    except Fail as e:
        code, detail, extra = e.code, e.detail, e.extra
    except server.HttpError as e:
        code, detail, extra = e.code, e.detail, {}
    except Exception as e:  # noqa: BLE001
        engine.log.event("local_error", command=ns.command, kind=type(e).__name__)
        code, detail, extra = "internal_error", None, {}
    finally:
        engine.log.close()
    if engine.cancelled:  # whatever the killed children made the pipeline report
        code, detail = "cancelled", None
        extra = {k: v for k, v in extra.items() if k == "request_id"}
    emit_error(code, detail=detail, **extra)
    return 1


if __name__ == "__main__":
    sys.exit(main())
