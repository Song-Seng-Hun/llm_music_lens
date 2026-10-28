"""Synthetic test audio (numpy; run with the Essentia python): canonical PCM mono 16 kHz s16 WAV.

Usage: synth_audio.py KIND SECONDS OUT.wav
KIND: click (120 BPM click track), chords (C - Am - F - G, 2 s each, with a soft 120 BPM pulse), mix (same as chords),
      modulation (C major I-IV-V-I for the first half, then D major I-IV-V-I; 2 s chords, soft 120 BPM pulse),
      kick100 (synthetic kick on every beat at 100 BPM only), kick100mid (the same kicks plus equally strong kicks
      exactly halfway between them: an effective 200 BPM pulse),
      abab (song form A-B-A-B in 15 s blocks, soft 120 BPM pulse throughout: A = C - Am - F - G with three soft
      harmonics; B = E - C#m - A - B with eight sawtooth-like harmonics at about +8 dB; best with SECONDS = 60),
      melody (a known 16-note line, 0.5 s per note, A4..A5 in C major (MELODY), six-partial tone over soft C - Am - F - G
      chords),
      melody_ab (abab plus a melody: A blocks carry MELODY_LOW (C4..G4, C major), B blocks MELODY_HIGH (E5..B5,
      E major), 0.5 s per note; best with SECONDS = 60),
      silence (digital silence)
"""
import sys
import wave

import numpy as np

RATE = 16000
SEMITONE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "B": 11}
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


def kicks(n, bpm=100.0, gain=0.8, offset=0.0):
    """Kick: 150 -> 50 Hz exponential pitch drop, 120 ms, exponential decay; first hit at `offset` s."""
    out = np.zeros(n, dtype=np.float64)
    k = np.arange(int(0.12 * RATE))
    t = k / RATE
    f = 50.0 + 100.0 * np.exp(-t / 0.02)
    burst = gain * np.sin(2 * np.pi * np.cumsum(f) / RATE) * np.exp(-t / 0.04)
    period = RATE * 60.0 / bpm
    j = 0
    while True:
        start = int(round(offset * RATE + j * period))
        if start >= n:
            break
        seg = burst[:n - start]
        out[start:start + len(seg)] += seg
        j += 1
    return out


C_MAJOR = [("C", ("C", "E", "G")), ("F", ("F", "A", "C")), ("G", ("G", "B", "D")), ("C", ("C", "E", "G"))]
D_MAJOR = [("D", ("D", "F#", "A")), ("G", ("G", "B", "D")), ("A", ("A", "C#", "E")), ("D", ("D", "F#", "A"))]


def freq(name):
    return 261.63 * 2 ** (SEMITONE[name] / 12.0)


E_MAJOR = [("E", ("E", "G#", "B")), ("C#m", ("C#", "E", "G#")), ("A", ("A", "C#", "E")), ("B", ("B", "D#", "F#"))]
SOFT = ((1, 1.0), (2, 0.5), (3, 0.25))
BRIGHT = tuple((h, 1.0 / h) for h in range(1, 9))


def chords(n, seg_seconds=2.0, progression=PROGRESSION, partials=SOFT, gain=0.05):
    out = np.zeros(n, dtype=np.float64)
    seg = int(seg_seconds * RATE)
    t = np.arange(seg) / RATE
    env = np.minimum(1.0, np.minimum(t / 0.02, (seg_seconds - t) / 0.05))
    i = 0
    for start in range(0, n, seg):
        _, notes = progression[i % len(progression)]
        i += 1
        y = np.zeros(seg)
        for name in notes:
            for octave in (0.5, 1.0):
                f = freq(name) * octave
                for h, a in partials:
                    y += a * np.sin(2 * np.pi * f * h * t)
        y *= env * gain
        m = min(seg, n - start)
        out[start:start + m] += y[:m]
    return out


MELODY = (72, 74, 76, 79, 76, 74, 72, 71, 69, 71, 72, 74, 76, 77, 79, 81)  # MIDI, 0.5 s each, repeating
MELODY_LOW = (60, 62, 64, 65, 67, 65, 64, 62)  # C4 D4 E4 F4 G4 F4 E4 D4
MELODY_HIGH = (76, 78, 80, 81, 83, 81, 80, 78)  # E5 F#5 G#5 A5 B5 A5 G#5 F#5


def melody_line(n, notes, note_seconds=0.5, gain=0.15, offset=0):
    """Harmonic tone (6 partials, 0.6 decay) per note, 0.46 s sounding + 40 ms rest, from sample `offset` on."""
    out = np.zeros(n, dtype=np.float64)
    m = int(note_seconds * RATE)
    t = np.arange(m) / RATE
    env = np.clip(np.minimum(t / 0.01, (note_seconds - 0.04 - t) / 0.01), 0.0, 1.0)
    for i in range((n - offset + m - 1) // m):
        f = 440.0 * 2 ** ((notes[i % len(notes)] - 69) / 12.0)
        y = sum(0.6 ** k * np.sin(2 * np.pi * f * (k + 1) * t) for k in range(6)) * env * gain
        s = offset + i * m
        k = min(m, n - s)
        out[s:s + k] += y[:k]
    return out


def main(kind, seconds, path):
    n = int(float(seconds) * RATE)
    if kind == "click":
        y = clicks(n)
    elif kind in ("chords", "mix"):
        y = chords(n) + clicks(n, gain=0.25)
    elif kind == "modulation":
        half = n // 2
        y = np.concatenate([chords(half, progression=C_MAJOR), chords(n - half, progression=D_MAJOR)])
        y += clicks(n, gain=0.25)
    elif kind == "kick100":
        y = kicks(n)
    elif kind == "kick100mid":
        y = kicks(n) + kicks(n, offset=0.3)  # 0.6 s period at 100 BPM: halfway = 0.3 s
    elif kind == "abab":
        block = 15 * RATE
        parts = []
        for k in range(0, n, block):
            m = min(block, n - k)
            parts.append(chords(m, gain=0.02) if (k // block) % 2 == 0 else
                         chords(m, progression=E_MAJOR, partials=BRIGHT, gain=0.045))
        y = np.concatenate(parts) + clicks(n, gain=0.1)
    elif kind == "melody":
        y = chords(n, gain=0.02) + melody_line(n, MELODY)
    elif kind == "melody_ab":
        block = 15 * RATE
        parts = []
        for k in range(0, n, block):
            m = min(block, n - k)
            if (k // block) % 2 == 0:
                parts.append(chords(m, gain=0.02) + melody_line(m, MELODY_LOW))
            else:
                parts.append(chords(m, progression=E_MAJOR, partials=BRIGHT, gain=0.045) + melody_line(m, MELODY_HIGH))
        y = np.concatenate(parts) + clicks(n, gain=0.1)
    elif kind == "silence":
        y = np.zeros(n)
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
