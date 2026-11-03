#!/usr/bin/env python3
"""Bounded local ASR CLI and newline-delimited JSON-RPC/MCP server."""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
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
import uuid

VERSION = "0.2.6"
SCHEMA_VERSION = "music-lens-local/2"
END_TOLERANCE = 0.05
MAX_AUDIO = 64 * 1024 * 1024
MAX_REPORT = 4 * 1024 * 1024
MAX_WIRE = 128 * 1024
MAX_WORDS = 40000
LANGUAGES = ("auto", "en", "ko", "ja")
EMOTION_MODES = ("none", "voice", "music")
PORTABLE_CAP = 1900000
EMOTION_LIMIT = 600
EMOTION_SETTINGS = {"none": (0, 0, 0), "voice": (4, 2, 1), "music": (5, 2.5, 3)}
# 0.2.4 window re-read: end-of-video phrases Whisper invents after silence or non-lyric audio
REREAD_MARKERS = ("ご視聴", "次回予告", "第1話", "お疲れ様", "チャンネル登録", "字幕",
                  "Thank you for watching", "Thanks for watching", "시청해 주셔서", "구독과 좋아요", "자막")
REREAD_LOOP_CHARS = 40  # one word this long is a decoder loop; longest real word over 20 test songs: 14
REREAD_SHIFTS = (2, 4, 6, 8, 10)
# 0.2.5: song-credit text Whisper writes over an intro instead of lyrics (Vocaloid songs)
REREAD_CREDITS = ("作詞", "作曲", "編曲")
REREAD_REPEAT = re.compile(r"(.{2,24}?)\1{5,}|(.{25,80}?)\2{3,}")  # in a retry, a phrase 6+ (25+ chars: 4+) times in a row is invented


def emotion_shell(mode, duration, status="not_requested", warnings=None):
    size, hop, minimum = EMOTION_SETTINGS[mode]
    return {"mode": mode, "status": status, "warnings": warnings or [], "window_seconds": size,
            "hop_seconds": hop, "minimum_tail_seconds": minimum,
            "coverage": {"covered_seconds": 0, "uncovered_seconds": duration}, "windows": [],
            "score_semantics": "native_uncalibrated", "accuracy": "unvalidated_for_singing" if mode == "voice" else "unmeasured",
            "dimension_scale": "model native, approximately 0..1" if mode == "voice" else "model native, approximately 1..9"}


def checked_emotion(value, duration):
    """Validate stored/native windows without filling gaps or changing scores."""
    if not isinstance(value, dict) or value.get("mode") not in EMOTION_MODES:
        raise Failure("invalid_output", "Invalid emotion mode")
    mode = value["mode"]
    expected = emotion_shell(mode, duration)
    if set(value) != set(expected):
        raise Failure("invalid_output", "Unexpected emotion fields")
    status = value.get("status")
    if status not in ("complete", "partial", "failed", "unavailable", "not_requested"):
        raise Failure("invalid_output", "Invalid emotion status")
    if any(value.get(key) != expected[key] for key in ("window_seconds", "hop_seconds", "minimum_tail_seconds", "score_semantics", "accuracy", "dimension_scale")):
        raise Failure("invalid_output", "Emotion recipe/semantics mismatch")
    warnings, rows = value.get("warnings"), value.get("windows")
    if not isinstance(warnings, list) or any(not isinstance(w, str) or len(w) > 1000 for w in warnings) or len(warnings) > 100:
        raise Failure("invalid_output", "Invalid emotion warnings")
    if not isinstance(rows, list) or len(rows) > EMOTION_LIMIT:
        raise Failure("invalid_output", "Invalid emotion window count")
    if (mode == "none") != (status == "not_requested") or (status in ("failed", "unavailable", "not_requested") and rows) or (status in ("complete", "partial") and not rows):
        raise Failure("invalid_output", "Emotion state/windows mismatch")
    covered, right, last_start = 0.0, 0.0, -1.0
    for row in rows:
        if not isinstance(row, dict):
            raise Failure("invalid_output", "Invalid emotion row")
        start = number(row.get("start"), "emotion.start", 0, duration)
        end = number(row.get("end"), "emotion.end", 0, duration)
        if start <= last_start or end <= start or end - start < expected["minimum_tail_seconds"] - 1e-8 or end - start > expected["window_seconds"] + 1e-8:
            raise Failure("invalid_output", "Invalid emotion interval")
        # Every row follows the frozen window grid; omitted rows remain visible gaps.
        grid = start / expected["hop_seconds"]
        if abs(grid - round(grid)) > 1e-7 or abs(end - min(start + expected["window_seconds"], duration)) > 1e-7:
            raise Failure("invalid_output", "Emotion interval differs from recipe")
        prefix, top, count = ("category", "dominant_emotion", 9) if mode == "voice" else ("mood", "top_mood", 5)
        allowed = {"start", "end", prefix + "_labels", prefix + "_scores", top, "arousal", "valence"}
        if mode == "voice":
            allowed.add("dominance")
        if set(row) != allowed:
            raise Failure("invalid_output", "Unexpected emotion row fields")
        labels, scores = row.get(prefix + "_labels"), row.get(prefix + "_scores")
        if not isinstance(labels, list) or len(labels) != count or any(not isinstance(s, str) or not s or len(s) > 512 for s in labels) or len(set(labels)) != count:
            raise Failure("invalid_output", "Invalid emotion labels")
        if not isinstance(scores, list) or len(scores) != count:
            raise Failure("invalid_output", "Emotion label/score shape mismatch")
        for score in scores:
            number(score, "emotion score", 0, 1)
        if row.get(top) != labels[max(range(count), key=scores.__getitem__)]:
            raise Failure("invalid_output", "Dominant emotion disagrees with native scores")
        for name in (("arousal", "dominance", "valence") if mode == "voice" else ("arousal", "valence")):
            number(row.get(name), name, -100, 100)  # Native regression values are not clamped.
        covered += max(0.0, end - max(start, right))
        right = max(right, end)
        last_start = start
    wanted = {"covered_seconds": covered, "uncovered_seconds": max(0.0, duration - covered)}
    coverage = value.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != set(wanted) or any(abs(number(coverage[k], k, 0, duration) - wanted[k]) > 1e-7 for k in wanted):
        raise Failure("invalid_output", "Emotion coverage mismatch")
    if status == "complete" and (warnings or wanted["uncovered_seconds"] > 1e-7):
        raise Failure("invalid_output", "Incomplete emotion coverage marked complete")
    if status == "complete":
        expected_starts = [i * expected["hop_seconds"] for i in range(EMOTION_LIMIT)
                           if duration - i * expected["hop_seconds"] >= expected["minimum_tail_seconds"] - 1e-8]
        if len(rows) != len(expected_starts) or any(abs(row["start"] - start) > 1e-7 for row, start in zip(rows, expected_starts)):
            raise Failure("invalid_output", "Missing emotion recipe windows marked complete")
    if status in ("partial", "failed", "unavailable") and not warnings:
        raise Failure("invalid_output", "Incomplete emotion requires an explicit warning")
    return value


def portable_provenance(provenance):
    """Export only known provenance fields, rejecting absolute/path-like injected metadata."""
    def token(value, pattern=r"[A-Za-z0-9_.+!:-]{1,128}"):
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise Failure("invalid_result", "Nonportable provenance string")
        return value

    def manifest(rows):
        if not isinstance(rows, list) or len(rows) > 4096:
            raise Failure("invalid_result", "Invalid portable manifest")
        out = []
        for row in rows:
            name = row["file"]
            if not isinstance(name, str) or len(name) > 1024 or name.startswith("/") or "\\" in name or ":" in name or any(part in ("", ".", "..") for part in name.split("/")):
                raise Failure("invalid_result", "Manifest contains a nonportable path")
            out.append({"file": name, "sha256": token(row["sha256"], r"[0-9a-f]{64}"), "bytes": integer(row["bytes"], "model bytes", 0, 16 * 1024**3)})
        return out

    settings = provenance["settings"]
    if settings.get("language") not in LANGUAGES or settings.get("word_timestamps") is not True or settings.get("temperature") != 0.0:
        raise Failure("invalid_result", "Unsupported portable ASR settings")
    out = {"tool_version": token(provenance["tool_version"]), "engine": token(provenance["engine"]),
           "engine_version": token(provenance["engine_version"]), "model_files": manifest(provenance["model_files"]),
           "source_sha256": token(provenance["source_sha256"], r"[0-9a-f]{64}"),
           "runner_sha256": token(provenance["runner_sha256"], r"[0-9a-f]{64}"),
           "settings": {"language": settings["language"], "word_timestamps": True, "temperature": 0.0}}
    if "failure_attempt" in provenance:
        out["failure_attempt"] = token(provenance["failure_attempt"], r"[0-9a-f]{32}")
    emotion = provenance["emotion"]
    mode = emotion["mode"]
    if mode not in EMOTION_MODES:
        raise Failure("invalid_result", "Invalid portable emotion mode")
    size, hop, minimum = EMOTION_SETTINGS[mode]
    expected = {"window_seconds": size, "hop_seconds": hop, "minimum_tail_seconds": minimum}
    if emotion.get("settings") != expected:
        raise Failure("invalid_result", "Invalid portable emotion settings")
    out["emotion"] = {"mode": mode, "settings": expected}
    if mode != "none":
        out["emotion"]["worker_sha256"] = token(emotion["worker_sha256"], r"[0-9a-f]{64}")
        out["emotion"]["availability"] = token(emotion["availability"])
        if "reason" in emotion:
            out["emotion"]["reason"] = token(emotion["reason"])
        if "model_files" in emotion:
            out["emotion"]["model_files"] = manifest(emotion["model_files"])
        if "engine_versions" in emotion:
            out["emotion"]["engine_versions"] = {token(k, r"[A-Za-z0-9_.-]{1,128}"): token(v) for k, v in emotion["engine_versions"].items()}
    return out


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


def checked_rereads(rows, duration):
    """Child-reported re-read windows (see reread_windows); report metadata only, never words."""
    if not isinstance(rows, list) or len(rows) > 64:
        raise Failure("invalid_output", "Invalid window re-read list")
    out = []
    for r in rows:
        if not isinstance(r, dict) or r.get("reason") not in ("marker", "loop", "credit", "sparse") or not (r.get("shift_seconds") is None or r.get("shift_seconds") in REREAD_SHIFTS):
            raise Failure("invalid_output", "Invalid window re-read entry")
        out.append({"start": number(r.get("start"), "reread.start", 0, duration), "end": number(r.get("end"), "reread.end", 0, duration + END_TOLERANCE),
                    "reason": r["reason"], "shift_seconds": r["shift_seconds"]})
    return out


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
        if omitted:
            raise Failure("invalid_output", "All returned word/segment entries were malformed")
        raise Failure("empty_output", "No timed words in structurally valid output; silence or unrecognized audio is not a successful transcript")
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


def compute_helper():
    """Load the release-local cooperative lease under isolated Python (-I)."""
    cached = globals().get('_compute_helper')
    if cached is None:
        path = Path(__file__).with_name('heavy_job.py')
        spec = importlib.util.spec_from_file_location('music_lens_heavy_job', path)
        if spec is None or spec.loader is None:
            raise Failure('configuration', 'Release compute-lock helper unavailable')
        cached = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(cached)
        except (OSError, ValueError) as exc:
            raise Failure('configuration', 'Release compute-lock helper unavailable') from exc
        globals()['_compute_helper'] = cached
    return cached


class LocalLens:
    def __init__(self, audio_root, result_root, model_dir, timeout=300, emotion_python=None, emotion_assets=None, heavy_lock=None):
        self.audio_root = Path(audio_root).resolve(strict=True)
        self.model_dir = Path(model_dir).resolve(strict=True)
        self.result_root = Path(result_root).absolute()
        self.result_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.result_root = self.result_root.resolve(strict=True)
        if not all(p.is_dir() for p in (self.audio_root, self.model_dir, self.result_root)):
            raise Failure("configuration", "Configured roots must be directories")
        self.timeout = number(timeout, "timeout", 1, 1800)
        self.heavy_lock = str(heavy_lock) if heavy_lock is not None else str(Path.home() / "AI-Listen/.analyze.lock")
        # Keep the venv spelling: resolving its Python symlink selects the base runtime.
        self.emotion_python = Path(emotion_python).absolute() if emotion_python else None
        self.emotion_assets = Path(emotion_assets).resolve(strict=True) if emotion_assets else None
        if bool(self.emotion_python) != bool(self.emotion_assets) or (self.emotion_python and (not self.emotion_python.is_file() or not self.emotion_assets.is_dir())):
            raise Failure("configuration", "Configure both emotion Python and asset directory")

    def emotion_provenance(self, mode):
        self.check_budget()
        size, hop, minimum = EMOTION_SETTINGS[mode]
        base = {"mode": mode, "settings": {"window_seconds": size, "hop_seconds": hop, "minimum_tail_seconds": minimum}}
        if mode == "none":
            return base
        worker = Path(__file__).with_name("emotion_worker.py")
        base["worker_sha256"] = digest(worker.read_bytes())
        if not self.emotion_python:
            return dict(base, availability="unavailable", reason="emotion_not_configured")
        manifest = []
        required = (("category", ("model.pt", "config.yaml", "tokens.txt", "configuration.json")),
                    ("intensity", ("model.safetensors", "config.json", "preprocessor_config.json"))) if mode == "voice" else (
                    ("music-model", ("msd-musicnn-1.pb", "deam-msd-musicnn-2.pb", "moods_mirex-msd-musicnn-1.pb")),)
        for directory, filenames in required:
            for name in filenames:
                self.check_budget()
                path = self.emotion_assets / directory / name
                if path.is_symlink() or not path.is_file() or (self.emotion_assets / directory).is_symlink():
                    return dict(base, availability="unavailable", reason="emotion_model_missing")
                sha256, size_bytes = self.asset_digest(path, 4 * 1024**3)
                manifest.append({"file": directory + "/" + name, "sha256": sha256, "bytes": size_bytes})
        env = self.child_environment()
        self.check_budget()
        remaining = getattr(self, "_deadline", time.monotonic() + 30) - time.monotonic()
        try:
            probe = subprocess.run([str(self.emotion_python), "-I", str(worker), "--versions", mode], stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=min(30, max(0.001, remaining)), env=env)
        except (OSError, subprocess.TimeoutExpired):
            self.check_budget()
            return dict(base, availability="unavailable", reason="emotion_dependency_probe_failed", model_files=manifest)
        self.check_budget()
        if probe.returncode or len(probe.stdout) > 16384:
            return dict(base, availability="unavailable", reason="emotion_dependency_probe_failed", model_files=manifest)
        try:
            versions = json.loads(probe.stdout)
            if not isinstance(versions, dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 128 for k, v in versions.items()):
                raise ValueError()
        except (ValueError, TypeError):
            return dict(base, availability="unavailable", reason="emotion_dependency_probe_failed", model_files=manifest)
        return dict(base, availability="ready", model_files=manifest, engine_versions=versions)

    @staticmethod
    def child_environment():
        return dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", MODELSCOPE_OFFLINE="1",
                    HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", OMP_NUM_THREADS="4",
                    TF_NUM_INTRAOP_THREADS="4", TF_NUM_INTEROP_THREADS="1")

    def engine_version(self):
        try:
            return importlib.metadata.version("mlx-whisper")
        except importlib.metadata.PackageNotFoundError:
            raise Failure("dependency_missing", "mlx-whisper is not installed in this Python environment")

    @staticmethod
    def asset_identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def asset_digest(self, path, max_bytes):
        """Full first read; unchanged inode/content metadata permits a stat-only reuse.
        ctime catches same-size rewrites whose mtime is restored. Identity is checked
        before/after reading and against the current path; no global/disk digest cache.
        """
        self.check_budget()
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise Failure("configuration", "Model assets must be materialized regular files")
        if before.st_size > max_bytes:
            raise Failure("configuration", "Model asset exceeds manifest size limit")
        identity = self.asset_identity(before)
        cache = self.__dict__.setdefault("_asset_digests", {})
        key = str(path)
        entry = cache.get(key)
        if entry is not None and entry[0] == identity:
            if self.asset_identity(path.lstat()) != identity:
                raise Failure("model_changed", "Model asset changed during fingerprint check")
            self.check_budget()
            return entry[1], before.st_size
        h = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if self.asset_identity(os.fstat(stream.fileno())) != identity:
                raise Failure("model_changed", "Model asset changed before hashing")
            read_bytes = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                self.check_budget()
                read_bytes += len(chunk)
                h.update(chunk)
            if (read_bytes != before.st_size or self.asset_identity(os.fstat(stream.fileno())) != identity
                    or self.asset_identity(path.lstat()) != identity):
                raise Failure("model_changed", "Model asset changed during hashing")
        self.check_budget()
        sha256 = h.hexdigest()
        # One entry per path, bounded even if an owner repeatedly replaces a model tree.
        if key not in cache and len(cache) >= 8192:
            del cache[next(iter(cache))]
        cache[key] = (identity, sha256)
        return sha256, before.st_size

    def model_manifest(self):
        self.check_budget()
        if not (self.model_dir / "config.json").is_file():
            raise Failure("configuration", "Local model needs config.json")
        manifest, total = [], 0
        for path in sorted(self.model_dir.rglob("*")):
            self.check_budget()
            if path.is_symlink():
                raise Failure("configuration", "Model directory must contain materialized files, not symlinks")
            if not path.is_file():
                continue
            if len(manifest) >= 4096:
                raise Failure("configuration", "Model assets exceed manifest bounds")
            sha256, size_bytes = self.asset_digest(path, 16 * 1024**3 - total)
            total += size_bytes
            manifest.append({"file": path.relative_to(self.model_dir).as_posix(), "sha256": sha256, "bytes": size_bytes})
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
                "inference_timeout_seconds": self.timeout, "automatic_downloads": False,
                "emotion_configured": bool(self.emotion_python), "emotion_modes": list(EMOTION_MODES)}

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
            schema = report["schema_version"]
            if schema not in ("music-lens-local/1", SCHEMA_VERSION) or report["result_id"] != result_id or report["state"] not in ("complete", "partial") or digest(encoded(report["provenance"])) != result_id:
                raise ValueError()
            words = report["words"]
            if not isinstance(words, list) or len(words) > MAX_WORDS or report["word_count"] != len(words) or (schema == "music-lens-local/1" and not words):
                raise ValueError()
            duration = number(report["audio"]["duration_seconds"], "stored duration", 0, 600)
            native = [{**w, "word": w["text"]} for w in words]
            if words:
                checked, warnings, _, timing_notes = normalize({"segments": [{"words": native}]}, duration)
                if warnings or checked != words or report["timing_notes"] != timing_notes:
                    raise ValueError()
            elif report["timing_notes"]:
                raise ValueError()
            if schema == SCHEMA_VERSION:
                emotion = checked_emotion(report["emotion"], duration)
                asr = report["asr_status"]
                if asr not in ("complete", "partial", "failed") or (asr == "failed") != (not words):
                    raise ValueError()
                if report["provenance"]["emotion"]["mode"] != emotion["mode"]:
                    raise ValueError()
                if asr == "failed" and emotion["status"] not in ("complete", "partial"):
                    raise ValueError()
                complete = asr == "complete" and emotion["status"] in ("complete", "not_requested")
                attempt = report["provenance"].get("failure_attempt")
                if attempt is not None and (not isinstance(attempt, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt)
                                            or (asr != "failed" and emotion["status"] != "failed")):
                    raise ValueError()
                if (report["state"] == "complete") != complete:
                    raise ValueError()
                if not isinstance(report["warnings"], list) or any(not isinstance(w, str) or len(w) > 1000 for w in report["warnings"]):
                    raise ValueError()
                if complete and report["warnings"]:
                    raise ValueError()
        except (ValueError, KeyError, TypeError, Failure) as e:
            raise Failure("invalid_result", "Invalid stored result") from e
        return report

    @staticmethod
    def summary(report):
        value = {k: v for k, v in report.items() if k not in ("words", "emotion")}
        value["cacheable"] = "failure_attempt" not in report["provenance"]
        value["retry_recommended"] = not value["cacheable"]
        if "emotion" in report:
            emotion = report["emotion"]
            value["emotion"] = {k: v for k, v in emotion.items() if k != "windows"}
            rows = emotion["windows"]
            value["emotion"]["window_count"] = len(rows)
            value["emotion"]["highest_observed_arousal"] = max(rows, key=lambda x: x["arousal"]) if rows else None
        return value

    def report(self, result_id):
        return self.summary(self.load(result_id))

    def timeline(self, result_id, start=0, end=600, cursor=0, limit=100, layer="words"):
        number(start, "start")
        number(end, "end")
        if end < start:
            raise Failure("invalid_argument", "end must be at least start")
        integer(cursor, "cursor", 0, MAX_WORDS)
        integer(limit, "limit", 1, 200)
        report = self.load(result_id)
        if layer not in ("words", "emotion"):
            raise Failure("invalid_argument", "layer must be words or emotion")
        rows = report["words"] if layer == "words" else report.get("emotion", {}).get("windows", [])
        selected = [w for w in rows if w["end"] >= start and w["start"] <= end]
        if cursor > len(selected):
            raise Failure("invalid_argument", "cursor exceeds matching word count")
        page = []
        for word in selected[cursor:cursor + limit]:
            if len(encoded(page + [word])) > MAX_WIRE // 3:
                break
            page.append(word)
        next_cursor = cursor + len(page)
        layer_status = report.get("asr_status", report["state"]) if layer == "words" else report.get("emotion", {}).get("status", "not_requested")
        layer_warnings = report["warnings"] if layer == "words" else report.get("emotion", {}).get("warnings", [])
        return {"result_id": result_id, "clock": "seconds_from_audio_start", "range": {"start": start, "end": end},
                "cursor": cursor, "total": len(selected), "layer": layer, "words" if layer == "words" else "windows": page,
                "layer_status": layer_status, "warnings": layer_warnings, "report_state": report["state"],
                "truncated": next_cursor < len(selected), "continuation": next_cursor if next_cursor < len(selected) else None}

    def export(self, result_id, title):
        if not isinstance(title, str) or not title.strip() or len(title) > 300 or any(ord(c) < 32 for c in title):
            raise Failure("invalid_argument", "title must be 1..300 printable characters")
        report = self.load(result_id)
        if report["schema_version"] != SCHEMA_VERSION:
            raise Failure("invalid_result", "Portable export requires a v2 analysis")
        # Do not export unrecognized stored fields or local/host metadata.
        portable = {k: report[k] for k in ("schema_version", "result_id", "state", "audio", "clock", "detected_language", "word_count",
                    "warnings", "timing_notes", "probability_semantics", "created_unix", "words", "asr_status", "emotion")}
        portable["provenance"] = portable_provenance(report["provenance"])
        portable["audio"] = {k: report["audio"][k] for k in ("duration_seconds", "sample_rate_hz", "channels", "sample_format", "frames", "sha256")}
        if portable["provenance"] != report["provenance"]:
            # The immutable content ID is tied to its full provenance; never silently rewrite it.
            raise Failure("invalid_result", "Report provenance has fields outside the portable whitelist")
        envelope = {"schema": "music-lens/1", "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "music-lens-local:" + result_id)),
                    "createdAt": datetime.fromtimestamp(report["created_unix"], timezone.utc).isoformat(),
                    "source": {"kind": "file", "title": title, "sha256": report["audio"]["sha256"], "duration": report["audio"]["duration_seconds"]},
                    "signal": None, "interpretation": None,
                    "provenance": {"mode": "imported", "inputStatus": "local model estimates; not independently validated", "accuracy": "unvalidated"},
                    "localAnalysis": portable}
        if len(encoded(envelope)) > PORTABLE_CAP:
            raise Failure("output_limit", "Portable report exceeds 1900000 UTF-8 bytes; nothing truncated")
        return envelope

    def infer(self, audio_file, output_file, cancel):
        """Only this fixed child can import MLX. No caller-controlled executable."""
        request = encoded({"audio": str(audio_file), "output": str(output_file), "model": str(self.model_dir)})
        self.run_child([sys.executable, "-I", str(Path(__file__).resolve()), "_infer"], request, cancel)

    def infer_emotion(self, audio_file, output_file, cancel, mode):
        request = encoded({"audio": str(audio_file), "output": str(output_file), "assets": str(self.emotion_assets), "mode": mode})
        self.run_child([str(self.emotion_python), "-I", str(Path(__file__).with_name("emotion_worker.py"))], request, cancel)

    def run_child(self, command, request, cancel):
        if cancel.is_set():
            raise Failure("cancelled", "Analysis cancelled before child start")
        deadline = getattr(self, "_deadline", time.monotonic() + self.timeout)
        if hasattr(self, "_deadline") and time.monotonic() >= deadline:
            raise Failure("timeout", "Analysis exceeded configured timeout")
        with tempfile.TemporaryFile() as log:
            lease = getattr(self, '_compute_lease', None)
            env = lease.child_env(self.child_environment()) if lease is not None else self.child_environment()
            inherited = lease.pass_fds() if lease is not None else ()
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                       stdout=log, stderr=log, env=env, pass_fds=inherited, start_new_session=True)
            try:
                process.stdin.write(request)
                process.stdin.close()
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

    def check_budget(self):
        cancel = getattr(self, "_analysis_cancel", None)
        if cancel is not None and cancel.is_set():
            raise Failure("cancelled", "Analysis cancelled during preparation")
        deadline = getattr(self, "_deadline", None)
        if deadline is not None and time.monotonic() >= deadline:
            raise Failure("timeout", "Analysis exceeded configured timeout")

    def analyze(self, audio_path, language, cancel=None, emotion_mode="none"):
        cancel = cancel or threading.Event()
        helper = compute_helper()
        absent = object()
        previous = {key: getattr(self, key, absent) for key in ("_analysis_cancel", "_deadline", "_compute_lease")}
        self._analysis_cancel, self._deadline = cancel, time.monotonic() + self.timeout
        try:
            with helper.from_env(self.heavy_lock) as lease:
                self._compute_lease = lease
                self.check_budget()
                return self.analyze_once(audio_path, language, cancel, emotion_mode)
        except helper.HeavyJobError as exc:
            raise Failure(exc.code, str(exc)) from exc
        finally:
            for key, value in previous.items():
                if value is absent:
                    if hasattr(self, key):
                        delattr(self, key)
                else:
                    setattr(self, key, value)

    def analyze_once(self, audio_path, language, cancel, emotion_mode):
        if language not in LANGUAGES:
            raise Failure("invalid_argument", "language must be explicit: auto, en, ko or ja")
        if emotion_mode not in EMOTION_MODES:
            raise Failure("invalid_argument", "emotion_mode must be none, voice or music")
        cancel = cancel or threading.Event()
        if cancel.is_set():
            raise Failure("cancelled", "Analysis cancelled")
        data, audio = self.read_audio(audio_path)
        try:
            asr_version = self.engine_version()
        except Failure:
            asr_version = "unavailable"
        provenance = {"tool_version": VERSION, "engine": "mlx-whisper", "engine_version": asr_version,
                      "model_files": self.model_manifest(), "source_sha256": audio["sha256"],
                      "settings": {"language": language, "word_timestamps": True, "temperature": 0.0},
                      "runner_sha256": digest(Path(__file__).read_bytes()), "emotion": self.emotion_provenance(emotion_mode)}
        self.check_budget()
        result_id = digest(encoded(provenance))
        path = self.result_path(result_id)
        if path.exists():
            cached = self.report(result_id)
            self.check_budget()
            return dict(cached, cache_hit=True)
        lock_path = self.result_root / (result_id + ".lock")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as e:
            raise Failure("busy", "This analysis is locked; stale locks require manual owner verification") from e
        try:
            with os.fdopen(fd, "w") as lock:
                json.dump({"pid": os.getpid(), "created_unix": time.time()}, lock)
            if path.exists():
                cached = self.report(result_id)
                self.check_budget()
                return dict(cached, cache_hit=True)
            with tempfile.TemporaryDirectory(prefix=".analysis-", dir=self.result_root) as work:
                work = Path(work)
                audio_file, output_file = work / "audio.wav", work / "engine.json"
                audio_file.write_bytes(data)
                # Language travels in a bounded sidecar; the executable stays fixed.
                (work / "settings.json").write_bytes(encoded(provenance["settings"]))
                if cancel.is_set():
                    raise Failure("cancelled", "Analysis cancelled before inference")
                words, warnings, detected, timing_notes, rereads = [], [], None, [], []
                transient_failure = False
                asr_status = "failed"
                try:
                    if asr_version == "unavailable":
                        raise Failure("dependency_missing", "ASR dependency unavailable")
                    self.infer(audio_file, output_file, cancel)
                    raw = self.read_child_output(output_file)
                    rereads = checked_rereads(raw.get("rereads", []), audio["duration_seconds"])  # before words: a failure must leave none
                    words, warnings, detected, timing_notes = normalize(raw, audio["duration_seconds"])
                    asr_status = "partial" if warnings else "complete"
                except (Failure, OSError, ValueError, TypeError) as e:
                    if isinstance(e, Failure) and e.code in ("cancelled", "timeout"):
                        raise
                    transient_failure = not (isinstance(e, Failure) and e.code in ("empty_output", "dependency_missing"))
                    warnings.append("ASR failed (" + getattr(e, "code", "invalid_output") + ")")
                if cancel.is_set():
                    raise Failure("cancelled", "Analysis cancelled between stages")
                if time.monotonic() >= self._deadline:
                    raise Failure("timeout", "Analysis exceeded configured timeout")
                emotion = emotion_shell(emotion_mode, audio["duration_seconds"])
                if emotion_mode != "none":
                    if provenance["emotion"].get("availability") != "ready":
                        emotion = emotion_shell(emotion_mode, audio["duration_seconds"], "unavailable", [provenance["emotion"].get("reason", "emotion_unavailable")])
                    else:
                        try:
                            emotion_output = work / "emotion.json"
                            self.infer_emotion(audio_file, emotion_output, cancel, emotion_mode)
                            emotion = checked_emotion(self.read_child_output(emotion_output), audio["duration_seconds"])
                        except (Failure, OSError, ValueError, TypeError) as e:
                            if isinstance(e, Failure) and e.code in ("cancelled", "timeout"):
                                raise
                            emotion = emotion_shell(emotion_mode, audio["duration_seconds"], "failed", ["Emotion failed (" + getattr(e, "code", "invalid_output") + ")"])
                    if emotion["status"] != "complete":
                        warnings.extend(emotion["warnings"])
                    transient_failure = transient_failure or emotion["status"] == "failed"
                if cancel.is_set():
                    raise Failure("cancelled", "Inference cancelled before publication")
                if time.monotonic() >= self._deadline:
                    raise Failure("timeout", "Analysis exceeded configured timeout")
                if self.model_manifest() != provenance["model_files"]:
                    raise Failure("model_changed", "Model assets changed during inference")
                if self.emotion_provenance(emotion_mode) != provenance["emotion"]:
                    raise Failure("model_changed", "Emotion assets/runtime changed during inference")
                if cancel.is_set():
                    raise Failure("cancelled", "Analysis cancelled before publication")
                if time.monotonic() >= self._deadline:
                    raise Failure("timeout", "Analysis exceeded configured timeout")
                if not words and emotion["status"] not in ("complete", "partial"):
                    raise Failure("empty_output", "No requested analysis layer succeeded; no result published")
                if transient_failure:
                    # Preserve useful layers immutably without occupying the reusable cache slot.
                    provenance = dict(provenance, failure_attempt=uuid.uuid4().hex)
                    result_id = digest(encoded(provenance))
                    path = self.result_path(result_id)
                complete = asr_status == "complete" and emotion["status"] in ("complete", "not_requested")
                report = {"schema_version": SCHEMA_VERSION, "result_id": result_id, "state": "complete" if complete else "partial", "audio": audio,
                          "clock": "seconds_from_audio_start", "provenance": provenance, "detected_language": detected,
                          "word_count": len(words), "warnings": warnings, "timing_notes": timing_notes, "window_rereads": rereads, "probability_semantics": "native_uncalibrated",
                          "created_unix": time.time(), "words": words, "asr_status": asr_status, "emotion": emotion}
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

    @staticmethod
    def read_child_output(path):
        with path.open("rb") as f:
            payload = f.read(MAX_REPORT + 1)
        if len(payload) > MAX_REPORT:
            raise Failure("invalid_output", "Engine result exceeds 4 MiB")
        try:
            return json.loads(payload)
        except ValueError as e:
            raise Failure("invalid_output", "Engine returned invalid JSON") from e


def reread_windows(segments, duration, transcribe, level):
    """Re-decode 30 s decoder windows that hallucinated, looped or skipped sung lines, starting later inside the window.

    A window is one decoder seek. Reasons, first match wins:
    marker: its text has a REREAD_MARKERS phrase; loop: a word longer than REREAD_LOOP_CHARS or a REREAD_REPEAT run;
    credit: its text has a REREAD_CREDITS word; sparse: not the first window (intros are often instrumental),
    level(start, end) > -30 dBFS, at least 5 s long and under half the song's median words per second.
    transcribe(clip) is retried with clip "start+k,end". A retry with a marker, credit, loop word or
    REREAD_REPEAT run is never used. marker/loop take the first usable retry with words; credit/sparse take
    the usable retry with the most words, only if it has 1.5x and 10 more words than the window.
    If nothing is used, the long loop words go (a repeat run stays: it may be sung), every word from a marker to the window end goes (what followed
    one was invented too in the test songs), and only the runs of words that spell a credit go.
    Windows without words are left alone: a retry there tends to invent text.
    Returns (segments, rereads); rereads lists each bad window and the shift used (null: words dropped).
    """
    looped = lambda w: len(w["word"].strip()) > REREAD_LOOP_CHARS
    marked = lambda text: any(m in text for m in REREAD_MARKERS)
    credited = lambda text: any(c in text for c in REREAD_CREDITS)
    repeated = lambda text: REREAD_REPEAT.search(re.sub(r"\s", "", text))

    def usable(words):
        text = "".join(w["word"] for w in words)
        return (words and not marked(text) and not credited(text) and not any(looped(w) for w in words)
                and not repeated(text))

    def clean(words):
        drop = {i for i, w in enumerate(words) if looped(w)}
        for j in range(len(words)):  # runs ending at j: any that holds a marker; for a credit, only one that word j completes
            text = ""
            for i in range(j, -1, -1):
                text = words[i]["word"] + text
                if marked(text):
                    drop.update(range(i, j + 1))
                    break
                if credited(text):
                    if not credited(text[:len(text) - len(words[j]["word"])]):
                        drop.update(range(i, j + 1))
                    break
        return [w for i, w in enumerate(words) if i not in drop]

    seeks = sorted({s["seek"] for s in segments})
    windows = []
    for seek, nxt in zip(seeks, seeks[1:] + [None]):
        start, end = seek / 100, (nxt / 100 if nxt is not None else duration)
        window = [s for s in segments if s["seek"] == seek]
        windows.append((seek, start, end, window, [w for s in window for w in s.get("words") or []], level(start, end)))
    rates = sorted(len(words) / (end - start) for _, start, end, _, words, db in windows if db > -35 and end > start)
    median = rates[len(rates) // 2] if rates else 0
    out, rereads = [], []
    for n, (seek, start, end, window, words, db) in enumerate(windows):
        text = "".join(s.get("text", "") for s in window)
        reason = ("marker" if marked(text) else "loop" if any(looped(w) for w in words) or repeated(text) else
                  "credit" if credited(text) else
                  "sparse" if n and words and db > -30 and end - start >= 5 and len(words) / (end - start) < 0.5 * median else None)
        if reason is None:
            out += window
            continue
        fixed, best = None, None
        for k in REREAD_SHIFTS:
            if start + k >= end - 1:
                break
            new = [w for s in transcribe(f"{start + k},{end}") for w in s.get("words") or [] if start <= w["start"] < end]
            if not usable(new):
                continue
            if reason in ("marker", "loop"):
                fixed, best = k, new
                break
            if len(new) >= 1.5 * len(words) and len(new) - len(words) >= 10 and (best is None or len(new) > len(best)):
                fixed, best = k, new
        out.append({"seek": seek, "words": best if best is not None else clean(words)})
        rereads.append({"start": start, "end": end, "reason": reason, "shift_seconds": fixed})
    return out, rereads


def child_infer():
    """Private fixed process entrypoint; parent supplies only validated snapshots."""
    request = json.loads(sys.stdin.buffer.read(16384))
    import numpy as np
    import mlx_whisper
    with wave.open(request["audio"], "rb") as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    settings = json.loads((Path(request["audio"]).parent / "settings.json").read_bytes())
    kwargs = {"path_or_hf_repo": request["model"], "word_timestamps": True, "temperature": 0.0, "verbose": False,
              "condition_on_previous_text": False}  # 0.2.2: stops decoder loops and blank songs (8-song CER check)
    if settings["language"] != "auto":
        kwargs["language"] = settings["language"]
    raw = mlx_whisper.transcribe(audio, **kwargs)
    def level(start, end):  # mean power in dBFS
        part = audio[int(start * 16000):int(end * 16000)]
        return float(10 * np.log10(np.mean(np.square(part)) + 1e-18)) if len(part) else -180.0
    segments, rereads = reread_windows(raw.get("segments", []), len(audio) / 16000,
                                       lambda clip: mlx_whisper.transcribe(audio, clip_timestamps=clip, **kwargs).get("segments", []), level)
    # Keep only fields consumed by the parent; no unrestricted transcript dump.
    output = {"language": raw.get("language"), "segments": [{"words": s.get("words")} for s in segments], "rereads": rereads}
    payload = encoded(output)
    if len(payload) > MAX_REPORT:
        raise Failure("invalid_output", "Engine result exceeds output cap")
    Path(request["output"]).write_bytes(payload)


TOOLS = [
    {"name": "status", "description": "Local dependency/status summary; never loads MLX.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "analyze", "description": "Analyze a confined mono16k signed16 WAV. Optional explicit voice emotion or music mood; native scores, not verified emotion. Children exit per job. Inspect per-layer state.",
     "inputSchema": {"type": "object", "properties": {"audio_path": {"type": "string", "maxLength": 4096}, "language": {"type": "string", "enum": list(LANGUAGES)}, "emotion_mode": {"type": "string", "enum": list(EMOTION_MODES)}}, "required": ["audio_path", "language"], "additionalProperties": False}},
    {"name": "report", "description": "Read provenance and summary by result ID; excludes transcript. Full report remains local.",
     "inputSchema": {"type": "object", "properties": {"result_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}, "required": ["result_id"], "additionalProperties": False}},
    {"name": "timeline", "description": "Read words or emotion windows on the audio clock. Continue with the same range/layer and returned cursor. Scores and peak windows are unvalidated estimates.",
     "inputSchema": {"type": "object", "properties": {"result_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"}, "start": {"type": "number", "minimum": 0, "maximum": 600}, "end": {"type": "number", "minimum": 0, "maximum": 600}, "cursor": {"type": "integer", "minimum": 0, "maximum": MAX_WORDS}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}, "layer": {"type": "string", "enum": ["words", "emotion"]}}, "required": ["result_id"], "additionalProperties": False}}
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
    parser.add_argument("--emotion-python")
    parser.add_argument("--emotion-assets")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    analyze = commands.add_parser("analyze")
    analyze.add_argument("audio_path")
    analyze.add_argument("--language", choices=LANGUAGES, required=True)
    analyze.add_argument("--emotion-mode", choices=EMOTION_MODES, default="none")
    report = commands.add_parser("report")
    report.add_argument("result_id")
    timeline = commands.add_parser("timeline")
    timeline.add_argument("result_id")
    timeline.add_argument("--start", type=float, default=0)
    timeline.add_argument("--end", type=float, default=600)
    timeline.add_argument("--cursor", type=int, default=0)
    timeline.add_argument("--limit", type=int, default=100)
    timeline.add_argument("--layer", choices=("words", "emotion"), default="words")
    export = commands.add_parser("export")
    export.add_argument("result_id")
    export.add_argument("--title", required=True)
    commands.add_parser("serve")
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        lens = LocalLens(*(args.pop(k) for k in ("audio_root", "result_root", "model_dir")), timeout=args.pop("timeout"),
                         emotion_python=args.pop("emotion_python"), emotion_assets=args.pop("emotion_assets"))
        if command == "serve":
            serve(lens)
        else:
            value = getattr(lens, command)(**args)
            payload = encoded(value)
            if len(payload) > (PORTABLE_CAP if command == "export" else MAX_WIRE):
                raise Failure("output_limit", "Response exceeds wire cap; full report remains local")
            sys.stdout.buffer.write(payload + b"\n")
        return 0
    except (Failure, OSError, ValueError, TypeError) as e:
        print(encoded({"error": {"code": getattr(e, "code", "local_error"), "message": str(e)[:1000]}}).decode(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
