"""Notes of the other stem from Spotify Basic Pitch (Apache-2.0), run by rhythm_worker.py in its own environment.

Usage: <basic-pitch python> -I other_notes.py IN.wav OUT.json
IN.wav is the Demucs other stem. OUT.json holds [[start, end, midi], ...]. The model file is pinned here. The thresholds were
chosen on RWC-P songs 1-50; see "Accompaniment notes" in README.md.
"""
import hashlib
import json
import sys

from basic_pitch import ICASSP_2022_MODEL_PATH as MODEL
from basic_pitch.inference import Model, run_inference
from basic_pitch.note_creation import model_output_to_notes

MODEL_SHA256 = "2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec"
ONSET_THRESHOLD, FRAME_THRESHOLD = 0.7, 0.2


def main(wav, out):
    with open(MODEL, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != MODEL_SHA256:
            return 5
    _, events = model_output_to_notes(run_inference(wav, Model(MODEL)), ONSET_THRESHOLD, FRAME_THRESHOLD, include_pitch_bends=False)
    with open(out, "w") as f:
        json.dump([[float(e[0]), float(e[1]), int(e[2])] for e in events], f)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
