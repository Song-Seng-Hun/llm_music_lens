"""Official Beat This waveform frontend only; no checkpoint or predictions."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    import numpy as np
    import torch
    import beat_this.preprocessing as preprocessing
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    frontend = preprocessing.LogMelSpect(device="cpu")
    data = json.loads((args.root / "data.json").read_text(encoding="utf-8"))
    destination = args.root / "features"
    destination.mkdir(exist_ok=True)
    result = {"source_sha256": hashlib.sha256(Path(preprocessing.__file__).read_bytes()).hexdigest(),
              "versions": {name: importlib.metadata.version(name) for name in ["beat-this", "torch", "torchaudio", "numpy"]}, "cases": []}
    for case in data["cases"]:
        raw = (args.root / case["pcm_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != case["pcm_sha256"]:
            raise ValueError("PCM identity changed")
        waveform = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
        started = time.perf_counter()
        with torch.inference_mode():
            features = frontend(torch.from_numpy(waveform)).numpy().astype("<f4")
        elapsed = time.perf_counter() - started
        path = destination / f"{case['key']}.f32"
        path.write_bytes(features.tobytes())
        result["cases"].append({"key": case["key"], "path": path.relative_to(args.root).as_posix(),
             "shape": list(features.shape), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "frontend_seconds": elapsed})
    (args.root / "features.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(result["cases"]), "versions": result["versions"], "model_inference": False}))


if __name__ == "__main__":
    main()
