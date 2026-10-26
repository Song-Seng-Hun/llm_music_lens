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
- key: KeyExtractor profile "bgate" (Essentia's default; profiles derived from annotated pop/rock
  recordings, the closest fit to the team's pop/K-pop material among the built-in profiles).
- chords: HPCP (12 bins) + ChordsDetection (hop 2048, window 2 s), consecutive identical labels merged.
  Major/minor triads only; reliability "low_reference".
- downbeats: Essentia has no native downbeat tracker; reported as {"status":"not_provided"}.
"""
import json
import math
import os
import resource
import signal
import sys
import wave

TOOL = {"name": "music-lens-rhythm-worker", "version": "0.1.0"}
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

    window = es.Windowing(type="blackmanharris62")
    spectrum = es.Spectrum()
    peaks = es.SpectralPeaks(orderBy="magnitude", magnitudeThreshold=0.00001, minFrequency=20, maxFrequency=3500,
                             maxPeaks=60, sampleRate=RATE)
    hpcp = es.HPCP(size=12, sampleRate=RATE)
    pcps = []
    for frame in es.FrameGenerator(audio, frameSize=FRAME, hopSize=HOP, startFromZero=True):
        f, m = peaks(spectrum(window(frame)))
        pcps.append(hpcp(f, m))
    chords = []
    if pcps:
        labels, strengths = es.ChordsDetection(hopSize=HOP, sampleRate=RATE, windowSize=2.0)(
            essentia.array(np.array(pcps, dtype=np.float32)))
        step = HOP / float(RATE)
        cur = None
        for i, (lab, s) in enumerate(zip(labels, strengths)):
            t = min(i * step, duration)
            s = float(s)
            s = s if math.isfinite(s) else 0.0
            if cur is not None and cur["label"] == lab:
                cur["_s"] += s
                cur["_n"] += 1
                continue
            if cur is not None:
                cur["end"] = t
                chords.append(cur)
            cur = {"start": t, "end": duration, "label": str(lab)[:16], "_s": s, "_n": 1}
        if cur is not None:
            chords.append(cur)
        chords = [{"start": r3(c["start"]), "end": r3(c["end"]), "label": c["label"],
                   "strength": round(c["_s"] / c["_n"], 3)} for c in chords if c["end"] > c["start"]]
    if not chords:
        warnings.append("no_chords: no chord segment was produced")

    return {
        "bpm": round(bpm, 2),
        "bpm_confidence": round(conf, 3),
        "bpm_confidence_scale": "RhythmExtractor2013 multifeature native, 0..~5.32, uncalibrated",
        "bpm_estimates": bpm_estimates,
        "beats": beats,
        "downbeats": {"status": "not_provided", "reason": "Essentia has no native downbeat tracker"},
        "key": {"tonic": str(tonic), "scale": str(scale), "strength": round(kstrength, 3), "profile": KEY_PROFILE},
        "chords": chords,
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
