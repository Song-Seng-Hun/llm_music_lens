"""Optional reproducible export; normal Sites builds use the tracked ONNX model.

Isolated dependencies: torch==2.8.0+cpu, beat-this==1.1.0, onnx==1.16.1.
No audio, annotation, checkpoint download or quality evaluation is performed.
"""
import argparse
import hashlib
import inspect
import json
from pathlib import Path

CHECKPOINT_SHA = "6074be2c4d490c5f6101fcc374a1ec72ae93456e23bb6019783b849f5dc7d47b"
MODEL_SHA = "2c61c460654723857fc8dface211d133e6c983efec4c5e91b576bfca746182f1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New ONNX/.bin file; existing output is refused")
    parser.add_argument("--frontend-output", type=Path, help="Optionally reproduce the fixed official frontend constants into a new .mjs file")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output already exists")
    if args.frontend_output and args.frontend_output.exists():
        parser.error("--frontend-output already exists")
    if hashlib.sha256(args.checkpoint.read_bytes()).hexdigest() != CHECKPOINT_SHA:
        parser.error("checkpoint SHA256 mismatch")
    import torch
    import onnx
    from beat_this.model.beat_tracker import BeatThis

    torch.set_num_threads(1)
    torch.manual_seed(0)
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    parameters = inspect.signature(BeatThis).parameters
    model = BeatThis(**{k: v for k, v in checkpoint["hyper_parameters"].items() if k in parameters}).eval()
    model.load_state_dict({k.removeprefix("model.").replace("_orig_mod.", ""): v for k, v in checkpoint["state_dict"].items()}, strict=True)

    class Outputs(torch.nn.Module):
        def __init__(self, model):
            super().__init__()
            self.model = model

        def forward(self, features):
            output = self.model(features)
            return output["beat"], output["downbeat"]

    wrapper = Outputs(model).eval()
    features = torch.zeros(1, 250, 128)
    # Match the original export's reference forward: populate fixed rotary caches.
    with torch.inference_mode():
        wrapper(features)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation prevents a concurrent invocation from replacing evidence.
    with args.output.open("xb") as stream:
        torch.onnx.export(wrapper, (features,), stream, input_names=["features"], output_names=["beat", "downbeat"], opset_version=17, dynamo=False, do_constant_folding=True)
    onnx.checker.check_model(onnx.load(args.output))
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    if digest != MODEL_SHA:
        raise RuntimeError(f"Export differs from pinned model: {digest}; output preserved for inspection")
    if args.frontend_output:
        from beat_this.preprocessing import LogMelSpect
        spect = LogMelSpect().spect_class
        window = spect.spectrogram.window.tolist()
        fbank = spect.mel_scale.fb
        filters = [[[i, float(value)] for i, value in enumerate(fbank[:, band]) if value > 0] for band in range(128)]
        text = "// Fixed torchaudio 2.8.0 coefficients for Beat This LogMelSpect.\n// Generated from official periodic Hann and Slaney mel filterbank; no audio data.\nexport const hann = new Float32Array(" + json.dumps(window, separators=(",", ":")) + ");\nexport const filters = [\n" + ",\n".join(json.dumps(band, separators=(",", ":")) for band in filters) + "\n];\n"
        args.frontend_output.parent.mkdir(parents=True, exist_ok=True)
        with args.frontend_output.open("x", encoding="utf-8", newline="") as stream:
            stream.write(text)
    print(f"ONNX check passed; SHA256 {digest}")


if __name__ == "__main__":
    main()
