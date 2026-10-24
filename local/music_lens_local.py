#!/usr/bin/env python3
"""Bounded local ASR CLI and newline-delimited JSON-RPC/MCP server."""
import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import wave

VERSION = "0.1.1"
SCHEMA_VERSION = "music-lens-local/1"
END_TOLERANCE = 0.05
MAX_AUDIO = 64 * 1024 * 1024
MAX_REPORT = 4 * 1024 * 1024
MAX_WIRE = 128 * 1024
MAX_WORDS = 40000
LANGUAGES = ("auto", "en", "ko", "ja")


class Failure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def number(value, name, minimum=0, maximum=600):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise Failure("invalid_argument", f"{name} must be finite in [{minimum}, {maximum}]")
    return value


def integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Failure("invalid_argument", f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def wav_info(data):
    if len(data) > MAX_AUDIO:
        raise Failure("invalid_audio", "WAV exceeds 64 MiB")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            frames = w.getnframes()
            if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) != (1, 2, 16000, "NONE"):
                raise Failure("invalid_audio", "Expected mono 16000 Hz signed16 PCM WAV")
            duration = frames / 16000
            if not 0 < duration <= 600 or len(w.readframes(frames)) != frames * 2:
                raise Failure("invalid_audio", "Empty, truncated or longer-than-600s WAV")
    except (wave.Error, EOFError) as e:
        raise Failure("invalid_audio", "Malformed PCM WAV") from e
    return {"duration_seconds": duration, "sample_rate_hz": 16000, "channels": 1,
            "sample_format": "signed16_pcm", "frames": frames, "sha256": digest(data)}


def normalize(raw, duration):
    """Retain valid native words; never manufacture text or an alignment."""
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
        raise Failure("invalid_output", "Engine returned no segments list")
    words, omitted, bad_probability, last_start = [], 0, 0, -1
    overshoot_count, max_overshoot = 0, 0
    for segment in raw["segments"]:
        if not isinstance(segment, dict) or not isinstance(segment.get("words"), list):
            omitted += 1
            continue
        for item in segment["words"]:
            if len(words) >= MAX_WORDS:
                raise Failure("invalid_output", "Engine output exceeds word limit")
            if not isinstance(item, dict):
                omitted += 1
                continue
            text, start, end = item.get("word"), item.get("start"), item.get("end")
            try:
                number(start, "word.start", 0, duration)
                number(end, "word.end", 0, duration + END_TOLERANCE)
                if end < start or start < last_start or not isinstance(text, str) or not text.strip() or len(text) > 1024:
                    raise Failure("invalid_output", "Invalid word interval/text")
            except Failure:
                omitted += 1
                continue
            word = {"text": text, "start": start, "end": end}
            if "probability" in item:
                try:
                    word["probability"] = number(item["probability"], "probability", 0, 1)
                except Failure:
                    bad_probability += 1
            words.append(word)
            if end > duration:
                overshoot_count += 1
                max_overshoot = max(max_overshoot, end - duration)
            last_start = start
    if not words:
        raise Failure("empty_output", "No valid timed words; silence or unrecognized audio is not a successful transcript")
    warnings = []
    if omitted:
        warnings.append(f"Omitted {omitted} invalid words or segments without word data")
    if bad_probability:
        warnings.append(f"Omitted {bad_probability} invalid native probabilities")
    detected = raw.get("language")
    if not isinstance(detected, str) or len(detected) > 32:
        detected = None
    timing_notes = []
    if overshoot_count:
        timing_notes.append({"code": "native_end_overshoot", "word_count": overshoot_count,
                             "max_overshoot_seconds": max_overshoot, "tolerance_seconds": END_TOLERANCE})
    return words, warnings, detected, timing_notes


class LocalLens:
    def __init__(self, audio_root, result_root, model_dir, timeout=300):
        self.audio_root = Path(audio_root).resolve(strict=True)
        self.model_dir = Path(model_dir).resolve(strict=True)
        self.result_root = Path(result_root).absolute()
        self.result_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.result_root = self.result_root.resolve(strict=True)
        if not all(p.is_dir() for p in (self.audio_root, self.model_dir, self.result_root)):
            raise Failure("configuration", "Configured roots must be directories")
        self.timeout = number(timeout, "timeout", 1, 1800)

    def engine_version(self):
        try:
            return importlib.metadata.version("mlx-whisper")
        except importlib.metadata.PackageNotFoundError:
            raise Failure("dependency_missing", "mlx-whisper is not installed in this Python environment")

    def model_manifest(self):
        if not (self.model_dir / "config.json").is_file():
            raise Failure("configuration", "Local model needs config.json")
        manifest, total = [], 0
        for path in sorted(self.model_dir.rglob("*")):
            if path.is_symlink():
                raise Failure("configuration", "Model directory must contain materialized files, not symlinks")
            if not path.is_file():
                continue
            total += path.stat().st_size
            if len(manifest) >= 4096 or total > 16 * 1024**3:
                raise Failure("configuration", "Model assets exceed manifest bounds")
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            manifest.append({"file": path.relative_to(self.model_dir).as_posix(), "sha256": h.hexdigest(), "bytes": path.stat().st_size})
        if not any(x["file"].endswith((".npz", ".safetensors")) for x in manifest):
            raise Failure("configuration", "Local model needs predownloaded .npz or .safetensors weights")
        return manifest

    def status(self):
        try:
            version, error = self.engine_version(), None
        except Failure as e:
            version, error = None, {"code": e.code, "message": str(e)}
        return {"tool_version": VERSION, "engine": "mlx-whisper", "engine_version": version,
                "dependency_error": error, "platform": sys.platform, "max_audio_seconds": 600,
                "inference_timeout_seconds": self.timeout, "automatic_downloads": False}

    def read_audio(self, audio_path):
        if not isinstance(audio_path, str) or not audio_path or len(audio_path) > 4096 or ".." in Path(audio_path).parts:
            raise Failure("invalid_path", "Invalid audio path or traversal")
        path = Path(audio_path)
        if not path.is_absolute():
            path = self.audio_root / path
        path = path.resolve(strict=True)
        if not path.is_relative_to(self.audio_root) or not path.is_file():
            raise Failure("invalid_path", "Audio path must resolve inside audio root")
        # Open each resolved component relative to a pinned root descriptor.
        # O_NOFOLLOW prevents a directory/file swap from escaping confinement.
        fd = os.open(self.audio_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = path.relative_to(self.audio_root).parts
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            audio_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(audio_fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    raise Failure("invalid_path", "Audio must be a regular file")
                data = f.read(MAX_AUDIO + 1)
        finally:
            os.close(fd)
        return data, wav_info(data)

    def result_path(self, result_id):
        if not isinstance(result_id, str) or not re.fullmatch(r"[0-9a-f]{64}", result_id):
            raise Failure("invalid_id", "Expected a 64-character lowercase hex result ID")
        path = self.result_root / (result_id + ".json")
        if path.is_symlink():
            raise Failure("invalid_path", "Result symlinks are forbidden")
        return path

    def load(self, result_id):
        path = self.result_path(result_id)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    raise Failure("invalid_result", "Stored result must be a regular file")
                data = f.read(MAX_REPORT + 1)
        except FileNotFoundError as e:
            raise Failure("not_found", "Unknown result ID") from e
        if len(data) > MAX_REPORT:
            raise Failure("invalid_result", "Stored result exceeds size limit")
        try:
            report = json.loads(data)
            if report["schema_version"] != SCHEMA_VERSION or report["result_id"] != result_id or report["state"] not in ("complete", "partial") or digest(encoded(report["provenance"])) != result_id:
                raise ValueError()
            words = report["words"]
            if not isinstance(words, list) or not words or len(words) > MAX_WORDS or report["word_count"] != len(words):
                raise ValueError()
            duration = number(report["audio"]["duration_seconds"], "stored duration", 0, 600)
            native = [{**w, "word": w["text"]} for w in words]
            checked, warnings, _, timing_notes = normalize({"segments": [{"words": native}]}, duration)
            if warnings or checked != words or report["timing_notes"] != timing_notes:
                raise ValueError()
        except (ValueError, KeyError, TypeError, Failure) as e:
            raise Failure("invalid_result", "Invalid stored result") from e
        return report

    @staticmethod
    def summary(report):
        return {k: v for k, v in report.items() if k != "words"}

    def report(self, result_id):
        return self.summary(self.load(result_id))

    def timeline(self, result_id, start=0, end=600, cursor=0, limit=100):
        number(start, "start")
        number(end, "end")
        if end < start:
            raise Failure("invalid_argument", "end must be at least start")
        integer(cursor, "cursor", 0, MAX_WORDS)
        integer(limit, "limit", 1, 200)
        report = self.load(result_id)
        selected = [w for w in report["words"] if w["end"] >= start and w["start"] <= end]
        if cursor > len(selected):
            raise Failure("invalid_argument", "cursor exceeds matching word count")
        page = []
        for word in selected[cursor:cursor + limit]:
            if len(encoded(page + [word])) > MAX_WIRE // 3:
                break
            page.append(word)
        next_cursor = cursor + len(page)
        return {"result_id": result_id, "clock": "seconds_from_audio_start", "range": {"start": start, "end": end},
                "cursor": cursor, "total": len(selected), "words": page,
                "truncated": next_cursor < len(selected), "continuation": next_cursor if next_cursor < len(selected) else None}

    def infer(self, audio_file, output_file, cancel):
        """Only this fixed child can import MLX. No caller-controlled executable."""
        request = encoded({"audio": str(audio_file), "output": str(output_file), "model": str(self.model_dir)})
        env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "_infer"], stdin=subprocess.PIPE,
                                       stdout=log, stderr=log, env=env, start_new_session=True)
            try:
                process.stdin.write(request)
                process.stdin.close()
                deadline = time.monotonic() + self.timeout
                while process.poll() is None:
                    if cancel.wait(0.05):
                        raise Failure("cancelled", "Inference cancelled")
                    if time.monotonic() >= deadline:
                        raise Failure("timeout", "Inference exceeded configured timeout")
                    if log.tell() > 1024 * 1024:
                        raise Failure("inference_failed", "Inference diagnostic output exceeded 1 MiB")
                if process.returncode:
                    log.seek(0)
                    diagnostic = log.read(4096).decode("utf-8", errors="replace")
                    print("Inference child diagnostic (first 4096 bytes): " + diagnostic, file=sys.stderr)
                    raise Failure("inference_failed", f"Inference child exited {process.returncode}; no result published")
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # The child may exit between poll and kill.
                process.wait()

    def analyze(self, audio_path, language, cancel=None):
        if language not in LANGUAGES:
            raise Failure("invalid_argument", "language must be explicit: auto, en, ko or ja")
        cancel = cancel or threading.Event()
        if cancel.is_set():
            raise Failure("cancelled", "Analysis cancelled")
        data, audio = self.read_audio(audio_path)
        provenance = {"tool_version": VERSION, "engine": "mlx-whisper", "engine_version": self.engine_version(),
                      "model_files": self.model_manifest(), "source_sha256": audio["sha256"],
                      "settings": {"language": language, "word_timestamps": True, "temperature": 0.0},
                      "runner_sha256": digest(Path(__file__).read_bytes())}
        result_id = digest(encoded(provenance))
        path = self.result_path(result_id)
        if path.exists():
            return dict(self.report(result_id), cache_hit=True)
        lock_path = self.result_root / (result_id + ".lock")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as e:
            raise Failure("busy", "This analysis is locked; stale locks require manual owner verification") from e
        try:
            with os.fdopen(fd, "w") as lock:
                json.dump({"pid": os.getpid(), "created_unix": time.time()}, lock)
            if path.exists():
                return dict(self.report(result_id), cache_hit=True)
            with tempfile.TemporaryDirectory(prefix=".analysis-", dir=self.result_root) as work:
                work = Path(work)
                audio_file, output_file = work / "audio.wav", work / "engine.json"
                audio_file.write_bytes(data)
                # Language travels in a bounded sidecar; the executable stays fixed.
                (work / "settings.json").write_bytes(encoded(provenance["settings"]))
                if cancel.is_set():
                    raise Failure("cancelled", "Analysis cancelled before inference")
                self.infer(audio_file, output_file, cancel)
                if cancel.is_set():
                    raise Failure("cancelled", "Inference cancelled before publication")
                if self.model_manifest() != provenance["model_files"]:
                    raise Failure("model_changed", "Model assets changed during inference")
                with output_file.open("rb") as f:
                    payload = f.read(MAX_REPORT + 1)
                if len(payload) > MAX_REPORT:
                    raise Failure("invalid_output", "Engine result exceeds 4 MiB")
                try:
                    raw = json.loads(payload)
                except ValueError as e:
                    raise Failure("invalid_output", "Engine returned invalid JSON") from e
                words, warnings, detected, timing_notes = normalize(raw, audio["duration_seconds"])
                report = {"schema_version": SCHEMA_VERSION, "result_id": result_id, "state": "partial" if warnings else "complete", "audio": audio,
                          "clock": "seconds_from_audio_start", "provenance": provenance, "detected_language": detected,
                          "word_count": len(words), "warnings": warnings, "timing_notes": timing_notes, "probability_semantics": "native_uncalibrated",
                          "created_unix": time.time(), "words": words}
                payload = encoded(report)
                if len(payload) > MAX_REPORT:
                    raise Failure("invalid_output", "Final report exceeds 4 MiB")
                staged = work / "report.json"
                with staged.open("xb") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                # Hard-link is atomic and fails if the destination already exists.
                os.link(staged, path)
            return dict(self.summary(report), cache_hit=False)
        finally:
            lock_path.unlink()


def child_infer():
    """Private fixed process entrypoint; parent supplies only validated snapshots."""
    request = json.loads(sys.stdin.buffer.read(16384))
    import numpy as np
    import mlx_whisper
    with wave.open(request["audio"], "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    settings = json.loads((Path(request["audio"]).parent / "settings.json").read_bytes())
    kwargs = {"path_or_hf_repo": request["model"], "word_timestamps": True, "temperature": 0.0, "verbose": False}
    if settings["language"] != "auto":
        kwargs["language"] = settings["language"]
    raw = mlx_whisper.transcribe(audio, **kwargs)
    # Keep only fields consumed by the parent; no unrestricted transcript dump.
    output = {"language": raw.get("language"), "segments": [{"words": s.get("words")} for s in raw.get("segments", [])]}
    payload = encoded(output)
    if len(payload) > MAX_REPORT:
        raise Failure("invalid_output", "Engine result exceeds output cap")
    Path(request["output"]).write_bytes(payload)


TOOLS = [
    {"name": "status", "description": "Local dependency/status summary; never loads MLX.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "analyze", "description": "Transcribe a confined mono16k signed16 PCM WAV; explicit language required. Heavy child exits after inference.",
     "inputSchema": {"type": "object", "properties": {"audio_path": {"type": "string", "maxLength": 4096}, "language": {"type": "string", "enum": list(LANGUAGES)}}, "required": ["audio_path", "language"], "additionalProperties": False}},
    {"name": "report", "description": "Read provenance and summary by result ID; excludes transcript. Full report remains local.",
     "inputSchema": {"type": "object", "properties": {"result_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}, "required": ["result_id"], "additionalProperties": False}},
    {"name": "timeline", "description": "Read native words overlapping an inclusive time range. Continue with the same range and returned cursor.",
     "inputSchema": {"type": "object", "properties": {"result_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "start": {"type": "number", "minimum": 0, "maximum": 600}, "end": {"type": "number", "minimum": 0, "maximum": 600}, "cursor": {"type": "integer", "minimum": 0, "maximum": MAX_WORDS}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "required": ["result_id"], "additionalProperties": False}}
]


def call_tool(lens, name, arguments, cancel):
    schema = next((t["inputSchema"] for t in TOOLS if t["name"] == name), None)
    if schema is None or not isinstance(arguments, dict):
        raise Failure("invalid_argument", "Unknown tool or invalid arguments")
    if set(arguments) - set(schema["properties"]) or set(schema.get("required", [])) - set(arguments):
        raise Failure("invalid_argument", "Unknown or missing arguments")
    if name == "analyze":
        return lens.analyze(**arguments, cancel=cancel)
    return getattr(lens, name)(**arguments)


def serve(lens):
    output_lock, active_lock = threading.Lock(), threading.Lock()
    active = {}
    initialized = False

    def send(value):
        payload = encoded(value)
        if len(payload) > MAX_WIRE:
            payload = encoded({"jsonrpc": "2.0", "id": value.get("id"), "error": {"code": -32603, "message": "Response exceeds wire size cap"}})
        with output_lock:
            sys.stdout.buffer.write(payload + b"\n")
            sys.stdout.buffer.flush()

    def run_tool(request, cancel):
        try:
            params = request.get("params", {})
            value = call_tool(lens, params.get("name"), params.get("arguments", {}), cancel)
            result = {"content": [{"type": "text", "text": encoded(value).decode()}], "isError": False}
        except Exception as e:
            if not isinstance(e, (Failure, OSError, ValueError, TypeError)):
                print(f"Unexpected tool failure: {type(e).__name__}", file=sys.stderr)
            result = {"content": [{"type": "text", "text": encoded({"code": getattr(e, "code", "local_error"), "message": str(e)[:1000]}).decode()}], "isError": True}
        finally:
            with active_lock:
                active.pop(request["id"], None)
        send({"jsonrpc": "2.0", "id": request["id"], "result": result})

    try:
        while True:
            line = sys.stdin.buffer.readline(MAX_WIRE + 1)
            if not line:
                break
            if len(line) > MAX_WIRE:
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Input exceeds wire size cap; closing"}})
                break
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                    raise ValueError()
                method, rid = request["method"], request.get("id")
                if rid is not None and (type(rid) not in (int, str) or len(str(rid)) > 128):
                    raise ValueError()
                params = request.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC request"}})
                continue
            if rid is None:
                if method == "notifications/cancelled":
                    target = params.get("requestId")
                    if type(target) in (int, str):
                        with active_lock:
                            entry = active.get(target)
                            if entry:
                                entry[0].set()
                # Includes notifications/initialized; notifications never receive replies.
                continue
            response = {"jsonrpc": "2.0", "id": rid}
            if method == "initialize":
                offered = params.get("protocolVersion")
                version = offered if offered in ("2024-11-05", "2025-03-26", "2025-06-18") else "2024-11-05"
                response["result"] = {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": {"name": "music-lens-local", "version": VERSION}}
                initialized = True
            elif method == "ping":
                response["result"] = {}
            elif not initialized:
                response["error"] = {"code": -32000, "message": "Initialize first"}
            elif method == "tools/list":
                response["result"] = {"tools": TOOLS}
            elif method == "tools/call":
                with active_lock:
                    if active:
                        response["error"] = {"code": -32001, "message": "A tool call is active; retry after completion"}
                    else:
                        cancel = threading.Event()
                        thread = threading.Thread(target=run_tool, args=(request, cancel))
                        active[rid] = (cancel, thread)
                        thread.start()
                        continue
            else:
                response["error"] = {"code": -32601, "message": "Method not found"}
            send(response)
    finally:
        with active_lock:
            entries = list(active.values())
            for cancel, _ in entries:
                cancel.set()
        for _, thread in entries:
            thread.join()


def main():
    if sys.argv[1:] == ["_infer"]:
        try:
            child_infer()
            return 0
        except Exception as e:
            print(f"Local inference failed: {type(e).__name__}: {str(e)[:1000]}", file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-root", required=True)
    parser.add_argument("--result-root", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    analyze = commands.add_parser("analyze")
    analyze.add_argument("audio_path")
    analyze.add_argument("--language", choices=LANGUAGES, required=True)
    report = commands.add_parser("report")
    report.add_argument("result_id")
    timeline = commands.add_parser("timeline")
    timeline.add_argument("result_id")
    timeline.add_argument("--start", type=float, default=0)
    timeline.add_argument("--end", type=float, default=600)
    timeline.add_argument("--cursor", type=int, default=0)
    timeline.add_argument("--limit", type=int, default=100)
    commands.add_parser("serve")
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        lens = LocalLens(*(args.pop(k) for k in ("audio_root", "result_root", "model_dir")), timeout=args.pop("timeout"))
        if command == "serve":
            serve(lens)
        else:
            value = getattr(lens, command)(**args)
            payload = encoded(value)
            if len(payload) > MAX_WIRE:
                raise Failure("output_limit", "Response exceeds wire cap; full report remains local")
            sys.stdout.buffer.write(payload + b"\n")
        return 0
    except (Failure, OSError, ValueError, TypeError) as e:
        print(encoded({"error": {"code": getattr(e, "code", "local_error"), "message": str(e)[:1000]}}).decode(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
