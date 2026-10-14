"""Export one fixed-shape Beat This feasibility model; no accuracy evaluation."""
import argparse
import hashlib
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace

EXPECTED_SHA = "6074be2c4d490c5f6101fcc374a1ec72ae93456e23bb6019783b849f5dc7d47b"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True, help="Existing small0.ckpt; fixed SHA checked before safe loading")
    parser.add_argument("--audio", type=Path, required=True, help="Existing audio; use first five seconds only")
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory below this checkout's ignored evidence/; existing directories are refused")
    args = parser.parse_args()
    evidence = Path(__file__).resolve().parents[3] / "evidence"
    args.output_dir = args.output_dir.resolve()
    if args.output_dir == evidence or not args.output_dir.is_relative_to(evidence):
        parser.error("--output-dir must be a subdirectory of this checkout's evidence/")
    if args.output_dir.exists():
        parser.error("--output-dir must be new; existing experiment artifacts are never overwritten")
    # Keep --help usable without installing research-only packages.
    import onnx
    import torch
    from beat_this.model.beat_tracker import BeatThis
    from beat_this.inference import Audio2Frames
    from beat_this.preprocessing import LogMelSpect, load_audio

    digest = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA:
        raise RuntimeError(f"checkpoint SHA mismatch: {digest}")
    torch.set_num_threads(1)
    torch.manual_seed(0)
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    parameters = inspect.signature(BeatThis).parameters
    hparams = {k: v for k, v in checkpoint["hyper_parameters"].items() if k in parameters}
    model = BeatThis(**hparams).eval()
    state = {k.removeprefix("model.").replace("_orig_mod.", ""): v for k, v in checkpoint["state_dict"].items()}
    model.load_state_dict(state, strict=True)

    class Outputs(torch.nn.Module):
        def __init__(self, model):
            super().__init__()
            self.model = model

        def forward(self, features):
            output = self.model(features)
            return output["beat"], output["downbeat"]

    wrapper = Outputs(model).eval()
    signal, sample_rate = load_audio(str(args.audio))
    if len(signal) < 5 * sample_rate:
        raise ValueError("This frozen pilot requires at least five seconds of audio")
    preprocessor = SimpleNamespace(device=torch.device("cpu"), spect=LogMelSpect(device="cpu"))
    features = Audio2Frames.signal2spect(preprocessor, signal[:5 * sample_rate], sample_rate)[:250].unsqueeze(0)
    if tuple(features.shape) != (1, 250, 128):
        raise ValueError(f"Unexpected feature shape: {tuple(features.shape)}")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    features.numpy().astype("<f4").tofile(args.output_dir / "beat-input.f32")
    started = time.perf_counter()
    with torch.inference_mode():
        expected = wrapper(features)
    torch_ms = (time.perf_counter() - started) * 1000
    reference = {name: value.numpy().reshape(-1).tolist() for name, value in zip(("beat", "downbeat"), expected)}
    (args.output_dir / "beat-reference.json").write_text(json.dumps(reference), encoding="utf-8")
    print(json.dumps({"stage": "torch", "parameters": sum(p.numel() for p in model.parameters()), "shapes": [list(x.shape) for x in expected], "ms": torch_ms}), flush=True)
    started = time.perf_counter()
    target = args.output_dir / "beat-small-250.onnx"
    torch.onnx.export(wrapper, (features,), str(target), input_names=["features"], output_names=["beat", "downbeat"], opset_version=17, dynamo=False, do_constant_folding=True)
    export_ms = (time.perf_counter() - started) * 1000
    graph = onnx.load(target)
    onnx.checker.check_model(graph)
    binary = target.read_bytes()
    result = {
        "purpose": "fixed-shape runtime feasibility only; no downbeat quality claim",
        "checkpoint_sha256": digest, "torch": torch.__version__, "onnx": onnx.__version__, "shape": [1, 250, 128],
        "featureFixture": f"{args.audio.name} first 5 seconds; official Audio2Frames.signal2spect first 250 frames",
        "audioSha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
        "inputSha256": hashlib.sha256((args.output_dir / "beat-input.f32").read_bytes()).hexdigest(),
        "atol": 1e-4, "rtol": 1e-4, "torchMs": torch_ms, "exportMs": export_ms,
        "modelBytes": len(binary), "modelSha256": hashlib.sha256(binary).hexdigest(),
        "opsets": [{"domain": x.domain, "version": x.version} for x in graph.opset_import],
        "operators": sorted({x.op_type for x in graph.graph.node}), "hparams": hparams,
    }
    (args.output_dir / "beat-export.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
