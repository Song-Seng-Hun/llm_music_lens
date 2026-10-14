"""Local TIFA phone alignment with known phone symbols; no G2P or downloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

SOURCE_COMMIT = "32a0a13aebce580844ad1dc281ea11ef1be94f14"
SOURCE_PYTHON_SHA256 = "d1a5932e64003526f56f8d046b28c43f603d1bfecb48858d326fce7da7997cb5"
MODEL_HASHES = {
    "model.pt": "8d9bf896da1827f36bf135f80c7ab1fc4f3c1d48f328818b8402c9527c0f014b",
    "config.yaml": "b4bdfd5c73ca3a01ff8675ca353c54d4c14eefa75a188eac9a22ab94e147f8ac",
    "vocabulary.json": "644c85ac28cfe4f26fcf60de5a6d1fe192c11a6a837746119ef2dbc6dac9246c",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_inputs(args):
    source, model = args.source_root.resolve(), args.model_root.resolve()
    if not (source / "inference/backend.py").is_file():
        raise ValueError(f"Missing local TIFA source: {source}")
    for name in MODEL_HASHES:
        if not (model / name).is_file():
            raise ValueError(f"Missing local TIFA model asset: {model / name}")
    if args.output.exists() or args.output.is_symlink():
        raise ValueError(f"Output already exists; refusing overwrite: {args.output}")
    if not args.output.parent.is_dir():
        raise ValueError("Output parent directory must already exist")
    if args.input.stat().st_size > 1024 * 1024:
        raise ValueError("Input JSON exceeds 1 MiB")
    data = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"audio", "phones"}:
        raise ValueError("Input must contain exactly 'audio' and 'phones'; no timing labels")
    if not isinstance(data["audio"], str) or not data["audio"]:
        raise ValueError("audio must be a local path string")
    phones = data["phones"]
    if not isinstance(phones, list) or not 1 <= len(phones) <= 512:
        raise ValueError("phones must contain 1 to 512 model-vocabulary symbols")
    if not all(isinstance(phone, str) and phone for phone in phones):
        raise ValueError("Every phone must be a nonempty model-vocabulary symbol")
    audio = Path(data["audio"])
    if not audio.is_absolute():
        audio = args.input.resolve().parent / audio
    if not audio.is_file():
        raise ValueError(f"Missing local audio: {audio}")
    # Check the pinned source before importing any of its Python modules.
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*.py"), key=lambda p: p.relative_to(source).as_posix()):
        digest.update(path.relative_to(source).as_posix().encode() + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    if digest.hexdigest() != SOURCE_PYTHON_SHA256:
        raise ValueError(f"TIFA Python source differs from pinned commit {SOURCE_COMMIT}")
    for name, expected in MODEL_HASHES.items():
        if sha256(model / name) != expected:
            raise ValueError(f"TIFA-1.0-ST asset hash mismatch: {name}")
    vocabulary = json.loads((model / "vocabulary.json").read_text(encoding="utf-8"))["symbols"]
    unknown = sorted(set(phones) - vocabulary.keys())
    if unknown:
        raise ValueError(f"Unsupported phone symbols: {unknown}")
    return source, model, audio, phones, [vocabulary[phone] for phone in phones]


def align(args):
    source, model, audio, phones, tokens = validate_inputs(args)
    os.environ["NUMBA_NUM_THREADS"] = "4"
    os.environ["NUMBA_CACHE_DIR"] = str(args.output.resolve().parent / ".tifa-numba-cache")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    started = time.perf_counter()
    import soundfile as sf
    import torch
    import yaml
    from lib.config.schema import ConfigurationScope, InferenceConfig, ModelConfig
    from inference.backend import ForcedAlignmentInferenceModel

    info = sf.info(audio)
    if info.channels != 1 or info.samplerate != 48000 or not 0 < info.duration <= 60:
        raise ValueError("Audio must be mono 48 kHz and longer than 0 but at most 60 seconds")
    wave, sample_rate = sf.read(audio, dtype="float32")
    waveform = torch.from_numpy(wave)[None]
    if not torch.isfinite(waveform).all():
        raise ValueError("Audio contains non-finite samples")
    torch.set_num_threads(4)
    torch.manual_seed(0)
    cfg = yaml.safe_load((model / "config.yaml").read_text(encoding="utf-8"))
    if cfg["model"]["backbone"]["cls"] != "modules.backbones.jebf.JEBFBackbone":
        raise ValueError("Unsupported model backbone")
    scope = ConfigurationScope.FA
    backend = ForcedAlignmentInferenceModel(
        ModelConfig.model_validate(cfg["model"], scope=scope),
        InferenceConfig.model_validate(cfg["inference"], scope=scope),
    )
    checkpoint = torch.load(model / "model.pt", map_location="cpu", weights_only=True)
    state = checkpoint["state_dict"]
    if not state or not all(isinstance(value, torch.Tensor) for value in state.values()):
        raise ValueError("Checkpoint must contain a tensor-only state_dict")
    backend.load_state_dict(state, strict=True)
    backend.eval()
    loaded = time.perf_counter()
    duration = len(wave) / sample_rate
    with torch.inference_mode():
        spec = backend.spectrogram(waveform, torch.tensor([duration]))
        result = backend.align(spec, tokens=torch.tensor([tokens]), groups=None,
                               unit="second", skip_penalty=0.5)
    # Keep zero-width spans in place. Do not turn agreement into timing confidence.
    output = {
        "schema": "tifa-known-phones-1", "source_commit": SOURCE_COMMIT,
        "model_sha256": MODEL_HASHES["model.pt"], "audio_sha256": sha256(audio),
        "input_sha256": sha256(args.input), "duration_seconds": duration,
        "settings": {"device": "cpu", "threads": 4, "precision": "float32",
                     "skip_penalty": 0.5, "groups": None, "torch": torch.__version__},
        "phones": phones, "tokens": tokens,
        "spans_seconds": result.spans[0].tolist(),
        "agreement_uncalibrated": result.agreement[0].item(),
        "timing": {"load_seconds": loaded-started,
                   "frontend_and_alignment_seconds": time.perf_counter()-loaded},
        "limitations": ["Known phone identity/order supplied; no G2P or syllable grouping.",
                        "Agreement is not calibrated timing confidence; inspect skipped spans."],
    }
    serialized = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(serialized + "\n")
    print(json.dumps({"output": str(args.output), "phones": len(phones)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help=f"Local TIFA source at {SOURCE_COMMIT}")
    parser.add_argument("--model-root", type=Path, required=True, help="Local official TIFA-1.0-ST directory")
    parser.add_argument("--input", type=Path, required=True, help="JSON with local audio path and known phone symbols")
    parser.add_argument("--output", type=Path, required=True, help="New JSON path in an existing local output directory")
    args = parser.parse_args()
    try:
        align(args)
    except Exception as exc:
        print(f"Alignment failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
