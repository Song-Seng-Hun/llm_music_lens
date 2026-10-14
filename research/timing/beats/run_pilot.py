"""Frozen local Beat This pilot. No downloads, whole-archive extraction or tuning.

Run only after writing a pre-prediction freeze; both --freeze and --output are required.
Paths in the freeze resolve relative to that file. See README.md for its schema.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import threading
import time
import traceback
import zipfile
from beat_eval import event_score, aggregate

for variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_asset(record, root):
    path = (root / record["path"]).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if sha256(path) != record["sha256"]:
        raise ValueError(f"Frozen asset identity mismatch: {path}")
    return path


class PeakRSS:
    """10ms sampled process RSS; explicitly a measured sample, not exact peak."""
    def __enter__(self):
        import psutil
        self.process = psutil.Process()
        self.start = self.peak = self.process.memory_info().rss
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.thread.start()
        return self

    def sample(self):
        while not self.stop.wait(.01):
            self.peak = max(self.peak, self.process.memory_info().rss)

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()
        self.peak = max(self.peak, self.process.memory_info().rss)

    def record(self):
        return {"start_rss_bytes": self.start, "sampled_peak_rss_bytes": self.peak,
                "sampled_increment_bytes": self.peak - self.start, "sampling_interval_seconds": .01}


def member_bytes(archive, member):
    info = archive.getinfo(member)
    if info.file_size > 64 * 1024 * 1024:
        raise ValueError("Selected member exceeds64MiB bound")
    with archive.open(info) as stream:
        payload = stream.read(64 * 1024 * 1024 + 1)
    if len(payload) != info.file_size:
        raise ValueError("Incomplete ZIP member")
    return payload


def spectrogram(archive, case, np):
    payload = member_bytes(archive, case["spectrogram_member"])
    loaded = np.load(io.BytesIO(payload), allow_pickle=False)
    if isinstance(loaded, np.lib.npyio.NpzFile):
        try:
            array = loaded[case["spectrogram_key"]]
        finally:
            loaded.close()
    else:
        array = loaded
    if array.ndim != 2 or array.shape[1] != 128 or not 1 <= array.shape[0] <= 30001:
        raise ValueError("Expected spectrogram[time,128], <=600s")
    if not np.issubdtype(array.dtype, np.floating) or not np.isfinite(array).all():
        raise ValueError("Invalid spectrogram values")
    return np.ascontiguousarray(array, dtype=np.float32)


def references(archive, case, np):
    rows = np.loadtxt(io.BytesIO(member_bytes(archive, case["annotation_member"])), ndmin=2)
    if rows.ndim != 2 or rows.shape[0] == 0 or rows.shape[1] not in (1, 2) or not np.isfinite(rows).all():
        raise ValueError("Expected nonempty beat annotation in seconds, optionally beat positions")
    beats = rows[:, 0]
    if np.any(beats < 0):
        raise ValueError("Negative annotation time")
    if rows.shape[1] == 1:
        return beats, None
    positions = rows[:, 1]
    if np.any(positions < 1) or np.any(positions != np.floor(positions)):
        raise ValueError("Invalid beat positions; downbeat is position1")
    return beats, beats[positions == 1]


def native_times(values, np):
    times = np.asarray(values, dtype=np.float64)
    if times.ndim != 1 or not np.isfinite(times).all() or np.any(times < 0) or np.any(np.diff(times) < 0):
        raise ValueError("Invalid native event times")
    return times.tolist()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Explicit new result path; existing files are refused")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Output already exists; preserve the prior run")
    freeze_path = args.freeze.resolve()
    frozen_bytes = freeze_path.read_bytes()
    freeze = json.loads(frozen_bytes)
    if freeze.get("frozen_before_predictions") is not True:
        raise ValueError("Parent freeze must explicitly precede predictions")
    root = freeze_path.parent
    checkpoint = checked_asset(freeze["checkpoint"], root)
    spect_path = checked_asset(freeze["spectrograms"], root)
    annotation_path = checked_asset(freeze["annotations"], root)
    keys = [case["key"] for case in freeze["cases"]]
    if len(keys) != len(set(keys)) or not keys:
        raise ValueError("Frozen cases must have unique nonempty keys")
    smokes = [(case, checked_asset(case, root)) for case in freeze.get("smokes", [])]
    report = {"freeze_sha256": hashlib.sha256(frozen_bytes).hexdigest(), "configuration": {
        "device": "cpu", "torch_threads": 1, "torch_interop_threads": 1, "dtype": "float32",
        "postprocessing": "official minimal", "fps": 50, "matching_tolerance_seconds": .07,
        "return_order": "beats,downbeats", "checkpoint": str(checkpoint), "checkpoint_sha256": freeze["checkpoint"]["sha256"]},
        "versions": {}, "cases": [], "smokes": [], "failures": [],
        "limitations": ["Sampled RSS can miss brief peaks; process RSS includes runtime/model storage.",
                        "Local CPU execution is not Sites hosting/runtime evidence.",
                        "Unlabelled waveform smokes establish execution only."]}

    def persist():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf8")

    try:
        with PeakRSS() as model_memory:
            started = time.perf_counter()
            import numpy as np
            import torch
            import soxr
            import soundfile as sf
            from beat_this.inference import Spect2Frames
            from beat_this.model.postprocessor import Postprocessor
            from beat_this.preprocessing import LogMelSpect
            torch.set_num_threads(1)
            torch.set_num_interop_threads(1)
            # Local existence/hash was checked above; official loading cannot
            # fall through to its URL/shortname branch for a missing path.
            runner = Spect2Frames(checkpoint_path=str(checkpoint), device="cpu", float16=False)
            post = Postprocessor(type="minimal", fps=50)
            transform = LogMelSpect(device="cpu")
            if any(parameter.dtype != torch.float32 or parameter.device.type != "cpu" for parameter in runner.model.parameters()):
                raise ValueError("Model is not CPU float32")
            report["model_load_seconds_including_imports"] = time.perf_counter() - started
        report["model_load_memory"] = model_memory.record()
        for package in ("torch", "torchaudio", "beat-this", "numpy", "psutil", "soundfile", "soxr"):
            report["versions"][package] = importlib.metadata.version(package)
        report["installed_source_sha256"] = {name: sha256(Path(module.__file__)) for name, module in (
            ("inference", __import__("beat_this.inference", fromlist=["_"])),
            ("postprocessor", __import__("beat_this.model.postprocessor", fromlist=["_"])),
            ("preprocessing", __import__("beat_this.preprocessing", fromlist=["_"]))) }

        def infer(spect):
            before = time.perf_counter()
            beat_logits, downbeat_logits = runner(spect)
            inference_seconds = time.perf_counter() - before
            before = time.perf_counter()
            beats, downbeats = post(beat_logits, downbeat_logits)
            return {"beats": native_times(beats, np), "downbeats": native_times(downbeats, np),
                    "inference_seconds": inference_seconds, "postprocessing_seconds": time.perf_counter() - before,
                    "frames": len(beat_logits)}

        with zipfile.ZipFile(spect_path) as spectra, zipfile.ZipFile(annotation_path) as annotations:
            for case in freeze["cases"]:
                record = {"key": case["key"], "status": "failed"}
                started = time.perf_counter()
                try:
                    with PeakRSS() as memory:
                        array = spectrogram(spectra, case, np)
                        truth_beats, truth_downbeats = references(annotations, case, np)
                        record.update(infer(torch.from_numpy(array)))
                        record["beat_score"] = event_score(truth_beats, record["beats"])
                        record["downbeat_score"] = None if truth_downbeats is None else event_score(truth_downbeats, record["downbeats"])
                        record["downbeat_annotation_available"] = truth_downbeats is not None
                        record["status"] = "complete"
                    record["memory"] = memory.record()
                except Exception as error:
                    record.update(error=str(error), traceback=traceback.format_exc())
                    report["failures"].append({"key": case["key"], "error": str(error)})
                record["total_seconds_including_read_and_score"] = time.perf_counter() - started
                report["cases"].append(record)
                persist()
                print(json.dumps({"stage": "track_done", "key": record["key"], "status": record["status"],
                                  "seconds": record["total_seconds_including_read_and_score"]}), flush=True)

        for case, audio_path in smokes:
            record = {"key": case["key"], "status": "failed", "accuracy": "unscored; no truth supplied"}
            started = time.perf_counter()
            try:
                with PeakRSS() as memory:
                    signal, rate = sf.read(audio_path, dtype="float64", always_2d=False)
                    if signal.ndim == 2:
                        signal = signal.mean(axis=1)
                    if signal.ndim != 1 or not len(signal) or not np.isfinite(signal).all():
                        raise ValueError("Invalid waveform")
                    record["duration_seconds"] = len(signal) / rate
                    if rate != 22050:
                        signal = soxr.resample(signal, in_rate=rate, out_rate=22050)
                    spect = transform(torch.tensor(signal, dtype=torch.float32))
                    record.update(infer(spect))
                    record["status"] = "complete"
                record["memory"] = memory.record()
            except Exception as error:
                record.update(error=str(error), traceback=traceback.format_exc())
                report["failures"].append({"key": case["key"], "error": str(error)})
            record["total_seconds_including_read_and_preprocessing"] = time.perf_counter() - started
            report["smokes"].append(record)
            persist()
            print(json.dumps({"stage": "smoke_done", "key": record["key"], "status": record["status"],
                              "seconds": record["total_seconds_including_read_and_preprocessing"]}), flush=True)
        if freeze_path.read_bytes() != frozen_bytes:
            raise ValueError("Freeze changed during execution")
        completed = [record for record in report["cases"] if record["status"] == "complete"]
        report["summary"] = {"selected_tracks": len(keys), "completed_tracks": len(completed),
            "failed_tracks": len(keys) - len(completed), "beats": aggregate([record["beat_score"] for record in completed]),
            "downbeats": aggregate([record["downbeat_score"] for record in completed])}
        report["status"] = "failed" if report["failures"] else "complete"
    except Exception as error:
        report["status"] = "failed"
        report["failures"].append({"stage": "setup_or_integrity", "error": str(error), "traceback": traceback.format_exc()})
    persist()
    print(json.dumps({"status": report["status"], "summary": report.get("summary"), "failures": report["failures"]}, allow_nan=False))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
