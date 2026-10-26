"""Music Lens LAN API optional rhythm layer worker (pure DSP, Essentia 2.1-beta6-dev, no models, no network).

Usage: <essentia python> -I rhythm_worker.py WAV
WAV is the server's already-validated canonical file (PCM mono 16 kHz s16, 0 < duration <= 600 s).
stdout: one JSON object. Error: exit 1, one JSON line {"error":{"code","message"}} on stderr.

Choices:
- Audio is resampled 16 kHz -> 44.1 kHz (Essentia Resample) because RhythmExtractor2013 has no sample-rate
  parameter and assumes 44.1 kHz; KeyExtractor/HPCP/ChordsDetection then run at the same rate. The source
  carries nothing above 8 kHz; that does not matter for beats, key or chroma below 5 kHz.
- bpm / beats / bpm_confidence: RhythmExtractor2013 method "multifeature". Its confidence is native,
  uncalibrated, on a 0..~5.32 scale (Essentia: >3.5 means very likely correct, <1.5 low).
- bpm_alternatives: [bpm/2, bpm*2] kept within 40..300. The metrical level (half/double tempo) is ambiguous for the
  tracker; no heuristic picks one.
- key: KeyExtractor profile "bgate" (Essentia's default; fit to real music not measured here).
- key_sections: the same KeyExtractor on fixed 20 s windows (a last window under 10 s joins the previous one), then
  adjacent windows with the same tonic+scale merged (strength = plain mean of the window strengths, null if any is
  null). 20 s holds ~8-10 bars at common tempos, enough chroma for a key profile; shorter windows flip on single
  chords. Boundary resolution is therefore one window (20 s). Weak (low strength) sections keep their row.
- chords: HPCP (12 bins) + ChordsDetection (hop 2048, window 2 s) frame labels. With beats, labels are re-voted per
  beat interval (plus the head before the first beat and the tail after the last): the label with the largest
  summed strength wins (null strength counts 0; ties: more frames, then earliest frame), then identical neighbours
  merge, so every segment spans >= one beat except head/tail ("chords_grid": "beat"). Without beats, consecutive
  identical frame labels merge ("frame"). Major/minor triads only; reliability "low_reference".
- downbeats: Essentia has no native downbeat tracker; reported as {"status":"not_provided"}.
"""
import json
import math
import os
import resource
import signal
import sys
import wave

TOOL = {"name": "music-lens-rhythm-worker", "version": "0.2.0"}
SRC_RATE = 16000
RATE = 44100
MAX_SECONDS = 600.0
MIN_SECONDS = 1.0  # below this the analysis is refused
SHORT_SECONDS = 10.0  # below this a warning is added
WALL_SECONDS = 330  # self-bound in case the parent never kills us (server wall timeout is 300 s)
CPU_SECONDS = 600
HOP = 2048
FRAME = 4096
KEY_PROFILE = "bgate"
LOW_BPM_CONFIDENCE = 1.5
LOW_KEY_STRENGTH = 0.5
MAX_BEATS = 4000  # 600 s at RhythmExtractor2013's maxTempo 208 yields at most ~2080
MAX_ESTIMATES = 5
ALT_MIN_BPM, ALT_MAX_BPM = 40.0, 300.0
ALT_NOTE = "metrical level is ambiguous; half or double tempo may match a published tempo"
KEY_WINDOW = 20.0
KEY_MIN_TAIL = 10.0  # a last window shorter than this joins the previous one


class Fail(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def fail_out(code, message):
    sys.stderr.write(json.dumps({"error": {"code": code, "message": message[:500]}}) + "\n")
    sys.stderr.flush()
    os._exit(1)


def on_alarm(signum, frame):
    fail_out("timeout", "rhythm worker exceeded its own wall bound")


def r3(x):
    return round(float(x), 3)


def finite(x):
    x = float(x)
    if not math.isfinite(x):
        raise Fail("analysis_failed", "non-finite value from the analyzer")
    return x


def bpm_alternatives(bpm):
    if not bpm > 0:
        return []
    return [a for a in (round(bpm / 2.0, 2), round(bpm * 2.0, 2)) if ALT_MIN_BPM <= a <= ALT_MAX_BPM]


def key_windows(duration):
    """[(start, end)] of KEY_WINDOW s covering [0, duration]; a last window under KEY_MIN_TAIL joins the previous."""
    out, t = [], 0.0
    while t < duration:
        out.append([t, min(t + KEY_WINDOW, duration)])
        t += KEY_WINDOW
    if len(out) > 1 and out[-1][1] - out[-1][0] < KEY_MIN_TAIL:
        last = out.pop()
        out[-1][1] = last[1]
    return [tuple(w) for w in out]


def merge_key_sections(rows):
    """rows: [(start, end, tonic, scale, strength|None)] in time order -> merged section dicts."""
    out = []
    for start, end, tonic, scale, s in rows:
        if out and (out[-1]["tonic"], out[-1]["scale"]) == (tonic, scale):
            out[-1]["end"] = end
            out[-1]["_s"].append(s)
            continue
        out.append({"start": start, "end": end, "tonic": tonic, "scale": scale, "_s": [s]})
    for r in out:
        ss = r.pop("_s")
        r["strength"] = None if any(x is None for x in ss) else round(sum(ss) / len(ss), 3)
        r["start"], r["end"] = r3(r["start"]), r3(r["end"])
    return out


def merge_frames(frames, duration):
    """frames: [(time, label, strength|None)] -> segments of consecutive identical labels (strength mean, null-sticky)."""
    segs = []
    for t, lab, s in frames:
        if segs and segs[-1]["label"] == lab:
            segs[-1]["_s"].append(s)
            continue
        if segs:
            segs[-1]["end"] = t
        segs.append({"start": t, "end": duration, "label": lab, "_s": [s]})
    return _finish(segs)


def _finish(segs):
    out = []
    for c in segs:
        ss = c.pop("_s")
        c["strength"] = None if any(x is None for x in ss) else round(sum(ss) / len(ss), 3)
        c["start"], c["end"] = r3(c["start"]), r3(c["end"])
        if c["end"] > c["start"]:
            out.append(c)
    return out


def beat_chords(frames, beats, duration):
    """Beat-synchronous chords. Intervals: [0, b0), [b0, b1), ..., [b_last, duration]. Per interval the label with the
    largest summed strength among its frames wins (null counts 0; ties: more frames, then earliest frame); an interval
    holding no frame takes the frame nearest its midpoint. Identical neighbours then merge. The segment strength is
    the mean of the winning label's frame strengths in it (null if any of them is null: never a measured 0.0)."""
    if not frames:
        return []
    edges = [0.0]
    for b in beats:
        b = min(float(b), duration)
        if b > edges[-1]:
            edges.append(b)
    if duration > edges[-1]:
        edges.append(duration)
    times = [f[0] for f in frames]
    segs, i, n = [], 0, len(frames)
    for j in range(len(edges) - 1):
        a, b = edges[j], edges[j + 1]
        last = j == len(edges) - 2
        while i < n and times[i] < a:
            i += 1
        k = i
        while k < n and (times[k] < b or (last and times[k] <= b)):
            k += 1
        group = frames[i:k]
        if not group:
            mid = (a + b) / 2.0
            group = [min(frames, key=lambda f: abs(f[0] - mid))]
        votes = {}
        for order, (_, lab, s) in enumerate(group):
            v = votes.setdefault(lab, [0.0, 0, -order, []])
            v[0] += 0.0 if s is None else s
            v[1] += 1
            v[3].append(s)
        lab, (_, _, _, ss) = max(votes.items(), key=lambda kv: tuple(kv[1][:3]))
        if segs and segs[-1]["label"] == lab:
            segs[-1]["end"] = b
            segs[-1]["_s"].extend(ss)
        else:
            segs.append({"start": a, "end": b, "label": lab, "_s": ss})
    return _finish(segs)


def read_wav(path):
    import numpy as np
    try:
        with wave.open(path, "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) != (1, 2, SRC_RATE, "NONE"):
                raise Fail("invalid_audio", "expected PCM mono 16 kHz 16-bit WAV")
            n = w.getnframes()
            if n <= 0 or n > MAX_SECONDS * SRC_RATE:
                raise Fail("invalid_audio", "duration must be within 0..600 s")
            raw = w.readframes(n)
    except (wave.Error, EOFError, OSError) as e:
        raise Fail("invalid_audio", "unreadable WAV: " + type(e).__name__)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return x, len(x) / float(SRC_RATE)


def analyze(path):
    import essentia
    import essentia.standard as es
    import numpy as np

    x16, duration = read_wav(path)
    if duration < MIN_SECONDS:
        raise Fail("too_short", "audio shorter than 1 s")
    warnings = []
    if duration < SHORT_SECONDS:
        warnings.append("short_audio: under 10 s, estimates are unreliable")
    audio = es.Resample(inputSampleRate=SRC_RATE, outputSampleRate=RATE, quality=1)(x16)
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if float(np.max(np.abs(audio))) < 1e-4:
        warnings.append("near_silent: estimates are not meaningful")

    bpm, ticks, conf, estimates, _ = es.RhythmExtractor2013(method="multifeature")(audio)
    bpm, conf = finite(bpm), finite(conf)
    beats = [r3(t) for t in ticks if 0.0 <= float(t) <= duration + 0.05][:MAX_BEATS]
    counts = {}
    for e in estimates:
        e = float(e)
        if math.isfinite(e) and e > 0:
            k = round(e, 1)
            counts[k] = counts.get(k, 0) + 1
    total = sum(counts.values())
    bpm_estimates = [{"bpm": k, "share": round(v / float(total), 3)}
                     for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_ESTIMATES]]
    if not beats:
        warnings.append("no_beats: no beat was detected")
    if conf < LOW_BPM_CONFIDENCE:
        warnings.append("low_bpm_confidence: below 1.5 on the native 0..5.32 scale")

    tonic, scale, kstrength = es.KeyExtractor(profileType=KEY_PROFILE, sampleRate=RATE, frameSize=FRAME,
                                              hopSize=HOP)(audio)
    kstrength = finite(kstrength)
    if kstrength < LOW_KEY_STRENGTH:
        warnings.append("low_key_strength: below 0.5")
    rows, bad_ks = [], False
    for ws, we in key_windows(duration):
        seg = np.ascontiguousarray(audio[int(ws * RATE):int(we * RATE)], dtype=np.float32)
        if len(seg) < FRAME:
            continue
        t_, s_, k_ = es.KeyExtractor(profileType=KEY_PROFILE, sampleRate=RATE, frameSize=FRAME, hopSize=HOP)(seg)
        k_ = float(k_)
        if not math.isfinite(k_):
            k_, bad_ks = None, True
        rows.append((ws, we, str(t_), str(s_), k_))
    key_sections = merge_key_sections(rows)
    if bad_ks:
        warnings.append("key_section_strength_unavailable: non-finite native strength; section strength is null")
    if len(key_sections) > 1:
        warnings.append("key_changes: %d sections on %g s windows (DSP estimate)" % (len(key_sections), KEY_WINDOW))

    window = es.Windowing(type="blackmanharris62")
    spectrum = es.Spectrum()
    peaks = es.SpectralPeaks(orderBy="magnitude", magnitudeThreshold=0.00001, minFrequency=20, maxFrequency=3500,
                             maxPeaks=60, sampleRate=RATE)
    hpcp = es.HPCP(size=12, sampleRate=RATE)
    pcps = []
    for frame in es.FrameGenerator(audio, frameSize=FRAME, hopSize=HOP, startFromZero=True):
        f, m = peaks(spectrum(window(frame)))
        pcps.append(hpcp(f, m))
    chords, frame_count = [], 0
    if pcps:
        labels, strengths = es.ChordsDetection(hopSize=HOP, sampleRate=RATE, windowSize=2.0)(
            essentia.array(np.array(pcps, dtype=np.float32)))
        step = HOP / float(RATE)
        frames, bad_strength = [], False
        for i, (lab, st) in enumerate(zip(labels, strengths)):
            st = float(st)
            if not math.isfinite(st):
                st, bad_strength = None, True
            frames.append((min(i * step, duration), str(lab)[:16], st))
        frame_count = len(merge_frames(frames, duration))
        chords = beat_chords(frames, beats, duration) if beats else merge_frames(frames, duration)
        if bad_strength:
            warnings.append("chord_strength_unavailable: non-finite native strength; segment strength is null")
    if not chords:
        warnings.append("no_chords: no chord segment was produced")

    return {
        "bpm": round(bpm, 2),
        "bpm_confidence": round(conf, 3),
        "bpm_confidence_scale": "RhythmExtractor2013 multifeature native, 0..~5.32, uncalibrated",
        "bpm_estimates": bpm_estimates,
        "bpm_alternatives": bpm_alternatives(round(bpm, 2)),
        "bpm_alternatives_note": ALT_NOTE,
        "beats": beats,
        "downbeats": {"status": "not_provided", "reason": "Essentia has no native downbeat tracker"},
        "key": {"tonic": str(tonic), "scale": str(scale), "strength": round(kstrength, 3), "profile": KEY_PROFILE},
        "key_sections": key_sections,
        "key_change_count": max(0, len(key_sections) - 1),
        "key_section_window": KEY_WINDOW,
        "chords": chords,
        "chords_grid": "beat" if beats else "frame",
        "chord_frame_segment_count": frame_count,
        "chords_reliability": "low_reference",
        "chord_vocabulary": "major/minor triads (ChordsDetection)",
        "sample_rate": RATE,
        "source_sample_rate": SRC_RATE,
        "duration": r3(duration),
        "clock": "seconds_from_audio_start",
        "tool": dict(TOOL, essentia_version=str(essentia.__version__)),
        "warnings": warnings,
    }


def main(argv):
    if len(argv) != 2 or not os.path.isabs(argv[1]):
        fail_out("invalid_argument", "usage: rhythm_worker.py /absolute/path.wav")
    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(WALL_SECONDS)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    except (ValueError, OSError):
        pass
    try:
        out = analyze(argv[1])
    except Fail as e:
        fail_out(e.code, e.message)
    except ImportError as e:
        fail_out("dependency_missing", "essentia or numpy unavailable: " + type(e).__name__)
    except Exception as e:  # never leak a traceback to the API
        fail_out("analysis_failed", type(e).__name__)
    sys.stdout.write(json.dumps(out, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
