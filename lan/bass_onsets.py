"""Note starts of a bass stem from Spotify Basic Pitch (Apache-2.0), run by rhythm_worker.py in its own environment.

Usage: <basic-pitch python> -I bass_onsets.py IN.wav OUT.json
IN.wav is PCM mono 16 kHz s16. OUT.json holds a list of note start times in seconds. The model file is pinned here.
"""
import hashlib
import json
import sys

from basic_pitch import ICASSP_2022_MODEL_PATH as MODEL
from basic_pitch.inference import predict

MODEL_SHA256 = "2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec"


def main(wav, out):
    with open(MODEL, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != MODEL_SHA256:
            return 5
    _, _, events = predict(wav, MODEL)
    with open(out, "w") as f:
        json.dump(sorted(float(e[0]) for e in events), f)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
