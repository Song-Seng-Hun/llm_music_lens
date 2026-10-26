"""Synthetic test audio (numpy; run with the Essentia python): canonical PCM mono 16 kHz s16 WAV.

Usage: synth_audio.py KIND SECONDS OUT.wav
KIND: click (120 BPM click track), chords (C - Am - F - G, 2 s each, with a soft 120 BPM pulse), mix (same as chords)
"""
import sys
import wave

import numpy as np

RATE = 16000
NOTES = {"C": 261.63, "D": 293.66, "E": 329.63, "F": 349.23, "G": 392.00, "A": 440.00, "B": 493.88}
PROGRESSION = [("C", ("C", "E", "G")), ("Am", ("A", "C", "E")), ("F", ("F", "A", "C")), ("G", ("G", "B", "D"))]


def clicks(n, bpm=120.0, gain=0.6):
    out = np.zeros(n, dtype=np.float64)
    period = int(round(RATE * 60.0 / bpm))
    k = np.arange(int(0.03 * RATE))
    burst = gain * np.sin(2 * np.pi * 1000.0 * k / RATE) * np.exp(-k / (0.006 * RATE))
    for start in range(0, n, period):
        seg = burst[:max(0, min(len(burst), n - start))]
        out[start:start + len(seg)] += seg
    return out


def chords(n, seg_seconds=2.0):
    out = np.zeros(n, dtype=np.float64)
    seg = int(seg_seconds * RATE)
    t = np.arange(seg) / RATE
    env = np.minimum(1.0, np.minimum(t / 0.02, (seg_seconds - t) / 0.05))
    i = 0
    for start in range(0, n, seg):
        _, notes = PROGRESSION[i % len(PROGRESSION)]
        i += 1
        y = np.zeros(seg)
        for name in notes:
            for octave in (0.5, 1.0):
                f = NOTES[name] * octave
                for h, a in ((1, 1.0), (2, 0.5), (3, 0.25)):
                    y += a * np.sin(2 * np.pi * f * h * t)
        y *= env * 0.05
        m = min(seg, n - start)
        out[start:start + m] += y[:m]
    return out


def main(kind, seconds, path):
    n = int(float(seconds) * RATE)
    if kind == "click":
        y = clicks(n)
    elif kind in ("chords", "mix"):
        y = chords(n) + clicks(n, gain=0.25)
    else:
        raise SystemExit("unknown kind")
    y = np.clip(y, -0.99, 0.99)
    pcm = (y * 32767).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


if __name__ == "__main__":
    main(*sys.argv[1:4])
