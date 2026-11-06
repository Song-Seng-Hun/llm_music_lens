"""Ear check: hear whether a stored rhythm result matches the song. Left ear = the original audio, right ear = a synth
replay built only from the result's rows, its loudness following the original. A person listens to both at once.
Local tool for a person, also run by server.py for POST /earcheck (via limit_exec.py --earcheck, spans <= 30 s).
Needs numpy.

Usage: python -I ear_check.py RESULT.json SOURCE.wav START END OUT.wav [LAYERS]
  RESULT.json: the stored worker file (<root>/results-rhythm/<result_id>.json); API responses carry no note rows.
  LAYERS: comma list of melody,bass,other,chords,drums,clicks (default melody,bass,other,chords,drums); at most 60 s.
Synth layers: melody_notes (bright triangle), bass_notes (sine + octave), other_notes (quiet pluck, E2-C6, 0.12 s or
longer), chords (soft pad from the labels; N = silence), drum_hits (kick, snare, hihat, cymbal, tom), clicks at bar starts (bars rows).
Every note plays at the detected pitch; no octave is moved. The original is never mixed into the right ear.
Limits: a note an octave above another note sounding at the same time may be a real note or an overtone; the result
cannot tell them apart, and a filter that dropped such notes was rejected because it cut played octave jumps."""
import json
import re
import struct
import sys

import numpy as np

SR = 44100
MAX_SPAN = 60.0
DEFAULT = ('melody', 'bass', 'other', 'chords', 'drums')
LAYERS = DEFAULT + ('clicks',)
GAIN = {'melody': 1.4, 'bass': 0.8, 'other': 0.25, 'chords': 0.6, 'drums': 0.7}  # melody leads; other is many notes
OTHER_LO, OTHER_HI, OTHER_MIN = 40, 84, 0.12  # E2-C6, s
ROOT = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
QUAL = [('maj7', [0, 4, 7, 11]), ('m7b5', [0, 3, 6, 10]), ('m7', [0, 3, 7, 10]), ('dim', [0, 3, 6]), ('aug', [0, 4, 8]),
        ('sus4', [0, 5, 7]), ('sus2', [0, 2, 7]), ('m6', [0, 3, 7, 9]), ('m', [0, 3, 7]), ('7', [0, 4, 7, 10]),
        ('6', [0, 4, 7, 9]), ('', [0, 4, 7])]


def read_wav(path, a, b):
    """Mono float32 of [a, b) s from a PCM16/24/32 or float32/64 WAV, resampled linearly to SR."""
    with open(path, 'rb') as f:
        h = f.read(12)
        if h[:4] != b'RIFF' or h[8:12] != b'WAVE':
            raise ValueError('not a WAV file')
        fmt = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise ValueError('no data chunk')
            cid, n = hdr[:4], struct.unpack('<I', hdr[4:])[0]
            if cid == b'fmt ':
                d = f.read(n)
                tag, ch, sr, _, _, bits = struct.unpack('<HHIIHH', d[:16])
                if tag == 0xFFFE:
                    tag = struct.unpack('<H', d[24:26])[0]
                fmt = (tag, ch, sr, bits)
                if n & 1:
                    f.read(1)
            elif cid == b'data':
                if fmt is None:
                    raise ValueError('data before fmt')
                tag, ch, sr, bits = fmt
                w = bits // 8
                s0, s1 = int(a * sr), min(int(b * sr), n // (ch * w))
                f.seek(s0 * ch * w, 1)
                raw = f.read(max(0, s1 - s0) * ch * w)
                raw = raw[:len(raw) // (ch * w) * (ch * w)]  # a truncated file: whole frames only
                break
            else:
                f.seek(n + (n & 1), 1)
    if tag == 3:
        x = np.frombuffer(raw, '<f4' if bits == 32 else '<f8').astype(np.float32)
    elif tag != 1:
        raise ValueError('unsupported WAV format %d' % tag)
    elif bits == 16:
        x = np.frombuffer(raw, '<i2').astype(np.float32) / 32768
    elif bits == 24:
        u = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        x = ((u[:, 0] | (u[:, 1] << 8) | (u[:, 2] << 16)) << 8 >> 8).astype(np.float32) / 8388608
    elif bits == 32:
        x = np.frombuffer(raw, '<i4').astype(np.float32) / 2147483648
    else:
        raise ValueError('unsupported bit depth %d' % bits)
    x = x[:len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    if sr != SR:
        t = np.arange(int(len(x) * SR / sr)) * sr / SR
        x = np.interp(t, np.arange(len(x)), x).astype(np.float32)
    return x


def write_wav(path, left, right):
    """16-bit stereo WAV; both channels scaled by one gain so the louder peak is 0.98."""
    peak = max(float(np.abs(left).max(initial=0)), float(np.abs(right).max(initial=0)), 1e-9)
    g = min(1.0, 0.98 / peak)
    d = (np.clip(np.stack([left, right], 1) * g, -1, 1) * 32767).astype('<i2').tobytes()
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', 36 + len(d)) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 1, 2, SR, SR * 4, 4, 16)
                + b'data' + struct.pack('<I', len(d)) + d)


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def env(n, att=0.01, rel=0.08):
    e = np.ones(n, np.float32)
    a, r = min(n, int(att * SR)), min(n, int(rel * SR))
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def put(buf, t0, sig):
    i = int(round(t0 * SR))
    if i >= len(buf) or i + len(sig) <= 0:
        return
    s = sig[max(0, -i):]
    i = max(0, i)
    buf[i:i + len(s)] += s[:len(buf) - i]


def tone(f, dur, kind):
    n = max(1, int(dur * SR))
    t = np.arange(n) / SR
    if kind == 'tri':
        x = 2 * np.abs(2 * ((f * t) % 1) - 1) - 1
        return (x * env(n, 0.005, 0.05) * 0.5).astype(np.float32)
    if kind == 'bass':
        x = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)
        return (x * env(n, 0.005, 0.04) * 0.6).astype(np.float32)
    if kind == 'pluck':
        x = sum(np.sin(2 * np.pi * f * k * t) / k for k in (1, 2, 3, 4))
        return (x * np.exp(-t * 6) * env(n, 0.002, 0.03) * 0.25).astype(np.float32)
    x = sum(np.sin(2 * np.pi * f * k * t) / k ** 2 for k in (1, 2, 3))
    return (x * env(n, 0.08, 0.15) * 0.18).astype(np.float32)


def chord_pcs(label):
    """Pitch classes of a chord label (root, accidental, quality prefix); None for N or an unparsed label."""
    m = re.match(r'([A-G])([#b]?)([^/]*)', label or '')
    if not m:
        return None
    r = (ROOT[m.group(1)] + {'#': 1, 'b': -1, '': 0}[m.group(2)]) % 12
    return [(r + i) % 12 for i in next(iv for name, iv in QUAL if m.group(3).startswith(name))]


def drum(kind, rng):
    if kind == 'kick':
        n = int(0.25 * SR)
        t = np.arange(n) / SR
        f = 50 + 70 * np.exp(-t * 30)
        return (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 14) * 0.9).astype(np.float32)
    if kind == 'snare':
        n = int(0.18 * SR)
        t = np.arange(n) / SR
        return ((rng.standard_normal(n) * 0.5 + np.sin(2 * np.pi * 185 * t) * 0.4) * np.exp(-t * 22) * 0.6).astype(np.float32)
    if kind == 'tom':
        n = int(0.3 * SR)
        t = np.arange(n) / SR
        return (np.sin(2 * np.pi * (110 + 40 * np.exp(-t * 20)) * t) * np.exp(-t * 10) * 0.5).astype(np.float32)
    n = int((0.05 if kind == 'hihat' else 0.6) * SR)
    t = np.arange(n) / SR
    x = np.diff(rng.standard_normal(n + 1)).astype(np.float32)  # crude high-pass
    return (x * np.exp(-t * (60 if kind == 'hihat' else 6)) * (0.12 if kind == 'hihat' else 0.1)).astype(np.float32)


def synth(v, a, b, layers=DEFAULT):
    """Mono synth of [a, b) s from the result's rows; missing layers stay silent."""
    out = np.zeros(int((b - a) * SR), np.float32)
    rng = np.random.default_rng(0)

    def inside(r):
        return r['end'] > a and r['start'] < b

    if 'melody' in layers:
        for r in filter(inside, v.get('melody_notes') or []):
            put(out, r['start'] - a, tone(hz(r['midi']), r['end'] - r['start'], 'tri') * GAIN['melody'])
    if 'bass' in layers:
        for r in filter(inside, v.get('bass_notes') or []):
            put(out, r['start'] - a, tone(hz(r['midi']), r['end'] - r['start'], 'bass') * GAIN['bass'])
    if 'other' in layers:
        for r in filter(inside, v.get('other_notes') or []):
            if OTHER_LO <= r['midi'] <= OTHER_HI and r['end'] - r['start'] >= OTHER_MIN:
                put(out, r['start'] - a, tone(hz(r['midi']), r['end'] - r['start'], 'pluck') * GAIN['other'])
    if 'chords' in layers:
        for r in filter(inside, v.get('chords') or []):
            for pc in chord_pcs(r['label'] if r['label'] != 'N' else None) or []:
                put(out, r['start'] - a, tone(hz(48 + pc + (12 if pc < 5 else 0)), r['end'] - r['start'], 'pad') * GAIN['chords'])
    if 'drums' in layers:
        cache = {}
        for h in v.get('drum_hits') or []:
            if a <= h['time'] < b:
                put(out, h['time'] - a, cache.setdefault(h['class'], drum(h['class'], rng)) * GAIN['drums'])
    if 'clicks' in layers:
        n = int(0.02 * SR)
        cl = (np.sin(2 * np.pi * 1500 * np.arange(n) / SR) * env(n, 0.001, 0.015) * 0.3).astype(np.float32)
        rows = (v.get('bars') or {}).get('rows') or []  # bar starts; the stored downbeats field is only a status
        for t in [r['start'] for r in rows] + [rows[-1]['end']] if rows else []:
            if a <= t < b:
                put(out, t - a, cl)
    return out


def rms(x):
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if len(x) else 0.0


def follow(left, right, win=0.4, cap=8.0):
    """Scale the synth so its short-time loudness follows the original: gain per 0.4 s window = RMS(original) /
    RMS(synth), smoothed over 3 windows, capped at `cap` times the whole-span ratio (a near-silent synth window is not
    blown up). A silent synth stays silent."""
    if rms(right) == 0:
        return right
    w = int(win * SR)
    n = len(left) // w + 1
    lr = np.array([rms(left[i * w:(i + 1) * w]) for i in range(n)])
    rr = np.array([rms(right[i * w:(i + 1) * w]) for i in range(n)])
    base = rms(left) / rms(right)
    g = np.clip(np.where(rr > 1e-6, lr / np.maximum(rr, 1e-9), base), 0, base * cap)
    g = np.convolve(np.pad(g, 1, mode='edge'), np.ones(3) / 3, mode='valid')  # edge padding: no fade at the ends
    t = (np.arange(n) + 0.5) * w
    return right * np.interp(np.arange(len(right)), t, g).astype(np.float32)


def render(v, src, a, b, out, layers=DEFAULT):
    """Write the stereo check file; returns (seconds, rms left, rms right)."""
    if a < 0 or not 0 < b - a <= MAX_SPAN:
        raise ValueError('START must be >= 0 and the span 0-%d s' % MAX_SPAN)
    bad = set(layers) - set(LAYERS)
    if bad:
        raise ValueError('unknown layers: ' + ','.join(sorted(bad)))
    left = read_wav(src, a, b)
    right = synth(v, a, b, layers)
    n = min(len(left), len(right))
    if n == 0:
        raise ValueError('no audio in the source between %.2f and %.2f s' % (a, b))
    left, right = left[:n], follow(left[:n], right[:n])
    write_wav(out, left, right)
    return n / SR, rms(left), rms(right)


if __name__ == '__main__':
    if len(sys.argv) not in (6, 7):
        sys.exit(__doc__.split('\n\n')[1])
    res, src, a, b, out = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
    lay = tuple(sys.argv[6].split(',')) if len(sys.argv) == 7 else DEFAULT
    with open(res, encoding='utf-8') as f:
        v = json.load(f)
    s, l, r = render(v, src, a, b, out, lay)
    print('wrote %s %.1f s layers %s rms L/R %.4f/%.4f' % (out, s, ','.join(lay), l, r))
