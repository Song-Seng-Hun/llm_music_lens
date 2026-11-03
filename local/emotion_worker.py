"""Fixed local emotion child; no fixture runner, downloads, listener or caller code."""
import contextlib
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import wave

DEFAULT_BATCH_SIZE = 8
MAX_BATCH_SIZE = 16
DEFAULT_DEVICE = "cpu"

SETTINGS = {"voice": (4, 2, 1), "music": (5, 2.5, 3)}
MOODS = ["passionate, rousing, confident, boisterous, rowdy", "rollicking, cheerful, fun, sweet, amiable/good natured",
         "literate, poignant, wistful, bittersweet, autumnal, brooding", "humorous, silly, campy, quirky, whimsical, witty, wry",
         "aggressive, fiery, tense/anxious, intense, volatile, visceral"]
FUNASR_SOURCE = {"base.py": "4f2029c6062e3f6ef4d7abbc60f4570b03163e8b5d70b398c99c918b645c00e0",
                 "audio.py": "94c425d23ca096924fdd2157067b2b23a24061dbd9bd9f7e7433e4f3c8cabf44",
                 "model.py": "a9967e269646fcbefcacc29b9d4c76c5d03ca055de5b53884be95dafc53e921f"}


def versions(mode):
    packages = ("torch", "funasr", "transformers", "numpy") if mode == "voice" else ("essentia-tensorflow", "numpy")
    return {name: importlib.metadata.version(name) for name in packages}


def category_predictor(directory, device="cpu"):
    import torch
    torch.set_num_threads(4)
    torch.manual_seed(0)
    original = torch.load

    def safe_load(*args, **kwargs):
        if kwargs.get("weights_only") is False:
            raise RuntimeError("Unsafe pickle loading refused")
        kwargs["weights_only"] = True
        return original(*args, **kwargs)

    torch.load = safe_load
    from funasr import AutoModel
    from funasr.models.emotion2vec.model import Emotion2vec
    import funasr.models.emotion2vec.model as source
    root = Path(source.__file__).parent
    if any(hashlib.sha256((root / name).read_bytes()).hexdigest() != wanted for name, wanted in FUNASR_SOURCE.items()):
        raise RuntimeError("Category inference source differs from audited version")

    def coverage(self, loaded_keys):
        missing = set(self.state_dict()) - set(loaded_keys)
        unused = {f"modality_encoders.AUDIO.decoder.blocks.{i}.0.{kind}" for i in range(4) for kind in ("weight", "bias")}
        unused |= {f"modality_encoders.AUDIO.decoder.proj.{kind}" for kind in ("weight", "bias")}
        if missing != unused:
            raise RuntimeError("Category checkpoint has unexpected missing inference state")
        self._local_coverage_checked = True

    Emotion2vec.on_pretrained_model_loaded = coverage
    check_device(torch, device)
    model = AutoModel(model=str(directory), device=device, ncpu=4, hub="hf", disable_update=True,
                      disable_log=True, disable_pbar=True, trust_remote_code=False, ignore_init_mismatch=False)
    if not getattr(model.model, "_local_coverage_checked", False):
        raise RuntimeError("Category checkpoint loader did not verify coverage")
    model.model.eval()
    if next(model.model.parameters()).device.type != device:
        raise RuntimeError("Category model did not load on the requested device")
    tokenizer = model.kwargs.get("tokenizer")
    labels = list(tokenizer.token_list) if tokenizer is not None else []
    keep = [i for i, label in enumerate(labels) if not label.startswith("unuse")]
    if model.model.proj is None or not keep:
        raise RuntimeError("Category classifier or token labels missing")
    selected_labels = [labels[i] for i in keep]

    def predict(batch):
        # AutoModel.generate(list, batch_size=N) is serial inside the pinned inference()
        # implementation. Mirror its audited utterance path using a real equal-length
        # tensor batch; normalize each utterance independently, never across the batch.
        import numpy as np
        import torch.nn.functional as F
        require_equal_length(batch)
        source = torch.from_numpy(np.stack(batch)).to(device=device)
        if model.model.cfg.normalize:
            source = F.layer_norm(source, source.shape[-1:])
        with torch.inference_mode():
            features = model.model.extract_features(source, padding_mask=None)["x"]
            logits = model.model.proj(features.mean(dim=1))
            if tuple(logits.shape) != (len(batch), len(labels)):
                raise RuntimeError("Invalid category batch result shape")
            unused = [i for i in range(len(labels)) if i not in keep]
            if unused:
                logits[:, unused] = -float("inf")
            scores = torch.softmax(logits, dim=-1).cpu().tolist()
        out = []
        for values in scores:
            selected_scores = [float(values[i]) for i in keep]
            out.append({"category_labels": selected_labels, "category_scores": selected_scores,
                        "dominant_emotion": selected_labels[max(range(len(keep)), key=selected_scores.__getitem__)]})
        return out

    return predict


def dimension_predictor(directory, device="cpu"):
    import torch
    import torch.nn as nn
    from transformers import Wav2Vec2FeatureExtractor
    from transformers.models.wav2vec2.modeling_wav2vec2 import Wav2Vec2Model, Wav2Vec2PreTrainedModel
    torch.set_num_threads(4)
    torch.manual_seed(0)

    class RegressionHead(nn.Module):
        def __init__(self, config):
            super().__init__()
            self.dense = nn.Linear(config.hidden_size, config.hidden_size)
            self.dropout = nn.Dropout(config.final_dropout)
            self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

        def forward(self, features):
            x = self.dropout(features)
            return self.out_proj(self.dropout(torch.tanh(self.dense(x))))

    class EmotionModel(Wav2Vec2PreTrainedModel):
        def __init__(self, config):
            super().__init__(config)
            self.wav2vec2 = Wav2Vec2Model(config)
            self.classifier = RegressionHead(config)
            self.init_weights()

        def forward(self, values):
            return self.classifier(self.wav2vec2(values)[0].mean(dim=1))

    check_device(torch, device)
    extractor = Wav2Vec2FeatureExtractor.from_pretrained(directory, local_files_only=True)
    model, info = EmotionModel.from_pretrained(directory, local_files_only=True, use_safetensors=True, output_loading_info=True)
    if info["missing_keys"] or info["unexpected_keys"] or info["mismatched_keys"] or info.get("error_msgs"):
        raise RuntimeError("Dimensional checkpoint mismatch")
    model.to(device)
    model.eval()
    if next(model.parameters()).device.type != device:
        raise RuntimeError("Dimensional model did not load on the requested device")

    def predict(batch):
        require_equal_length(batch)
        # Equal-length batches require no padding or padding-dependent mean pooling.
        values = extractor(list(batch), sampling_rate=16000, padding=False, return_tensors="pt")["input_values"].to(device)
        with torch.inference_mode():
            values = model(values)
        if tuple(values.shape) != (len(batch), 3):
            raise RuntimeError("Invalid dimensional batch result shape")
        return [dict(zip(("arousal", "dominance", "valence"), map(float, row))) for row in values.cpu().tolist()]

    return predict


def music_predictor(directory):
    import numpy as np
    from essentia.standard import TensorflowPredictMusiCNN, TensorflowPredict2D
    backbone = TensorflowPredictMusiCNN(graphFilename=str(directory / "msd-musicnn-1.pb"), output="model/dense/BiasAdd")
    dim = TensorflowPredict2D(graphFilename=str(directory / "deam-msd-musicnn-2.pb"), output="model/Identity")
    mood = TensorflowPredict2D(graphFilename=str(directory / "moods_mirex-msd-musicnn-1.pb"),
                              input="serving_default_model_Placeholder", output="PartitionedCall")

    def predict(x):
        embedding = backbone(x)
        values = np.asarray(dim(embedding)).mean(axis=0)
        scores = np.asarray(mood(embedding)).mean(axis=0)
        if values.shape != (2,) or scores.shape != (5,):
            raise RuntimeError("Invalid music result shape")
        scores = scores.tolist()
        return {"valence": float(values[0]), "arousal": float(values[1]), "mood_labels": MOODS,
                "mood_scores": scores, "top_mood": MOODS[max(range(5), key=scores.__getitem__)]}

    return predict


def execution_settings(request):
    device = request.get("device", DEFAULT_DEVICE)
    batch_size = request.get("batch_size", DEFAULT_BATCH_SIZE)
    if device not in ("cpu", "mps"):
        raise ValueError("Explicit emotion device must be cpu or mps")
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError("Emotion batch_size must be an integer in 1..16")
    if request["mode"] == "music" and device != "cpu":
        raise ValueError("Music inference supports cpu only")
    return device, batch_size


def check_device(torch, device):
    if device == "mps" and (not torch.backends.mps.is_built() or not torch.backends.mps.is_available()):
        raise RuntimeError("Requested MPS emotion device is unavailable; no CPU fallback")
    if device == "mps" and os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") not in (None, "0"):
        raise RuntimeError("MPS operator fallback must be disabled")


def require_equal_length(batch):
    if not batch or len(batch) > MAX_BATCH_SIZE or len({len(x) for x in batch}) != 1:
        raise ValueError("Emotion batch must contain 1..16 equal-length windows")


def window_batches(x, rows, batch_size):
    """Stable-order equal-length batches; short tails never pad or mix with full windows."""
    at = 0
    while at < len(rows):
        first = rows[at]
        length = int(first["end"] * 16000) - int(first["start"] * 16000)
        end = at + 1
        while end < min(len(rows), at + batch_size):
            row = rows[end]
            if int(row["end"] * 16000) - int(row["start"] * 16000) != length:
                break
            end += 1
        yield rows[at:end], [x[int(r["start"] * 16000):int(r["end"] * 16000)] for r in rows[at:end]]
        at = end


def predict_rows(predict, x, rows, batch_size):
    for selected, batch in window_batches(x, rows, batch_size):
        result = predict(batch)
        if len(result) != len(selected):
            raise RuntimeError("Emotion batch result count mismatch")
        for row, values in zip(selected, result):
            row.update(values)


def analyze(request):
    import numpy as np
    mode = request["mode"]
    device, batch_size = execution_settings(request)
    size, hop, minimum = SETTINGS[mode]
    assets = Path(request["assets"])
    with wave.open(request["audio"], "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) != (1, 2, 16000, "NONE"):
            raise ValueError("Expected validated mono16k PCM snapshot")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    duration = len(x) / 16000
    rows = []
    for frame in range(0, len(x), int(hop * 16000)):
        end_frame = min(frame + int(size * 16000), len(x))
        if end_frame - frame < int(minimum * 16000):
            break
        rows.append({"start": frame / 16000, "end": end_frame / 16000})
    if not rows:
        return {"mode": mode, "status": "unavailable", "warnings": ["Audio shorter than minimum emotion window"],
                "window_seconds": size, "hop_seconds": hop, "minimum_tail_seconds": minimum,
                "coverage": {"covered_seconds": 0, "uncovered_seconds": duration}, "windows": [],
                "score_semantics": "native_uncalibrated", "accuracy": "unvalidated_for_singing" if mode == "voice" else "unmeasured",
                "dimension_scale": "model native, approximately 0..1" if mode == "voice" else "model native, approximately 1..9"}
    if mode == "voice":
        predict = category_predictor(assets / "category", device)
        predict_rows(predict, x, rows, batch_size)
        del predict
        gc.collect()
        predict = dimension_predictor(assets / "intensity", device)
        predict_rows(predict, x, rows, batch_size)
    else:
        predict = music_predictor(assets / "music-model")
        for row in rows:
            row.update(predict(x[int(row["start"] * 16000):int(row["end"] * 16000)]))
    right, covered = 0.0, 0.0
    for row in rows:
        covered += max(0.0, row["end"] - max(right, row["start"]))
        right = max(right, row["end"])
    uncovered = max(0.0, duration - covered)
    warnings = ["Tail shorter than minimum window remains uncovered"] if uncovered > 1e-7 else []
    return {"mode": mode, "status": ("partial" if warnings else "complete") if rows else "unavailable",
            "warnings": warnings if rows else ["Audio shorter than minimum emotion window"],
            "window_seconds": size, "hop_seconds": hop, "minimum_tail_seconds": minimum,
            "coverage": {"covered_seconds": covered, "uncovered_seconds": uncovered}, "windows": rows,
            "score_semantics": "native_uncalibrated", "accuracy": "unvalidated_for_singing" if mode == "voice" else "unmeasured",
            "dimension_scale": "model native, approximately 0..1" if mode == "voice" else "model native, approximately 1..9"}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--versions" and sys.argv[2] in SETTINGS:
        print(json.dumps(versions(sys.argv[2])))
        return
    request = json.loads(sys.stdin.buffer.read(16384))
    if request.get("mode") not in SETTINGS:
        raise ValueError("Explicit supported emotion mode required")
    execution_settings(request)
    work = Path(request["audio"]).parent
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", MODELSCOPE_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", OMP_NUM_THREADS="4",
                      TF_NUM_INTRAOP_THREADS="4", TF_NUM_INTEROP_THREADS="1",
                      NUMBA_CACHE_DIR=str(work / "numba-cache"), MPLCONFIGDIR=str(work / "matplotlib-cache"),
                      PYTORCH_ENABLE_MPS_FALLBACK="0")
    with contextlib.redirect_stdout(sys.stderr):
        result = analyze(request)
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("Emotion output exceeds bound")
    Path(request["output"]).write_bytes(payload)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Emotion child failed: {type(error).__name__}: {str(error)[:1000]}", file=sys.stderr)
        sys.exit(1)
