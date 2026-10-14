"""Fetch only the fixed 12 GTZAN waveform cases; no inference or corpus sweep."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import wave
import zipfile

COMMIT = "a61439e86c13037011fde8e0f0743ec55c50bce3"
INDEX_URL = "https://zenodo.org/records/13993311/files/gtzan_genre_index_1.0.json?download=1"


def get(url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": "MusicLens-bounded-research/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Download exceeds byte bound: {url}")
    return data


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pilot-root", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    audio = args.output / "audio"
    audio.mkdir(exist_ok=True)
    old_freeze = json.loads((args.pilot_root / "FREEZE.json").read_text(encoding="utf-8"))
    index_bytes = get(INDEX_URL, 2_000_000)
    index = json.loads(index_bytes)
    annotations_path = args.pilot_root / "annotations.zip"
    if sha(annotations_path.read_bytes()) != old_freeze["annotations"]["sha256"]:
        raise ValueError("Original annotation archive identity changed")
    manifest = {"audio_source_commit": COMMIT, "index_url": INDEX_URL,
                "index_sha256": sha(index_bytes), "annotation_archive_sha256": sha(annotations_path.read_bytes()),
                "audio_source_url": f"https://github.com/TempoBeatDownbeat/gtzan_mini/tree/{COMMIT}",
                "sample_rule": "First min(N,661500) native mono22050 PCM16 samples; no resample or gain; decode /32768.",
                "cases": []}
    with zipfile.ZipFile(annotations_path) as archive:
        for case in old_freeze["cases"]:
            _, genre, number = case["key"].split("_")
            filename = f"{genre}.{number}.wav"
            url = f"https://raw.githubusercontent.com/TempoBeatDownbeat/gtzan_mini/{COMMIT}/genres/{genre}/{filename}"
            destination = audio / filename
            payload = destination.read_bytes() if destination.exists() else get(url, 2_000_000)
            expected_md5 = index["tracks"][f"{genre}.{number}"]["audio"][1]
            actual_md5 = hashlib.md5(payload).hexdigest()
            if actual_md5 != expected_md5:
                raise ValueError(f"Authoritative audio MD5 mismatch: {filename}")
            destination.write_bytes(payload)
            with wave.open(io.BytesIO(payload), "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 22050, "NONE"):
                    raise ValueError(f"Not native mono22050 PCM16: {filename}")
                original_frames = wav.getnframes()
                frame_count = min(original_frames, 661500)
                pcm = wav.readframes(frame_count)
            if len(pcm) != frame_count * 2:
                raise ValueError(f"Short PCM read: {filename}")
            pcm_path = audio / f"{case['key']}.s16le"
            pcm_path.write_bytes(pcm)
            duration = frame_count / 22050
            raw_truth = archive.read(case["annotation_member"])
            rows = [[float(field) for field in line.split()] for line in raw_truth.decode().splitlines() if line.strip()]
            if any(len(row) != 2 or row[0] < 0 or row[1] < 1 or row[1] != int(row[1]) for row in rows):
                raise ValueError("Expected beat time and positive integer bar position")
            selected = [row for row in rows if row[0] <= duration]
            manifest["cases"].append({"key": case["key"], "url": url,
                "audio_path": destination.relative_to(args.output).as_posix(), "audio_bytes": len(payload),
                "audio_sha256": sha(payload), "authoritative_md5": expected_md5,
                "original_frames": original_frames, "input_frames": frame_count,
                "duration_seconds": duration, "pcm_path": pcm_path.relative_to(args.output).as_posix(),
                "pcm_sha256": sha(pcm), "annotation_member": case["annotation_member"],
                "annotation_sha256": sha(raw_truth), "reference_beats": [row[0] for row in selected],
                "reference_downbeats": [row[0] for row in selected if row[1] == 1],
                "excluded_tail_references": [row for row in rows if row[0] > duration]})
            print(json.dumps({"key": case["key"], "frames": frame_count, "md5_verified": True}), flush=True)
    manifest["audio_download_bytes"] = sum(case["audio_bytes"] for case in manifest["cases"])
    (args.output / "data.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
