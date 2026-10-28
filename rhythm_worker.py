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
  0.3.1: both calls get batchSize 64, patchHopSize 128, lastPatchMode "discard" explicitly. Measured (2026-10-28,
  synthetic 12-600 s): patch counts already matched at every length with the shared defaults, but the predictor's
  default batchSize 16 (TempoCNN: 64) changed softmax values by up to 6e-7, while batchSize 64 reproduced TempoCNN's
  per-patch maxima exactly, so a near-tie could flip the argmax (likely cause of one real-song inconsistent_output).
  A patch whose local BPM class is within 1e-6 of the row maximum (about 2x the observed batch noise, far below the
  3-decimal probabilities reported) counts as consistent. inconsistent_output now carries `detail` (class_count_
  mismatch, patch_count_mismatch, argmax_mismatch, global_out_of_range); a patch count off by exactly one (not seen,
  kept as a guard) uses the shared patches plus warning tempo_cnn_patch_count_mismatch.
  Residual: the file could change between hashing and the Essentia load (same trust as the worker file itself).
  0.4.1: one source only. A real song (274 s, ~21 s near-silent tail) failed argmax_mismatch on its last patch with
  a real probability gap of 0.08, and a synthetic track with a 20 s silent tail reproduced it (gap 0.04): TempoCNN
  and TensorflowPredictTempoCNN disagree on near-silent patches, so the 0.3.1 batch-size explanation was wrong.
  TempoCNN is no longer called. Everything comes from TensorflowPredictTempoCNN's per-patch softmax: local BPM = 30 +
  argmax per patch; global bpm = the most frequent local BPM (Essentia documents TempoCNN's global as an aggregation
  of localTempo, recommending "majority" voting), ties: higher mean probability, then lower BPM; top = mean softmax.
  A patch (frames 1024/hop 512 at 11025 Hz, 256 frames, patch hop 128 frames: start 5.944 i s, length 11.935 s) is
  excluded first (`silent_patches`) when more than half of the 1 s energy windows in its span are below SILENCE_DB
  (the sections rule). A plain patch-RMS rule was tried first and kept a patch of 10 s silence + 2 s music (its RMS
  is the music's minus ~8 dB), which voted 30 BPM. All excluded -> failed no_audible_patches.
- silence (0.4.1): SILENCE_DB -60 dBFS (RMS per 1 s energy window; quiet room tone and dithered digital silence sit
  far below it, soft music above). A section with more than half of its energy windows (by midpoint) below it is
  labelled "silence": it takes no letter, is no grouping reference, similarity_to_first null. energy_summary peaks and
  quietest consider only windows with no value below it; silence_s counts the seconds below it.
- energy (0.3.0): RMS dBFS of the 16 kHz source per 1 s window (floor -120). Plain dBFS rather than Essentia Loudness
  (Stevens power law, unitless): a reproducible unit; not perceptual (unweighted, not LUFS). energy_summary: 3 loudest
  non-overlapping 8 s windows (~4 bars at 120 BPM, one phrase) and the quietest one, plain means of the dB values.
- sections (0.3.0, reliability "low_reference"): beat-synchronous HPCP chroma + z-scored MFCC 2-13 (level coefficient
  left out), cosine SSM, Foote checkerboard 2 x 16 beats (a 4-bar loop fits one half) computed only where the full
  kernel fits (truncated edge kernels gave false boundaries), boundaries at novelty >= 0.15 (synthetic: true changes
  0.33-0.38, inside one loop <= 0.12) >= 8 beats apart, then letters by segment-mean similarity >= 0.75 to each
  group's first segment, in order of first appearance. Similarity groups, not verse/chorus. No beats -> [].
- melody (0.4.0, reliability "low_reference"): the predominant pitch of the full mix (usually the lead vocal; intros
  and solos may track an instrument, accompaniment-only passages track the loudest line, e.g. a chord root). No
  vocal separation. EqualLoudness + PredominantPitchMelodia run on the 16 kHz source itself with frameSize 1024
  (64 ms; the 44.1 kHz default 2048 is 46 ms) and hopSize 128 (8 ms); time-based parameters (ms, cents/ms) are rate
  independent, maxFrequency is set to the 8 kHz Nyquist (the default 20 kHz is above it) and the salience range is the
  default 55 Hz..1760 Hz with minFrequency 80 Hz. Measured on synthetic lines (2026-10-28): 16 kHz direct (frame 1024 or
  2048) recovered every note of a 0.5 s line at MIDI 69-81, and 20 of 24 at MIDI 48-60 where the same audio
  resampled to 44.1 kHz with the defaults gave 12; frameSize 512 split notes. PitchContourSegmentation (same hop,
  defaults: min 0.1 s, 60 cents, rms -2) turns the contour into notes; midi is rounded to the nearest semitone (A4 = 69 = 440 Hz).
  Stored notes are capped at MELODY_MAX_NOTES (6000 = 600 s / 0.1 s minimum note length, so never reached by valid
  output; kept as a guard with a warning). Not a transcription: octave errors, split or merged repeated notes.
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

TOOL = {"name": "music-lens-rhythm-worker", "version": "0.4.1"}
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
TEMPO_CNN_BATCH = 64  # TempoCNN's default; the predictor's default is 16 (see tempo_cnn notes in the docstring)
TEMPO_CNN_PATCH_HOP = 128  # both algorithms' default, passed explicitly so the patching matches by construction
TEMPO_CNN_LAST_PATCH = "discard"
TC_FRAME_HOP, TC_PATCH_FRAMES, TC_FRAME = 512, 256, 1024  # TensorflowInputTempoCNN at 11025 Hz (fixed in Essentia)
TEMPO_CNN_NOTE = "learned estimate, uncalibrated on team songs; compare with bpm and bpm_alternatives"
ENERGY_UNIT = "dBFS"
ENERGY_STEP = 1.0  # s; one RMS value per non-overlapping 1 s window of the 16 kHz source
ENERGY_FLOOR = -120.0  # dB; digital silence and anything quieter
ENERGY_MIN_TAIL = 0.5  # s; a last partial window shorter than this is dropped
ENERGY_WINDOW = 8  # s (values) per summary window: ~4 bars at 120 BPM, one phrase
ENERGY_PEAKS = 3
SILENCE_DB = -60.0  # dBFS; an energy window below this counts as silence (sections, energy_summary)
SILENCE_LABEL = "silence"
SEC_KERNEL = 16  # beats per checkerboard half (~4 bars of 4/4): a 4-bar loop sits inside one half
SEC_MIN = 8  # beats; minimum section length and peak-picking neighbourhood (~2 bars)
SEC_NOVELTY_MIN = 0.15  # absolute Foote novelty (-1..1 scale) a boundary needs
SEC_GROUP_MIN = 0.75  # segment similarity (0..1) needed to join an existing label group
SEC_MAX = 40
MEL_FRAME, MEL_HOP = 1024, 128  # at 16 kHz: 64 ms window, 8 ms hop (see docstring)
MEL_MAX_FREQ = 8000.0  # Nyquist of the 16 kHz source
MELODY_MAX_NOTES = 6000  # 600 s / PitchContourSegmentation's 0.1 s minimum note: an upper bound for valid output
MEL_PHRASE_GAP = 1.0  # s; consecutive notes further apart than this are a phrase break, not an interval
MEL_DIR_MIN = 2.0  # semitones of fitted change a section needs to be rising/falling
MEL_DIR_NOTES = 4  # fewer notes in a section: direction null
MEL_METHOD = ("EqualLoudness+PredominantPitchMelodia 16 kHz frame 1024 hop 128 (predominant pitch of the full mix); "
              "PitchContourSegmentation notes, midi rounded")
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
PITCH_CLASS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7,
               "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
SEC_METHOD = ("beat-sync HPCP12+MFCC2-13(z) cosine SSM; Foote checkerboard 2x16 beats (full kernel only), "
              "novelty>=0.15, min 8 beats; groups: segment-mean similarity>=0.75 to the group's first segment")


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


def tempo_cnn_summary(preds, patch_silent, sha256=TEMPO_CNN_SHA256):
    """preds: TensorflowPredictTempoCNN softmax rows (lists of TEMPO_CNN_CLASSES floats); patch_silent: one bool per
    row (True = excluded as near-silent). Local BPM = 30 + argmax per audible patch; bpm =
    the most frequent local BPM (ties: higher mean probability, then lower BPM); top = mean softmax of the audible
    patches. Plain lists in, the tempo_cnn dict out."""
    if not preds:
        return {"status": "failed", "code": "too_short"}
    if not all(math.isfinite(float(x)) for row in preds for x in row):
        return {"status": "failed", "code": "non_finite"}
    for i, row in enumerate(preds):
        if len(row) != TEMPO_CNN_CLASSES:
            return {"status": "failed", "code": "inconsistent_output",
                    "detail": {"code": "class_count_mismatch", "row": i, "classes": len(row)}}
    rows = [row for row, quiet in zip(preds, patch_silent) if not quiet]
    silent = len(preds) - len(rows)
    if not rows:
        return {"status": "failed", "code": "no_audible_patches"}
    k = float(len(rows))
    mean = [sum(col) / k for col in zip(*rows)]
    local = [max(range(len(row)), key=lambda i, r=row: (r[i], -i)) for row in rows]  # argmax, ties: lower class
    votes = {}
    for c in local:
        votes[c] = votes.get(c, 0) + 1
    best = max(votes, key=lambda c: (votes[c], mean[c], -c))
    top = sorted(range(len(mean)), key=lambda i: (-mean[i], i))[:TEMPO_CNN_TOP]
    loc = [float(TEMPO_CNN_MIN_BPM + c) for c in local]
    return {"status": "done", "model": TEMPO_CNN_NAME, "model_sha256": sha256, "bpm": float(TEMPO_CNN_MIN_BPM + best),
            "local_bpm_median": round(statistics.median(loc), 2), "local_bpm_min": min(loc),
            "local_bpm_max": max(loc), "local_count": len(rows), "silent_patches": silent,
            "top": [{"bpm": float(TEMPO_CNN_MIN_BPM + i), "prob": round(min(1.0, max(0.0, mean[i])), 3)}
                    for i in top],
            "note": TEMPO_CNN_NOTE}


def patch_span(i):
    """(start, end) in s of TempoCNN patch i (see docstring)."""
    a = i * TEMPO_CNN_PATCH_HOP * TC_FRAME_HOP / float(TEMPO_CNN_RATE)
    return a, a + ((TC_PATCH_FRAMES - 1) * TC_FRAME_HOP + TC_FRAME) / float(TEMPO_CNN_RATE)


def tempo_cnn(x16, model, energy):
    """tempo_cnn dict for the 16 kHz source; never raises."""
    reason = verify_model(model)
    if reason:
        return {"status": "unavailable", "reason": reason}
    try:
        import essentia.standard as es
        import numpy as np
        a = es.Resample(inputSampleRate=SRC_RATE, outputSampleRate=TEMPO_CNN_RATE, quality=1)(x16)
        a = np.ascontiguousarray(a, dtype=np.float32)
        preds = np.asarray(es.TensorflowPredictTempoCNN(graphFilename=model, batchSize=TEMPO_CNN_BATCH,
                                                        patchHopSize=TEMPO_CNN_PATCH_HOP,
                                                        lastPatchMode=TEMPO_CNN_LAST_PATCH)(a), dtype=np.float64)
        rows = preds.tolist() if preds.size else []
        return tempo_cnn_summary(rows, [section_silent(energy, *patch_span(i)) for i in range(len(rows))])
    except Exception as e:  # missing TensorFlow algorithms, graph load or inference error
        return {"status": "failed", "code": "dependency_missing" if isinstance(e, (ImportError, AttributeError))
                else "inference_error"}


def energy_values(x16, duration):
    """RMS level in dBFS per ENERGY_STEP window of the 16 kHz source ([i, i+1) s; a last partial window of at least
    ENERGY_MIN_TAIL s is kept, a shorter one dropped); floor ENERGY_FLOOR."""
    import numpy as np
    step = int(ENERGY_STEP * SRC_RATE)
    n = int(duration // ENERGY_STEP)
    if duration - n * ENERGY_STEP >= ENERGY_MIN_TAIL or n == 0:
        n += 1
    out = []
    for i in range(n):
        seg = np.asarray(x16[i * step:(i + 1) * step], dtype=np.float64)
        rms = float(np.sqrt(np.mean(seg * seg))) if len(seg) else 0.0
        db = 20.0 * math.log10(rms) if rms > 0 else ENERGY_FLOOR
        out.append(round(min(0.0, max(ENERGY_FLOOR, finite(db))), 2))
    return out


def _window_means(values, w):
    """[(start index, mean)] of every window of w consecutive values (one window of all when fewer)."""
    w = min(w, len(values))
    s = sum(values[:w])
    out = [(0, s / w)]
    for i in range(1, len(values) - w + 1):
        s += values[i + w - 1] - values[i - 1]
        out.append((i, s / w))
    return out, w


def energy_summary(values, duration, step=ENERGY_STEP, w=ENERGY_WINDOW, floor=SILENCE_DB):
    """min/max/median of the values, the ENERGY_PEAKS loudest non-overlapping windows of w values (greedy by mean,
    ties earliest) and the quietest window (ties earliest), both among windows holding no value below `floor` (no
    such window: peaks [] and quietest None). Window means are plain means of the dB values. silence_s = seconds of
    values below `floor`."""
    if not values:
        return None
    wins, w = _window_means(values, w)
    wins = [(i, m) for i, m in wins if min(values[i:i + w]) >= floor]
    silence_s = round(min(duration, sum(1 for x in values if x < floor) * step), 3)
    span = lambda i, m: {"start": r3(i * step), "end": r3(min((i + w) * step, duration)), "mean": round(m, 2)}
    peaks = []
    for i, m in sorted(wins, key=lambda x: (-x[1], x[0])):
        if all(abs(i - j) >= w for j, _ in peaks):
            peaks.append((i, m))
            if len(peaks) == ENERGY_PEAKS:
                break
    quiet = min(wins, key=lambda x: (x[1], x[0])) if wins else None
    return {"unit": ENERGY_UNIT, "window_s": w * step, "min": min(values), "max": max(values),
            "median": round(statistics.median(values), 2), "peaks": [span(i, m) for i, m in peaks],
            "quietest": span(*quiet) if quiet else None, "silence_db": floor, "silence_s": silence_s}


def pick_boundaries(nov, min_gap=SEC_MIN, thresh=SEC_NOVELTY_MIN, cap=SEC_MAX - 1):
    """nov[i]: novelty at boundary index i (None where not computed). Candidates: nov >= thresh and the maximum of
    its +-min_gap neighbourhood (ties: the earliest). Accepted greedily by novelty (ties earliest) when >= min_gap
    from every accepted one and from both ends (0 and len(nov)); at most cap. Returns sorted indices."""
    n = len(nov)
    cand = []
    for i, x in enumerate(nov):
        if x is None or x < thresh or i < min_gap or n - i < min_gap:
            continue
        lo, hi = max(0, i - min_gap), min(n, i + min_gap + 1)
        if all(nov[j] is None or nov[j] < x for j in range(lo, i)) and \
                all(nov[j] is None or nov[j] <= x for j in range(i + 1, hi)):
            cand.append(i)
    out = []
    for i in sorted(cand, key=lambda k: (-nov[k], k)):
        if len(out) >= cap:
            break
        if all(abs(i - j) >= min_gap for j in out):
            out.append(i)
    return sorted(out)


def label_name(k):
    return chr(65 + k) if k < 26 else chr(65 + k // 26 - 1) + chr(65 + k % 26)


def assign_labels(sim, thresh=SEC_GROUP_MIN):
    """sim[a][b]: segment similarity 0..1. In time order each segment joins the label group whose first segment it is
    most similar to (ties: the earlier group) when that similarity >= thresh, else it starts a new label (A, B, ...
    in order of first appearance). Returns [(label, similarity to its group's first segment)]."""
    reps, out = [], []
    for k in range(len(sim)):
        best = max(range(len(reps)), key=lambda g: (sim[k][reps[g]], -g)) if reps else None
        if best is not None and sim[k][reps[best]] >= thresh:
            out.append((label_name(best), round(min(1.0, max(0.0, sim[k][reps[best]])), 3)))
        else:
            reps.append(k)
            out.append((label_name(len(reps) - 1), 1.0))
    return out


def top_chord(chords, start, end):
    """Label with the most chord time inside [start, end] (ties: earliest overlap), or None."""
    acc = {}
    for order, c in enumerate(chords):
        ov = min(c["end"], end) - max(c["start"], start)
        if ov > 0:
            a = acc.setdefault(c["label"], [0.0, -order])
            a[0] += ov
    if not acc:
        return None
    return max(acc.items(), key=lambda kv: (round(kv[1][0], 6), kv[1][1]))[0]


def section_silent(values, start, end, step=ENERGY_STEP, floor=SILENCE_DB):
    """True when more than half of the energy values whose window midpoint lies in [start, end) (else the window
    holding the midpoint) are below `floor`."""
    sel = [v for i, v in enumerate(values) if start <= (i + 0.5) * step < end]
    if not sel:
        sel = [values[min(len(values) - 1, int(((start + end) / 2.0) // step))]]
    return sum(1 for v in sel if v < floor) * 2 > len(sel)


def section_energy(values, start, end, step=ENERGY_STEP):
    """Mean of the energy values whose window midpoint lies in [start, end) (else the window holding the midpoint)."""
    sel = [v for i, v in enumerate(values) if start <= (i + 0.5) * step < end]
    if not sel:
        sel = [values[min(len(values) - 1, int(((start + end) / 2.0) // step))]]
    return round(sum(sel) / len(sel), 2)


def _segment_similarity(ca, cb, za, zb):
    """0.5 x cosine of the mean chroma (both >= 0, so 0..1) + 0.5 x exp(-d^2/2), d = RMS difference of the mean
    standardized MFCCs in z units (identical timbre 1, one z unit apart 0.61)."""
    na, nb = math.sqrt(sum(x * x for x in ca)), math.sqrt(sum(x * x for x in cb))
    cc = sum(x * y for x, y in zip(ca, cb)) / (na * nb) if na > 0 and nb > 0 else 0.0
    d2 = sum((x - y) ** 2 for x, y in zip(za, zb)) / float(len(za))
    return 0.5 * min(1.0, max(0.0, cc)) + 0.5 * math.exp(-d2 / 2.0)


def structure(chroma, mfcc, frame_times, beats, duration, energy, chords):
    """Sections from beat-synchronous chroma + timbre (see SEC_METHOD). chroma/mfcc: per-frame arrays (frames x 12),
    frame_times: their times. Returns (sections, warning|None)."""
    import numpy as np
    edges = [b for b in beats if 0.0 < b < duration]
    if not beats or len(frame_times) == 0:
        return [], "no_sections: no beats, sections need a beat grid"
    nb = len(edges) - 1  # beat intervals [edges[k], edges[k+1])
    if nb < 2 * SEC_KERNEL:
        sections = [(0.0, duration)]
        warn = "sections_single: fewer than %d beat intervals, one section" % (2 * SEC_KERNEL)
    else:
        warn = None
        ft = np.asarray(frame_times, dtype=np.float64)
        lo = np.searchsorted(ft, edges[:-1], side="left")
        hi = np.searchsorted(ft, edges[1:], side="left")
        C, M = np.zeros((nb, 12)), np.zeros((nb, mfcc.shape[1]))
        for k in range(nb):
            a, b = lo[k], hi[k]
            if b <= a:
                a = int(np.argmin(np.abs(ft - (edges[k] + edges[k + 1]) / 2.0)))
                b = a + 1
            C[k], M[k] = chroma[a:b].mean(axis=0), mfcc[a:b].mean(axis=0)
        sd = M.std(axis=0)
        Z = (M - M.mean(axis=0)) / np.where(sd > 1e-9, sd, 1.0)
        unit = lambda X: X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)
        F = np.hstack([unit(C), unit(Z)]) / math.sqrt(2.0)
        S = F @ F.T  # 0.5 cos(chroma) + 0.5 cos(timbre), -1..1
        h = SEC_KERNEL  # only where the full kernel fits: truncated kernels near the ends see part of a loop
        u = np.arange(-h, h)
        g = np.exp(-0.5 * ((u + 0.5) / (h / 2.0)) ** 2) * np.where(u < 0, -1.0, 1.0)
        K = np.outer(g, g)
        K /= np.sum(np.abs(K))
        nov = [None] * (nb + 1)
        for i in range(h, nb - h + 1):
            nov[i] = float(np.sum(K * S[i - h:i + h, i - h:i + h]))
        cut = pick_boundaries(nov, cap=SEC_MAX - 1)
        bounds = [0.0] + [edges[i] for i in cut] + [duration]
        sections = list(zip(bounds[:-1], bounds[1:]))
        idx = [0] + cut + [nb]
        cm = [C[a:b].mean(axis=0).tolist() for a, b in zip(idx[:-1], idx[1:])]
        zm = [Z[a:b].mean(axis=0).tolist() for a, b in zip(idx[:-1], idx[1:])]
    silent = [section_silent(energy, s, e) for s, e in sections]
    keep = [k for k in range(len(sections)) if not silent[k]]  # silence takes no letter and is no reference
    if len(keep) == 1:
        sub = [("A", 1.0)]
    elif keep:
        sim = [[_segment_similarity(cm[a], cm[b], zm[a], zm[b]) for b in keep] for a in keep]
        sub = assign_labels(sim)
    else:
        sub = []
    it = iter(sub)
    labels = [(SILENCE_LABEL, None) if silent[k] else next(it) for k in range(len(sections))]
    out = []
    for (s, e), (lab, sf) in zip(sections, labels):
        out.append({"start": r3(s), "end": r3(e), "label": lab, "energy_mean": section_energy(energy, s, e),
                    "top_chord": top_chord(chords, s, e), "similarity_to_first": sf})
    return out, warn


def note_name(midi):
    """MIDI number -> scientific pitch name with sharps (60 = C4, 69 = A4)."""
    return NOTE_NAMES[midi % 12] + str(midi // 12 - 1)


def _pitch(midi):
    return {"midi": midi, "note": note_name(midi)}


def _wquantile(pairs, q):
    """Duration-weighted quantile of [(midi, weight)]: the smallest midi whose cumulative weight reaches q."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for m, w in pairs:
        acc += w
        if acc >= q * total - 1e-12:
            return m
    return pairs[-1][0]


def _frac(n, d):
    return round(n / float(d), 3) if d else 0.0


def melody_intervals(notes, gap=MEL_PHRASE_GAP):
    """Intervals between consecutive notes at most `gap` s apart (onset minus previous end): repeat (0 semitones),
    step (1-2), leap (>= 3), up and down as fractions of all counted intervals. None when there is none."""
    iv = [b["midi"] - a["midi"] for a, b in zip(notes, notes[1:]) if b["start"] - a["end"] <= gap]
    if not iv:
        return None
    n = len(iv)
    return {"count": n, "repeat": _frac(sum(1 for d in iv if d == 0), n),
            "step": _frac(sum(1 for d in iv if 1 <= abs(d) <= 2), n),
            "leap": _frac(sum(1 for d in iv if abs(d) >= 3), n),
            "up": _frac(sum(1 for d in iv if d > 0), n), "down": _frac(sum(1 for d in iv if d < 0), n),
            "max_gap_s": gap}


def melody_direction(notes, min_notes=MEL_DIR_NOTES, thresh=MEL_DIR_MIN):
    """Least-squares line of midi against note midpoint time; fitted change = slope x (last - first midpoint).
    rising >= +thresh semitones, falling <= -thresh, else flat; None with fewer than min_notes notes."""
    if len(notes) < min_notes:
        return None
    t = [(n["start"] + n["end"]) / 2.0 for n in notes]
    y = [float(n["midi"]) for n in notes]
    tm, ym = sum(t) / len(t), sum(y) / len(y)
    var = sum((a - tm) ** 2 for a in t)
    if var <= 0:
        return "flat"
    change = sum((a - tm) * (b - ym) for a, b in zip(t, y)) / var * (t[-1] - t[0])
    return "rising" if change >= thresh else "falling" if change <= -thresh else "flat"


def melody_summary(notes, voiced, hop_s, n_frames, tonic, scale, sections):
    """notes: [{start, end, midi, note}] sorted; voiced: sorted times (s) of voiced pitch frames; n_frames: all pitch
    frames. notes_per_second = notes / voiced seconds (voiced frames x hop), so rests and gaps do not dilute it.
    Range p5/p95, median and scale degrees are duration-weighted. Section stats take the notes whose onset lies in
    the section ([start, end), the last one closed). Returns the `melody` dict (status done)."""
    import bisect
    vsec = len(voiced) * hop_s
    w = [(n["midi"], max(n["end"] - n["start"], 1e-6)) for n in notes]
    rng = med = degrees = None
    if notes:
        rng = {"low": _pitch(min(n["midi"] for n in notes)), "high": _pitch(max(n["midi"] for n in notes)),
               "p5": _pitch(_wquantile(w, 0.05)), "p95": _pitch(_wquantile(w, 0.95))}
        med = _wquantile(w, 0.5)
        pc = PITCH_CLASS.get(tonic)
        if pc is not None:
            bins = [0.0] * 12
            for m, d in w:
                bins[(m - pc) % 12] += d
            tot = sum(bins)
            degrees = {"tonic": tonic, "scale": scale, "weight": "duration",
                       "bins": [round(b / tot, 3) for b in bins]}
    secs = []
    for k, sec in enumerate(sections):
        last = k == len(sections) - 1
        inside = [n for n in notes if sec["start"] <= n["start"] < sec["end"] or (last and n["start"] == sec["end"])]
        a = bisect.bisect_left(voiced, sec["start"])
        b = bisect.bisect_right(voiced, sec["end"]) if last else bisect.bisect_left(voiced, sec["end"])
        sv = (b - a) * hop_s
        row = {"label": sec["label"], "start": sec["start"], "end": sec["end"], "note_count": len(inside),
               "low": None, "high": None, "median_midi": None, "notes_per_second": None, "peak": None,
               "direction": melody_direction(inside)}
        if inside:
            top = max(inside, key=lambda n: (n["midi"], -n["start"]))
            row.update(low=min(n["midi"] for n in inside), high=top["midi"],
                       median_midi=_wquantile([(n["midi"], max(n["end"] - n["start"], 1e-6)) for n in inside], 0.5),
                       notes_per_second=round(len(inside) / sv, 3) if sv > 0 else None,
                       peak=dict(_pitch(top["midi"]), time=top["start"]))
        secs.append(row)
    return {"status": "done", "method": MEL_METHOD, "reliability": "low_reference",
            "voiced_fraction": _frac(len(voiced), n_frames), "voiced_s": round(vsec, 3), "note_count": len(notes),
            "notes_per_second": round(len(notes) / vsec, 3) if vsec > 0 and notes else None,
            "range": rng, "median_midi": med, "intervals": melody_intervals(notes), "scale_degrees": degrees,
            "sections": secs}


def melody(x16, duration, tonic, scale, sections):
    """(melody dict, notes, warnings) for the 16 kHz source; never raises (failure -> status failed, no notes)."""
    try:
        import essentia.standard as es
        import numpy as np
        a = es.EqualLoudness(sampleRate=SRC_RATE)(x16)
        pitch, _ = es.PredominantPitchMelodia(sampleRate=SRC_RATE, frameSize=MEL_FRAME, hopSize=MEL_HOP,
                                              maxFrequency=MEL_MAX_FREQ)(a)
        pitch = np.asarray(pitch, dtype=np.float64)
        if not np.all(np.isfinite(pitch)):
            return {"status": "failed", "code": "non_finite"}, [], ["melody_failed: non_finite"]
        hop_s = MEL_HOP / float(SRC_RATE)
        voiced = [round(i * hop_s, 6) for i in np.nonzero(pitch > 0)[0].tolist()]
        notes = []
        if voiced:
            on, du, mi = es.PitchContourSegmentation(sampleRate=SRC_RATE, hopSize=MEL_HOP)(
                np.ascontiguousarray(pitch, dtype=np.float32), a)
            for o, d, m in zip(on, du, mi):
                o, d, m = float(o), float(d), float(m)
                if not (math.isfinite(o) and math.isfinite(d) and math.isfinite(m)):
                    return {"status": "failed", "code": "non_finite"}, [], ["melody_failed: non_finite"]
                s, e, mm = r3(min(max(o, 0.0), duration)), r3(min(max(o + d, 0.0), duration)), int(round(m))
                if e > s and 0 <= mm <= 127:
                    notes.append({"start": s, "end": e, "midi": mm, "note": note_name(mm)})
        notes.sort(key=lambda n: (n["start"], n["end"]))
        warns = []
        if not notes:
            warns.append("no_melody: no pitched note was found")
        summary = melody_summary(notes, voiced, hop_s, len(pitch), tonic, scale, sections)
        if summary["scale_degrees"] is None and notes:
            warns.append("melody_scale_degrees_unavailable: key tonic not recognized")
        if len(notes) > MELODY_MAX_NOTES:
            warns.append("melody_notes_capped: %d notes, first %d stored" % (len(notes), MELODY_MAX_NOTES))
            notes = notes[:MELODY_MAX_NOTES]
        return summary, notes, warns
    except Exception as e:  # never fail the worker because of the melody layer
        return ({"status": "failed", "code": "dependency_missing" if isinstance(e, (ImportError, AttributeError))
                 else "analysis_error"}, [], ["melody_failed: " + type(e).__name__])


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
    energy = energy_values(x16, duration)
    tc = tempo_cnn(x16, model, energy)

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
    mfcc = es.MFCC(inputSize=FRAME // 2 + 1, sampleRate=RATE, numberCoefficients=13)
    pcps, mfccs = [], []
    for frame in es.FrameGenerator(audio, frameSize=FRAME, hopSize=HOP, startFromZero=True):
        sp = spectrum(window(frame))
        f, m = peaks(sp)
        pcps.append(hpcp(f, m))
        mfccs.append(mfcc(sp)[1][1:])  # coefficient 0 (overall level) left out: energy is reported on its own
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

    step = HOP / float(RATE)
    sections, sec_warn = structure(np.array(pcps, dtype=np.float64).reshape(-1, 12),
                                   np.array(mfccs, dtype=np.float64).reshape(-1, 12),
                                   [min(i * step, duration) for i in range(len(pcps))], beats, duration, energy, chords)
    if sec_warn:
        warnings.append(sec_warn)
    mel, mel_notes, mel_warn = melody(x16, r3(duration), str(tonic), str(scale), sections)
    warnings.extend(mel_warn)

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
        "energy": {"unit": ENERGY_UNIT, "step_s": ENERGY_STEP, "values": energy},
        "energy_summary": energy_summary(energy, r3(duration)),
        "sections": sections,
        "sections_method": SEC_METHOD,
        "sections_reliability": "low_reference",
        "melody": mel,
        "melody_notes": mel_notes,
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
