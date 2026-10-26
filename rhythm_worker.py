"""Music Lens LAN API optional rhythm layer worker (Essentia 2.1-beta6-dev DSP plus one pinned local TempoCNN model,
no network).

Usage: <essentia python> -I rhythm_worker.py WAV
       <essentia python> -I rhythm_worker.py --tempo-cnn-model /abs/model.pb WAV   (tests and measurement only; the
       server always passes WAV alone. The SHA-256 pin is not overridable, so the option can only select a
       byte-identical copy or make tempo_cnn unavailable.)
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
- double_tempo_evidence (diagnostic, uncalibrated; no decision, bpm untouched): onset-strength envelope = L1
  half-rectified spectral flux (Essentia Flux) of Hann-windowed 1024-sample frames, hop 512 (11.6 ms), frames centred
  on i*hop, divided by its maximum (0..1). Flux reacts to any broadband or low-frequency attack (a kick), unlike HFC,
  and needs no phase. It is max-pooled over +-30 ms at each tracked beat and at the midpoint of each consecutive
  beat pair (gap 0.12..1.5 s): 30 ms absorbs tracker jitter plus the one-frame flux lag, and stays far below the
  shortest half-beat (144 ms at 208 BPM), so beat and midpoint windows never overlap. If the true tempo is double,
  strong onsets should fall at the midpoints too.
- tempo_cnn (learned estimate, uncalibrated; bpm untouched): the pinned deeptemp-k16-3 TempoCNN model, loaded only
  after the file is verified (regular file opened with O_NOFOLLOW, <= 64 MiB, SHA-256 equal to the pin); otherwise
  {"status":"unavailable","reason"} and the rest runs as before. Input: the 16 kHz source resampled to 11025 Hz
  (Essentia Resample, the model's inference rate). TempoCNN (aggregation "majority") gives the global BPM and one
  BPM per ~11.9 s patch (hop ~5.9 s; audio under ~12 s gives no patch); TensorflowPredictTempoCNN on the same
  audio and model gives each patch's 256-class softmax. Verified on synthetic audio (2026-10-26): predictions have
  shape (patches, 256), rows sum to 1, and 30 + argmax(row) equals TempoCNN's local BPM for every patch, i.e.
  class i = 30 + i BPM (30..285; the model json says "last ... 286"), while TempoCNN's localTempoProbabilities is
  only the per-patch maximum (shape (patches,)), so `top` comes from the mean of the softmax rows. The worker
  re-checks that argmax relation on every run. Any exception -> {"status":"failed","code"}; never fails the worker.
  Residual: the file could change between hashing and the Essentia load (same trust as the worker file itself).
"""
import errno
import hashlib
import json
import math
import os
import resource
import signal
import stat
import statistics
import sys
import wave

TOOL = {"name": "music-lens-rhythm-worker", "version": "0.2.2"}
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
ONSET_FRAME, ONSET_HOP = 1024, 512
ONSET_METHOD = "spectral_flux_l1_halfrect_hann1024_hop512_maxnorm"
DTE_WINDOW = 0.030  # max-pool half-width, s
DTE_STRONG = 0.5  # a midpoint is "strong" when its onset >= this x the mean onset of its two flanking beats
DTE_MIN_PAIRS = 7  # ~8 beats
DTE_MIN_BEAT_MEDIAN = 1e-3  # of the envelope maximum; below this the ratio is null (ratio therefore <= 1000)
DTE_GAP = (0.12, 1.5)  # beat pairs outside this gap (s) are skipped (tracker range 40..208 BPM is 0.29..1.5 s)
DTE_NOTE = "diagnostic only, uncalibrated, untested on real songs; do not pick a tempo from it alone"
TEMPO_CNN_MODEL = "/Users/songseunghun/tools/music-lens/models/tempocnn/deeptemp-k16-3.pb"
TEMPO_CNN_SHA256 = "21c328332a221695dd6e8572728c617373064df882e8f81da6d88dc3a821e3b3"
TEMPO_CNN_NAME = "deeptemp-k16-3"
TEMPO_CNN_RATE = 11025
TEMPO_CNN_CLASSES = 256
TEMPO_CNN_MIN_BPM = 30  # class i = 30 + i BPM
TEMPO_CNN_MAX_BYTES = 64 * 1024 * 1024
TEMPO_CNN_TOP = 3
TEMPO_CNN_NOTE = "learned estimate, uncalibrated on team songs; compare with bpm and bpm_alternatives"


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


def _pool(env, hop_s, t, half):
    """Max of env over frames whose centre lies within t +- half (frame i centred at i*hop_s); 0.0 when none."""
    a = max(0, int(math.ceil((t - half) / hop_s - 1e-9)))
    b = min(len(env) - 1, int(math.floor((t + half) / hop_s + 1e-9)))
    return max(env[a:b + 1]) if b >= a else 0.0


def double_tempo_evidence(env, hop_s, beats, half=DTE_WINDOW):
    """env: onset strength per frame (0..1, frame i centred at i*hop_s). Returns (evidence dict, warning|None).
    pairs = consecutive beat pairs with gap in DTE_GAP. mid_to_beat_ratio = median(midpoint onset) / median(beat
    onset over both flanking beats of every pair); mid_strong_fraction = share of pairs whose midpoint onset is
    >= DTE_STRONG x the mean of its two flanking beat onsets (and > 0). Null ratio/fraction (+ warning) when pairs <
    DTE_MIN_PAIRS or the beat median < DTE_MIN_BEAT_MEDIAN."""
    beat_v, mid_v, strong = [], [], 0
    for b1, b2 in zip(beats, beats[1:]):
        if not DTE_GAP[0] <= b2 - b1 <= DTE_GAP[1]:
            continue
        o1, o2 = _pool(env, hop_s, b1, half), _pool(env, hop_s, b2, half)
        om = _pool(env, hop_s, (b1 + b2) / 2.0, half)
        beat_v += [o1, o2]
        mid_v.append(om)
        if om > 0 and om >= DTE_STRONG * (o1 + o2) / 2.0:
            strong += 1
    pairs = len(mid_v)
    bmed = statistics.median(beat_v) if beat_v else None
    mmed = statistics.median(mid_v) if mid_v else None
    ratio = frac = warn = None
    if pairs < DTE_MIN_PAIRS:
        warn = "double_tempo_evidence_unavailable: fewer than %d usable beat pairs" % DTE_MIN_PAIRS
    elif bmed < DTE_MIN_BEAT_MEDIAN:
        warn = "double_tempo_evidence_unavailable: onset strength at beats is ~0"
    else:
        ratio, frac = round(mmed / bmed, 3), round(strong / float(pairs), 3)
    rnd = lambda x: None if x is None else round(min(1.0, max(0.0, x)), 4)
    return ({"method": ONSET_METHOD, "window_ms": round(half * 1000.0, 1), "beat_onset_median": rnd(bmed),
             "mid_onset_median": rnd(mmed), "mid_to_beat_ratio": ratio, "mid_strong_fraction": frac,
             "mid_strong_threshold": DTE_STRONG, "pairs": pairs}, warn)


def verify_model(path, expected=TEMPO_CNN_SHA256, cap=TEMPO_CNN_MAX_BYTES):
    """None when path is a regular file (not a symlink, not a FIFO), <= cap bytes, whose SHA-256 is `expected`;
    otherwise a short reason code."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (FileNotFoundError, NotADirectoryError):
        return "model_missing"
    except OSError as e:
        return "not_regular_file" if e.errno in (errno.ELOOP, errno.EISDIR) else "model_unreadable"
    h = hashlib.sha256()
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return "not_regular_file"
        with os.fdopen(fd, "rb") as f:
            if st.st_size > cap:
                return "model_too_large"
            n = 0
            for chunk in iter(lambda: f.read(65536), b""):
                n += len(chunk)
                if n > cap:
                    return "model_too_large"
                h.update(chunk)
    except OSError:
        return "model_unreadable"
    return None if h.hexdigest() == expected else "sha256_mismatch"


def tempo_cnn_summary(global_bpm, local_bpms, preds, sha256=TEMPO_CNN_SHA256):
    """global_bpm: TempoCNN global BPM; local_bpms: its per-patch BPMs; preds: TensorflowPredictTempoCNN softmax rows
    (lists of TEMPO_CNN_CLASSES floats) for the same audio. Plain lists in, the tempo_cnn dict out."""
    n = len(local_bpms)
    if n == 0:
        return {"status": "failed", "code": "too_short"}
    nums = [global_bpm] + list(local_bpms) + [p for row in preds for p in row]
    if not all(math.isfinite(float(x)) for x in nums):
        return {"status": "failed", "code": "non_finite"}
    if len(preds) != n or any(len(row) != TEMPO_CNN_CLASSES for row in preds):
        return {"status": "failed", "code": "inconsistent_output"}
    hi = TEMPO_CNN_MIN_BPM + TEMPO_CNN_CLASSES - 1
    for b, row in zip(local_bpms, preds):
        if abs(float(b) - (TEMPO_CNN_MIN_BPM + max(range(len(row)), key=row.__getitem__))) > 0.5:
            return {"status": "failed", "code": "inconsistent_output"}
    if not TEMPO_CNN_MIN_BPM <= global_bpm <= hi:
        return {"status": "failed", "code": "inconsistent_output"}
    mean = [sum(col) / float(n) for col in zip(*preds)]
    top = sorted(range(len(mean)), key=lambda i: (-mean[i], i))[:TEMPO_CNN_TOP]
    loc = [float(b) for b in local_bpms]
    return {"status": "done", "model": TEMPO_CNN_NAME, "model_sha256": sha256, "bpm": round(float(global_bpm), 2),
            "local_bpm_median": round(statistics.median(loc), 2), "local_bpm_min": round(min(loc), 2),
            "local_bpm_max": round(max(loc), 2), "local_count": n,
            "top": [{"bpm": float(TEMPO_CNN_MIN_BPM + i), "prob": round(min(1.0, max(0.0, mean[i])), 3)}
                    for i in top],
            "note": TEMPO_CNN_NOTE}


def tempo_cnn(x16, model):
    """tempo_cnn dict for the 16 kHz source; never raises."""
    reason = verify_model(model)
    if reason:
        return {"status": "unavailable", "reason": reason}
    try:
        import essentia.standard as es
        import numpy as np
        a = es.Resample(inputSampleRate=SRC_RATE, outputSampleRate=TEMPO_CNN_RATE, quality=1)(x16)
        a = np.ascontiguousarray(a, dtype=np.float32)
        g, local, _ = es.TempoCNN(graphFilename=model, aggregationMethod="majority")(a)
        preds = np.asarray(es.TensorflowPredictTempoCNN(graphFilename=model)(a), dtype=np.float64)
        rows = preds.tolist() if preds.size else []
        return tempo_cnn_summary(float(g), [float(b) for b in local], rows)
    except Exception as e:  # missing TensorFlow algorithms, graph load or inference error
        return {"status": "failed", "code": "dependency_missing" if isinstance(e, (ImportError, AttributeError))
                else "inference_error"}


def onset_envelope(audio):
    """Normalized (0..1) half-rectified L1 spectral flux per ONSET_HOP frame; frame i centred at i*ONSET_HOP."""
    import essentia.standard as es
    window, spectrum = es.Windowing(type="hann"), es.Spectrum()
    flux = es.Flux(norm="L1", halfRectify=True)
    env = [float(flux(spectrum(window(f))))
           for f in es.FrameGenerator(audio, frameSize=ONSET_FRAME, hopSize=ONSET_HOP, startFromZero=False)]
    if env:
        env[0] = 0.0  # first frame is a difference against nothing
    env = [x if math.isfinite(x) and x > 0 else 0.0 for x in env]
    top = max(env) if env else 0.0
    return [x / top for x in env] if top > 0 else env


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


def analyze(path, model=TEMPO_CNN_MODEL):
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

    dte, dte_warn = double_tempo_evidence(onset_envelope(audio), ONSET_HOP / float(RATE), beats)
    if dte_warn:
        warnings.append(dte_warn)
    tc = tempo_cnn(x16, model)

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
        "double_tempo_evidence": dte,
        "double_tempo_evidence_note": DTE_NOTE,
        "tempo_cnn": tc,
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
    model = TEMPO_CNN_MODEL
    if len(argv) == 4 and argv[1] == "--tempo-cnn-model" and os.path.isabs(argv[2]):
        model, argv = argv[2], [argv[0], argv[3]]
    if len(argv) != 2 or not os.path.isabs(argv[1]):
        fail_out("invalid_argument", "usage: rhythm_worker.py [--tempo-cnn-model /abs/model.pb] /absolute/path.wav")
    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(WALL_SECONDS)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    except (ValueError, OSError):
        pass
    try:
        out = analyze(argv[1], model)
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
