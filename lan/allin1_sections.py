"""Song sections from All-In-One (MIT, mir-aidj/all-in-one) on stems that already exist. Runs in its own environment.

Usage: allin1_sections.py MODEL_DIR STEMS_DIR OUT.json
MODEL_DIR holds the eight harmonix-fold checkpoints; the worker checks their SHA-256 first. STEMS_DIR holds bass.wav,
drums.wav, other.wav and vocals.wav. OUT.json gets {"segments": [{"start", "end", "label"}], "downbeats": [seconds],
"beats": [seconds]}. No download and no separation here.
The checkpoints load with torch's restricted unpickler; the library's own loader uses the unrestricted one.
"""
import json
import os
import sys

CHECKPOINTS = ("harmonix-fold0-0vra4ys2.pth", "harmonix-fold1-3ozjhtsj.pth", "harmonix-fold2-gmgo0nsy.pth",
               "harmonix-fold3-i92b7m8p.pth", "harmonix-fold4-1bql5qo0.pth", "harmonix-fold5-x4z5zeef.pth",
               "harmonix-fold6-x7t226rq.pth", "harmonix-fold7-qwwskhg6.pth")


def main(argv):
    if len(argv) != 4:
        sys.stderr.write("usage: allin1_sections.py MODEL_DIR STEMS_DIR OUT.json\n")
        return 2
    model_dir, stems, out = argv[1:]
    work = os.path.join(os.path.dirname(out), "allin1")
    os.makedirs(work, mode=0o700, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = os.environ["NUMBA_CACHE_DIR"] = work  # imported libraries write caches here
    from pathlib import Path

    import torch
    from allin1.helpers import run_inference
    from allin1.models.allinone import AllInOne
    from allin1.models.ensemble import Ensemble
    from allin1.spectrogram import extract_spectrograms
    from omegaconf import OmegaConf

    models = []
    for name in CHECKPOINTS:
        c = torch.load(os.path.join(model_dir, name), map_location="cpu", weights_only=True)
        m = AllInOne(OmegaConf.create(c["config"]))
        m.load_state_dict(c["state_dict"])
        models.append(m.eval())
    model = Ensemble(models).to("cpu").eval()
    src = Path(stems)
    spec = extract_spectrograms([src], Path(work), False)[0]
    with torch.no_grad():
        r = run_inference(path=src, spec_path=spec, model=model, device="cpu", include_activations=False,
                          include_embeddings=False)
    with open(out, "w") as f:
        json.dump({"segments": [{"start": float(s.start), "end": float(s.end), "label": str(s.label)} for s in r.segments],
                   "downbeats": [float(t) for t in r.downbeats], "beats": [float(t) for t in r.beats]}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
