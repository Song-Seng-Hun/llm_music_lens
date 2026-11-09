"""Optional rhythm layer: config, POST /analyze?rhythm=1, stored file, /report merge, /timeline beats|chords,
/status, plus the real Essentia worker on synthetic audio (skipped when the Essentia python is missing)."""
import fcntl
import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

from helpers import ANALYZE_OUT, PY, REPO, RID64, Base, Client, Env, Harness, server, sha, silent_wav_bytes

import rhythm_worker as rw  # noqa: E402  (stdlib-only at import time; numpy/essentia load inside analyze)

A = "/analyze?language=en&emotion_mode=voice"
AR = A + "&rhythm=1"
WAV = silent_wav_bytes(0.5)
ESSENTIA_PY = os.environ.get("MUSIC_LENS_ESSENTIA_PYTHON", "")
WORKER = os.path.join(REPO, "rhythm_worker.py")
SYNTH = os.path.join(REPO, "tests", "synth_audio.py")

FAKE_WORKER = r'''import fcntl, json, os, sys, time
HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "control.json")) as f:
    ctl = json.load(f)
fd = os.open(ctl["lock"], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    held = False
except BlockingIOError:
    held = True
os.close(fd)
wav = sys.argv[-1]
stdin = sys.stdin.read()
with open(os.path.join(HERE, "calls.log"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "exists": os.path.isfile(wav), "lock_held": held, "stdin": stdin,
                        "path_env": os.environ.get("PATH"), "env_keys": sorted(os.environ),
                        "pid": os.getpid()}) + "\n")
time.sleep(ctl.get("sleep", 0))
for line in ctl.get("stderr_lines", []):
    sys.stderr.write(line + "\n")
if "stdout" in ctl:
    sys.stdout.write(ctl["stdout"])
sys.stdout.flush()
sys.exit(ctl.get("exit", 0))
'''

RHYTHM_OUT = {
    "bpm": 120.0, "bpm_confidence": 3.9, "bpm_confidence_scale": "native 0..~5.32",
    "bpm_estimates": [{"bpm": 120.2, "share": 0.9}],
    "beats": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    "downbeats": {"status": "not_provided"},
    "key": {"tonic": "C", "scale": "major", "strength": 0.9, "profile": "bgate"},
    "chords": [{"start": 0.0, "end": 2.0, "label": "C", "strength": 0.9},
               {"start": 2.0, "end": 4.0, "label": "Am", "strength": 0.8},
               {"start": 4.0, "end": 6.0, "label": "F", "strength": 0.7}],
    "chords_reliability": "low_reference", "sample_rate": 44100, "duration": 6.0,
    "tool": {"name": "fake", "version": "0", "essentia_version": "x"}, "warnings": [],
}


V2_FIELDS = {
    "bpm_alternatives": [60.0, 240.0], "bpm_alternatives_note": "metrical level is ambiguous",
    "key_sections": [{"start": 0.0, "end": 4.0, "tonic": "C", "scale": "major", "strength": 0.9},
                     {"start": 4.0, "end": 6.0, "tonic": "D", "scale": "major", "strength": None}],
    "key_change_count": 1, "key_section_window": 20.0, "chords_grid": "beat", "chord_frame_segment_count": 9,
}
RHYTHM_OUT_V2 = dict(RHYTHM_OUT, warnings=["key_changes: 2 sections on 20 s windows (DSP estimate)"], **V2_FIELDS)
V2_SUMMARY_KEYS = ("bpm_alternatives", "bpm_alternatives_note", "key_sections", "key_change_count", "chords_grid")


def bad_v2():
    """Worker-0.2.0 outputs that must be refused (invalid_output from the worker, invalid_result when stored)."""
    ks = V2_FIELDS["key_sections"]
    out = []
    for over in ({"bpm_alternatives": [float("nan")]}, {"bpm_alternatives": [60, 1e9]}, {"bpm_alternatives": 60},
                 {"bpm_alternatives": [0]}, {"bpm_alternatives": [60] * 5}, {"bpm_alternatives_note": "x" * 201},
                 {"bpm_alternatives_note": None}, {"key_sections": [dict(ks[0], start=5.0, end=4.0)]},
                 {"key_sections": [dict(ks[0], end=60.0)], "key_change_count": 0},
                 {"key_sections": [dict(ks[0], strength=float("inf"))]},
                 {"key_sections": [dict(ks[0], strength="x")]}, {"key_sections": [dict(ks[0], tonic="")]},
                 {"key_sections": [dict(ks[0], tonic="C\n")]}, {"key_sections": [ks[1], ks[0]]},
                 {"key_sections": [ks[0]] * 65, "key_change_count": 0}, {"key_sections": {"x": 1}},
                 {"key_change_count": 0}, {"key_change_count": True}, {"key_change_count": -1},
                 {"key_change_count": 1.0}, {"chords_grid": "bar"}, {"chords_grid": None},
                 {"chord_frame_segment_count": -1}, {"key_section_window": 0}):
        out.append(json.dumps(dict(RHYTHM_OUT_V2, **over)))
    return out


DTE = {"method": "spectral_flux_l1_halfrect_hann1024_hop512_maxnorm", "window_ms": 30.0, "beat_onset_median": 0.82,
       "mid_onset_median": 0.83, "mid_to_beat_ratio": 1.011, "mid_strong_fraction": 1.0, "mid_strong_threshold": 0.5,
       "pairs": 48}
DTE_NULL = dict(DTE, beat_onset_median=None, mid_onset_median=None, mid_to_beat_ratio=None, mid_strong_fraction=None,
                pairs=2)
DTE_WARN = "double_tempo_evidence_unavailable: fewer than 7 usable beat pairs"
RHYTHM_OUT_V21 = dict(RHYTHM_OUT_V2, double_tempo_evidence=DTE, double_tempo_evidence_note="diagnostic only")
V21_SUMMARY_KEYS = ("double_tempo_evidence", "double_tempo_evidence_note")


def bad_v21():
    """Worker-0.2.1 outputs that must be refused."""
    out = []
    for over in ({"mid_to_beat_ratio": float("nan")}, {"mid_to_beat_ratio": 1001}, {"mid_to_beat_ratio": -0.1},
                 {"mid_to_beat_ratio": "1"}, {"mid_strong_fraction": 1.5}, {"beat_onset_median": 2},
                 {"mid_onset_median": float("inf")}, {"pairs": -1}, {"pairs": 1.0}, {"pairs": True},
                 {"pairs": 10 ** 6}, {"window_ms": 0}, {"method": ""}, {"method": "x" * 65}, {"method": "a\nb"},
                 {"mid_strong_threshold": 2}, {"extra": 1}, {"mid_strong_fraction": None},
                 {"mid_to_beat_ratio": None, "mid_strong_fraction": None}, {"beat_onset_median": None},
                 {"pairs": 1}, {"pairs": 6}, {"beat_onset_median": 0}, {"beat_onset_median": 0.0001}):
        out.append(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=dict(DTE, **over))))
    d = dict(DTE)
    del d["pairs"]
    out.append(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=d)))
    for over in ({"double_tempo_evidence": [1]}, {"double_tempo_evidence": None},
                 {"double_tempo_evidence_note": ""}, {"double_tempo_evidence_note": "x" * 201},
                 {"double_tempo_evidence": DTE_NULL}):  # null ratio without the warning
        out.append(json.dumps(dict(RHYTHM_OUT_V21, **over)))
    return out


TC_DONE = {"status": "done", "model": "deeptemp-k16-3", "model_sha256": rw.TEMPO_CNN_SHA256, "bpm": 120.0,
           "local_bpm_median": 120.0, "local_bpm_min": 118.0, "local_bpm_max": 121.0, "local_count": 4,
           "top": [{"bpm": 120.0, "prob": 0.817}, {"bpm": 121.0, "prob": 0.171}, {"bpm": 161.0, "prob": 0.003}],
           "note": "learned estimate, uncalibrated"}
TC_UNAVAILABLE = {"status": "unavailable", "reason": "model_missing"}
TC_FAILED = {"status": "failed", "code": "inference_error"}
RHYTHM_OUT_V22 = dict(RHYTHM_OUT_V21, tempo_cnn=TC_DONE)
V22_SUMMARY_KEYS = ("tempo_cnn",)


def bad_v22():
    """Worker-0.2.2 tempo_cnn values that must be refused."""
    out = []
    top = TC_DONE["top"]
    for over in ({"bpm": float("nan")}, {"bpm": 29.9}, {"bpm": 301}, {"bpm": "120"}, {"bpm": None},
                 {"local_bpm_median": 1e9}, {"local_bpm_min": 125.0}, {"local_bpm_max": 119.0},
                 {"local_count": 0}, {"local_count": 1.0}, {"local_count": True}, {"local_count": 10 ** 6},
                 {"top": []}, {"top": top + [top[0]]}, {"top": [dict(top[0], prob=1.01)]},
                 {"top": [dict(top[0], prob=-0.1)]}, {"top": [dict(top[0], bpm=20.0)]},
                 {"top": [dict(top[0], extra=1)]}, {"top": [{"bpm": 120.0}]}, {"top": [120.0]}, {"top": {"bpm": 1}},
                 {"model_sha256": "AB" * 32}, {"model_sha256": "ab" * 31}, {"model_sha256": None},
                 {"model": ""}, {"model": "x" * 65}, {"note": "a\nb"}, {"note": "x" * 201}, {"status": "Done"},
                 {"extra": 1}):
        out.append(json.dumps(dict(RHYTHM_OUT_V22, tempo_cnn=dict(TC_DONE, **over))))
    d = dict(TC_DONE)
    del d["top"]
    out.append(json.dumps(dict(RHYTHM_OUT_V22, tempo_cnn=d)))
    det = {"code": "argmax_mismatch", "patch": 3, "local_bpm": 87.0, "argmax_bpm": 88.0, "prob_gap": 0.01}
    for d in (dict(det, prob_gap=1.5), dict(det, prob_gap=float("nan")), dict(det, patch=-1), dict(det, patch=1.0),
              dict(det, argmax_bpm=500.0), dict(det, extra=1), {"code": "argmax_mismatch"}, {"code": "other"},
              {"code": "patch_count_mismatch", "local": 40, "predictions": True},
              {"code": "patch_count_mismatch", "local": 10 ** 6, "predictions": 1},
              {"code": "class_count_mismatch", "row": 0, "classes": -1}, {"code": "global_out_of_range", "bpm": 1e9},
              {"code": "global_out_of_range", "bpm": "x"}, None, [1], "argmax_mismatch"):
        out.append(json.dumps(dict(RHYTHM_OUT_V22, tempo_cnn={"status": "failed", "code": "inconsistent_output",
                                                               "detail": d})))
    for tc in ({"status": "failed", "code": "inference_error", "detail": det},  # detail only on inconsistent_output
               {"status": "done", "detail": det}, {"status": "unavailable", "reason": "model_missing", "detail": det},
               {"status": "failed", "code": "inconsistent_output", "detail": det, "extra": 1}):
        out.append(json.dumps(dict(RHYTHM_OUT_V22, tempo_cnn=tc)))
    for tc in (None, [1], "done", {}, {"status": "unavailable"}, dict(TC_UNAVAILABLE, code="x"),
               {"status": "unavailable", "reason": "Model missing"}, {"status": "unavailable", "reason": ""},
               {"status": "unavailable", "reason": "x" * 65},
               {"status": "unavailable", "reason": "9lives"}, {"status": "unavailable", "reason": "a-b"}, {"status": "failed", "reason": "inference_error"},
               {"status": "failed", "code": 5}, dict(TC_FAILED, bpm=120.0), {"status": "running"}):
        out.append(json.dumps(dict(RHYTHM_OUT_V22, tempo_cnn=tc)))
    return out


ENERGY = {"unit": "dBFS", "step_s": 1.0, "values": [-30.0, -29.5, -20.0, -19.0, -18.5, -31.0]}
ENERGY_SUMMARY = {"unit": "dBFS", "window_s": 2.0, "min": -31.0, "max": -18.5, "median": -24.75,
                  "peaks": [{"start": 3.0, "end": 5.0, "mean": -18.75}, {"start": 1.0, "end": 3.0, "mean": -24.75}],
                  "quietest": {"start": 0.0, "end": 2.0, "mean": -29.75}}
SECTIONS = [{"start": 0.0, "end": 2.0, "label": "A", "energy_mean": -29.75, "top_chord": "C", "similarity_to_first": 1.0},
            {"start": 2.0, "end": 4.0, "label": "B", "energy_mean": -19.5, "top_chord": "Am", "similarity_to_first": 1.0},
            {"start": 4.0, "end": 6.0, "label": "A", "energy_mean": -24.75, "top_chord": None,
             "similarity_to_first": 0.8}]
V30_FIELDS = {"energy": ENERGY, "energy_summary": ENERGY_SUMMARY, "sections": SECTIONS,
              "sections_method": "beat-sync chroma+mfcc", "sections_reliability": "low_reference"}
RHYTHM_OUT_V30 = dict(RHYTHM_OUT_V22, **V30_FIELDS)
V30_SUMMARY_KEYS = ("energy_summary", "sections", "sections_method", "sections_reliability")


def bad_v30():
    """Worker-0.3.0 energy/sections values that must be refused."""
    out = []
    sec, es = SECTIONS, ENERGY_SUMMARY
    for e in ({"unit": "dBFS", "step_s": 1.0, "values": []}, dict(ENERGY, values=[float("nan")]),
              dict(ENERGY, values=[-121.0]), dict(ENERGY, values=[0.5]), dict(ENERGY, values=["x"]),
              dict(ENERGY, values=[-20.0] * 602), dict(ENERGY, values=[-20.0] * 10),  # 10 s of values for 6 s
              dict(ENERGY, step_s=0), dict(ENERGY, unit=""), dict(ENERGY, extra=1), {"unit": "dBFS", "step_s": 1.0},
              [1], None):
        out.append(json.dumps(dict(RHYTHM_OUT_V30, energy=e)))
    for over in ({"min": -10.0}, {"median": -40.0}, {"max": 1.0}, {"window_s": 0}, {"peaks": []},
                 {"peaks": es["peaks"] * 2}, {"peaks": [es["peaks"][1], es["peaks"][0]]},  # loudest first
                 {"peaks": [es["peaks"][0], dict(es["peaks"][0], mean=-19.0)]},  # overlapping
                 {"peaks": [dict(es["peaks"][0], end=3.0)]}, {"peaks": [dict(es["peaks"][0], end=60.0)]},
                 {"peaks": [dict(es["peaks"][0], mean=-10.0)]}, {"peaks": [dict(es["peaks"][0], x=1)]},
                 {"quietest": None}, {"quietest": dict(es["quietest"], mean=float("inf"))}, {"unit": None},
                 {"extra": 1}):
        out.append(json.dumps(dict(RHYTHM_OUT_V30, energy_summary=dict(es, **over))))
    for ss in ([dict(sec[0], label="a")], [dict(sec[0], label="AAA")], [dict(sec[0], label="B")],  # must start at A
               [sec[0], dict(sec[1], label="C")], [dict(sec[0], end=0.0)], [dict(sec[0], end=60.0)],
               [sec[1], sec[0]], [sec[0], dict(sec[1], start=1.0)], [dict(sec[0], energy_mean=5.0)],
               [dict(sec[0], similarity_to_first=1.5)], [dict(sec[0], similarity_to_first=None)],
               [dict(sec[0], top_chord="")], [dict(sec[0], top_chord="x" * 17)], [dict(sec[0], top_chord=3)],
               [dict(sec[0], extra=1)], [{k: x for k, x in sec[0].items() if k != "top_chord"}],
               [dict(sec[0], start=i * 0.1, end=(i + 1) * 0.1, label=rw.label_name(i)) for i in range(41)],
               {"A": 1}, None):
        out.append(json.dumps(dict(RHYTHM_OUT_V30, sections=ss)))
    for over in ({"sections_method": ""}, {"sections_method": "x" * 201}, {"sections_reliability": "high"},
                 {"sections_reliability": None}):
        out.append(json.dumps(dict(RHYTHM_OUT_V30, **over)))
    return out


SECTIONS_SILENCE = [dict(SECTIONS[0], label="silence", similarity_to_first=None, energy_mean=-120.0),
                    dict(SECTIONS[1], label="A"), dict(SECTIONS[2], label="silence", similarity_to_first=None)]
ENERGY_SUMMARY_V41 = dict(ENERGY_SUMMARY, silence_db=-60.0, silence_s=0.0)


def bad_v41():
    """Worker-0.4.1 silence labels and energy_summary fields that must be refused."""
    out = []
    ss, es = SECTIONS_SILENCE, ENERGY_SUMMARY_V41
    for sec in ([dict(ss[0], similarity_to_first=1.0)] + ss[1:], [ss[0], dict(ss[1], label="B"), ss[2]],
                [dict(ss[0], label="Silence")] + ss[1:], [dict(ss[0], label="silent")] + ss[1:],
                [ss[0], dict(ss[1], similarity_to_first=None), ss[2]], [dict(ss[0], end=3.0)] + ss[1:]):
        out.append(json.dumps(dict(RHYTHM_OUT_V30, sections=sec)))
    for over in ({"silence_s": -1.0}, {"silence_s": 100.0}, {"silence_s": None}, {"silence_db": 5.0},
                 {"quietest": None}, {"peaks": [], "quietest": es["quietest"]}, {"silence_db": -60.0, "extra": 1},
                 {"silence_db": -20.0}):  # peaks/quietest below the threshold are impossible
        out.append(json.dumps(dict(RHYTHM_OUT_V30, energy_summary=dict(es, **over))))
    es_old = {k: x for k, x in es.items() if k != "silence_s"}
    out.append(json.dumps(dict(RHYTHM_OUT_V30, energy_summary=es_old)))  # both new keys or neither
    out.append(json.dumps(dict(RHYTHM_OUT_V30, energy_summary=dict(ENERGY_SUMMARY, peaks=[]))))  # old shape needs one
    return out


WORDS = [{"text": c, "start": round(i * 0.3, 3), "end": round(i * 0.3 + 0.25, 3), "probability": 0.9}
         for i, c in enumerate("あいうえお")]


def _n(start, end, midi):
    return {"start": start, "end": end, "midi": midi, "note": rw.note_name(midi)}


MELODY_NOTES = [_n(0.0, 0.5, 60), _n(0.5, 1.0, 62), _n(1.0, 1.5, 64), _n(1.5, 1.9, 67), _n(2.0, 2.5, 72),
                _n(2.5, 3.0, 72), _n(4.0, 4.5, 69)]
MELODY_VOICED = [round(i * 0.008, 6) for i in range(750) if any(n["start"] <= i * 0.008 < n["end"]
                                                                for n in MELODY_NOTES)]
MELODY = rw.melody_summary(MELODY_NOTES, MELODY_VOICED, 0.008, 750, "C", "major", SECTIONS)
RHYTHM_OUT_V40 = dict(RHYTHM_OUT_V30, melody=MELODY, melody_notes=MELODY_NOTES)
V40_SUMMARY_KEYS = ("melody",)
MELODY_FAILED = {"status": "failed", "code": "analysis_error"}


def bad_v40():
    """Worker-0.4.0 melody values that must be refused."""
    out = []
    m, ms, ns = MELODY, MELODY["sections"], MELODY_NOTES
    rng, iv, sd = m["range"], m["intervals"], m["scale_degrees"]
    for over in ({"status": "Done"}, {"extra": 1}, {"reliability": "high"}, {"method": ""}, {"voiced_fraction": 1.5},
                 {"voiced_s": 100.0}, {"note_count": 8}, {"note_count": 6}, {"note_count": -1}, {"note_count": 7.0},
                 {"notes_per_second": None}, {"notes_per_second": 25}, {"median_midi": 74.5}, {"median_midi": 59},
                 {"range": None}, {"range": dict(rng, low={"midi": 61, "note": "C#4"})},
                 {"range": dict(rng, high={"midi": 72, "note": "C4"})}, {"range": dict(rng, p5={"midi": 60})},
                 {"range": dict(rng, p95={"midi": 128, "note": "G#9"})}, {"range": dict(rng, extra=rng["low"])},
                 {"intervals": dict(iv, count=7)}, {"intervals": dict(iv, count=0)}, {"intervals": dict(iv, repeat=0.9)},
                 {"intervals": dict(iv, up=0.0, down=0.0)}, {"intervals": dict(iv, leap=-0.1)},
                 {"intervals": dict(iv, extra=1)}, {"scale_degrees": dict(sd, bins=sd["bins"][:11])},
                 {"scale_degrees": dict(sd, bins=[0.5] * 12)}, {"scale_degrees": dict(sd, tonic="D")},
                 {"scale_degrees": dict(sd, weight="count")}, {"scale_degrees": dict(sd, bins=[float("nan")] * 12)},
                 {"sections": ms[:2]}, {"sections": [dict(ms[0], label="B")] + ms[1:]},
                 {"sections": [dict(ms[0], end=2.5)] + ms[1:]}, {"sections": [dict(ms[0], note_count=5)] + ms[1:]},
                 {"sections": [dict(ms[0], low=70)] + ms[1:]}, {"sections": [dict(ms[0], high=66)] + ms[1:]},
                 {"sections": [dict(ms[0], direction="up")] + ms[1:]},
                 {"sections": [dict(ms[0], direction=None)] + ms[1:]},  # 4 notes need a direction
                 {"sections": ms[:2] + [dict(ms[2], direction="flat")]},  # 1 note: direction must be null
                 {"sections": [dict(ms[0], peak=dict(ms[0]["peak"], time=3.0))] + ms[1:]},
                 {"sections": [dict(ms[0], peak=dict(ms[0]["peak"], note="A4"))] + ms[1:]},
                 {"sections": [dict(ms[0], peak=None)] + ms[1:]}, {"sections": [dict(ms[0], extra=1)] + ms[1:]},
                 {"sections": [dict(ms[0], notes_per_second=50)] + ms[1:]}, {"sections": None}):
        out.append(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(m, **over))))
    for notes in (ns[:6], [ns[1], ns[0]] + ns[2:], [dict(ns[0], note="D4")] + ns[1:], [dict(ns[0], midi=60.0)] + ns[1:],
                  [dict(ns[0], midi=128, note="G#9")] + ns[1:], [dict(ns[0], end=0.0)] + ns[1:],
                  ns[:6] + [dict(ns[6], end=60.0)], [dict(ns[0], extra=1)] + ns[1:], {"x": 1}, None,
                  [dict(ns[0], start=float("inf"))] + ns[1:]):
        out.append(json.dumps(dict(RHYTHM_OUT_V40, melody_notes=notes)))
    for over in ({"melody": MELODY_FAILED}, {"melody": dict(MELODY_FAILED, extra=1), "melody_notes": []},
                 {"melody": {"status": "failed", "code": "Bad code"}, "melody_notes": []},
                 {"melody": {"status": "failed"}, "melody_notes": []}, {"melody": None}, {"melody": "done"}):
        out.append(json.dumps(dict(RHYTHM_OUT_V40, **over)))
    out.append(json.dumps({k: x for k, x in RHYTHM_OUT_V40.items() if k != "melody_notes"}))  # both or neither
    out.append(json.dumps({k: x for k, x in RHYTHM_OUT_V40.items() if k != "melody"}))
    return out


SDEBUG = {"kernel_beats": 16, "min_gap_beats": 8, "novelty_threshold": 0.15, "group_threshold": 0.75,
          "novelty": {"times": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0], "values": [0.01, 0.1, 0.3, 0.02, 0.05, -0.01, 0.2]},
          "peaks": [{"time": 2.0, "value": 0.3, "selected": True}, {"time": 3.0, "value": 0.05, "selected": False},
                    {"time": 4.0, "value": 0.2, "selected": True}],
          "section_similarity": [[1.0, 0.5, 0.8], [0.5, 1.0, 0.4], [0.8, 0.4, 1.0]]}
LYRIC = {"status": "done", "method": rw.LYRIC_METHOD, "unit": "char", "n": 8,
         "blocks": [{"start": 0.5, "end": 5.0, "group": 0}], "group_count": 1, "coverage_s": 4.5, "note": rw.LYRIC_NOTE}
LYRIC_6 = dict(LYRIC, blocks=[{"start": 0.0, "end": 4.0, "group": 0}, {"start": 7.0, "end": 11.5, "group": 1},
                              {"start": 15.0, "end": 19.0, "group": 0}], group_count=2, coverage_s=12.5)
RHYTHM_OUT_V43 = dict(RHYTHM_OUT_V40, structure_debug=SDEBUG, lyric_repeats=LYRIC)
V43_SUMMARY_KEYS = ("lyric_repeats",)


def bad_v43():
    """Worker-0.4.3 structure_debug / lyric_repeats values that must be refused."""
    out = []
    d, nv, pk, sim = SDEBUG, SDEBUG["novelty"], SDEBUG["peaks"], SDEBUG["section_similarity"]
    for over in ({"extra": 1}, {"kernel_beats": -1}, {"kernel_beats": 16.0}, {"novelty_threshold": 2},
                 {"group_threshold": None}, {"novelty": None}, {"novelty": dict(nv, times=nv["times"][:-1])},
                 {"novelty": dict(nv, times=[2.0] + nv["times"][1:])}, {"novelty": dict(nv, values=[1.5] * 7)},
                 {"novelty": dict(nv, times=nv["times"][:-1] + [7.0])}, {"novelty": dict(nv, extra=[])},
                 {"novelty": {"times": [1.0] * 5001, "values": [0.0] * 5001}},
                 {"peaks": pk[::-1]}, {"peaks": pk[:1]}, {"peaks": [dict(pk[0], value=0.31)] + pk[1:]},
                 {"peaks": [pk[0], dict(pk[1], selected=True), pk[2]]}, {"peaks": [dict(pk[0], selected=1)] + pk[1:]},
                 {"peaks": [pk[0], pk[0]] + pk[1:]}, {"peaks": [dict(pk[0], extra=1)] + pk[1:]},
                 {"peaks": [{"time": 0.5 + i * 0.001, "value": 0.0, "selected": False} for i in range(301)]},
                 {"section_similarity": sim[:2]}, {"section_similarity": [r[:2] for r in sim]},
                 {"section_similarity": [[1.0, 0.6, 0.8], [0.5, 1.0, 0.4], [0.8, 0.4, 1.0]]},  # not symmetric
                 {"section_similarity": [[1.1, 0.5, 0.8], [0.5, 1.0, 0.4], [0.8, 0.4, 1.0]]},
                 {"section_similarity": "x"}):
        out.append(json.dumps(dict(RHYTHM_OUT_V43, structure_debug=dict(d, **over))))
    # a below-threshold peak cannot be selected; selected peaks must be exactly the section boundaries
    low = dict(d, novelty=dict(nv, values=[0.01, 0.1, 0.1, 0.02, 0.05, -0.01, 0.2]),
               peaks=[dict(pk[0], value=0.1), pk[1], pk[2]])
    out.append(json.dumps(dict(RHYTHM_OUT_V43, structure_debug=low)))
    out.append(json.dumps(dict(RHYTHM_OUT_V43, structure_debug="x")))
    out.append(json.dumps(dict({k: x for k, x in RHYTHM_OUT_V43.items() if k not in ("sections", "melody",
                                                                                      "melody_notes")})))
    b = LYRIC["blocks"]
    for over in ({"status": "Done"}, {"extra": 1}, {"unit": "byte"}, {"n": 0}, {"n": 8.0}, {"method": ""},
                 {"note": None}, {"blocks": None}, {"blocks": [dict(b[0], end=4.0)]},  # 3.5 s < 4 s
                 {"blocks": [dict(b[0], end=7.0)]}, {"blocks": [dict(b[0], group=1)]},
                 {"blocks": [dict(b[0], group=0.0)]},
                 {"blocks": [dict(b[0], extra=1)]}, {"group_count": 2}, {"coverage_s": 4.6}, {"coverage_s": -1},
                 {"blocks": [{"start": 0.0, "end": 4.5, "group": 0}] * 41, "coverage_s": 184.5}):
        out.append(json.dumps(dict(RHYTHM_OUT_V43, lyric_repeats=dict(LYRIC, **over))))
    lb = LYRIC_6["blocks"]
    for blocks in ([lb[1], lb[0], lb[2]], [lb[0], dict(lb[1], start=3.5), lb[2]], [lb[0], dict(lb[1], group=2), lb[2]],
                   [dict(lb[0], group=1), dict(lb[1], group=0), dict(lb[2], group=1)]):
        out.append(json.dumps(dict(RHYTHM_OUT_V43, duration=20.0, lyric_repeats=dict(LYRIC_6, blocks=blocks))))
    for ly in ({"status": "unavailable"}, {"status": "unavailable", "reason": "Too few"},
               {"status": "unavailable", "reason": "no_words", "blocks": []}, {"status": "failed", "reason": "x"},
               {"status": "failed", "code": 5}, {"status": "running"}, None, []):
        out.append(json.dumps(dict(RHYTHM_OUT_V43, lyric_repeats=ly)))
    for bad in ("AB" * 32, "ab" * 31, 5, None):
        out.append(json.dumps(dict(RHYTHM_OUT_V43, words_sha256=bad)))
    return out


GENRE_LABELS = ["Parent%02d---Style%03d" % (i % 15, i) for i in range(rw.GENRE_CLASSES)]


def genre_rows(n, hot=((7, 0.6), (22, 0.3), (8, 0.2))):
    """n patch rows with fixed activations: class 7 (Parent07) 0.6, class 22 (Parent07) 0.3, class 8 0.2, rest 0.01."""
    rows = []
    for _ in range(n):
        r = [0.01] * rw.GENRE_CLASSES
        for i, p in hot:
            r[i] = p
        rows.append(r)
    return rows


GENRE_DONE = rw.genre_summary(genre_rows(5), [False] * 5, GENRE_LABELS, 6.0)  # 6 s holds 5 patches
RHYTHM_OUT_V50 = dict(RHYTHM_OUT_V43, genre=GENRE_DONE)
GENRE_SUMMARY = dict({k: x for k, x in GENRE_DONE.items() if k != "windows"}, window_count=1)


def bad_v50():
    """Worker-0.5.0 genre values that must be refused."""
    out = []
    g, top, par, w = GENRE_DONE, GENRE_DONE["top"], GENRE_DONE["parents"], GENRE_DONE["windows"][0]
    for over in ({"status": "Done"}, {"extra": 1}, {"reliability": "low_reference"}, {"model": ""},
                 {"model_sha256": "AB" * 32}, {"embedding_sha256": None}, {"note": "a\nb"},
                 {"patches": 0}, {"patches": 5.0}, {"patches": 10 ** 6}, {"silent_patches": 5}, {"silent_patches": -1},
                 {"patch_hop_s": 0}, {"window_s": None},
                 {"top": []}, {"top": top + [top[0]]}, {"top": top[::-1]}, {"top": [dict(top[0], score=1.5)]},
                 {"top": [dict(top[0], style="no separator")] + top[1:]}, {"top": [dict(top[0], style="A---B---C")]},
                 {"top": [dict(top[0], style="---B")]}, {"top": [dict(top[0], extra=1)] + top[1:]},
                 {"top": top + [{"style": "P---S%d" % i, "score": 0.0} for i in range(2)]},
                 {"parents": []}, {"parents": par[::-1]}, {"parents": [dict(par[0], score=0.5)] + par[1:]},
                 {"parents": [par[0], par[0]] + par[2:]}, {"parents": [dict(par[0], parent="")] + par[1:]},
                 {"parents": par[:1] + [dict(par[1], score=0.0)] + par[2:]},
                 {"windows": []}, {"windows": [dict(w, start=1.0)]}, {"windows": [dict(w, end=5.0)]},
                 {"windows": [dict(w, end=60.0)]}, {"windows": [dict(w, patches=4)]},
                 {"windows": [dict(w, top=[])]}, {"windows": [dict(w, patches=0)], "silent_patches": 4},
                 {"windows": [dict(w, top=w["top"] + [w["top"][0]])]}, {"windows": [dict(w, extra=1)]},
                 {"windows": [dict(w, end=3.0), dict(w, start=3.5, patches=0, top=[])]}, {"windows": None}):
        out.append(json.dumps(dict(RHYTHM_OUT_V50, genre=dict(g, **over))))
    # a listed style above its listed parent (parent score = max over its styles)
    out.append(json.dumps(dict(RHYTHM_OUT_V50, genre=dict(g, top=[top[0], dict(top[1], style="Parent08---X",
                                                                              score=0.5)] + top[2:]))))
    for gn in ({"status": "unavailable"}, {"status": "unavailable", "reason": "Model missing"},
               {"status": "unavailable", "reason": "too_short", "top": []}, {"status": "failed", "reason": "x"},
               {"status": "failed", "code": 5}, {"status": "ok"}, None, [], "done"):
        out.append(json.dumps(dict(RHYTHM_OUT_V50, genre=gn)))
    return out


class FakeWorker:
    def __init__(self, env):
        self.dir = os.path.join(env.dir, "fakerhythm")
        os.mkdir(self.dir)
        self.path = os.path.join(self.dir, "worker.py")
        with open(self.path, "w") as f:
            f.write(FAKE_WORKER)
        os.chmod(self.path, 0o600)
        self.control = {"lock": os.path.join(env.root, ".analyze.lock"), "stdout": json.dumps(RHYTHM_OUT) + "\n"}
        self.write()

    def write(self, **over):
        self.control.update(over)
        with open(os.path.join(self.dir, "control.json"), "w") as f:
            json.dump(self.control, f)

    def calls(self):
        p = os.path.join(self.dir, "calls.log")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(x) for x in f if x.strip()]


class RhythmBase(Base):
    limits = None

    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        self.h = Harness(self.env, limits=self.limits, rhythm={"python": PY, "worker": self.fw.path})
        self.c = Client(self.env, self.h.port)
        self.write_result()

    def worker_sha(self):
        with open(self.fw.path, "rb") as f:
            return sha(f.read())

    def write_result(self, words=None, rid=RID64, raw=None):
        """The ml result file the server reads the words from (the fake ml writes none)."""
        d = os.path.join(self.env.root, "results")
        os.makedirs(d, 0o700, exist_ok=True)
        with open(os.path.join(d, rid + ".json"), "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps({"result_id": rid, "words": WORDS if words is None
                                                            else words}))

    def ids(self):
        """Server-added identity fields of a file stored now: worker file hash and words payload hash."""
        return {"worker_sha256": self.worker_sha(), "words_sha256": self.h.app.lyric_words(RID64)[1]}

    def rhythm_file(self, rid=RID64):
        return os.path.join(self.env.root, "results-rhythm", rid + ".json")

    def assert_rhythm_failed(self, status, code):
        h = self.c.signed("POST", AR, WAV)
        st, b = self.c.json("POST", AR, WAV, headers=h)
        self.assertEqual(st, 200, b)
        for k, v in ANALYZE_OUT.items():
            self.assertEqual(b[k], v)
        self.assertEqual(b["rhythm"], {"status": status, "code": code})
        self.assertFalse(os.path.exists(self.rhythm_file()))
        rec = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]
        self.assertEqual((rec["state"], rec["http_status"]), ("done", 200))
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])
        return b


class RhythmAnalyzeTest(RhythmBase):
    def test_flag_off_unchanged(self):
        for target in (A, A + "&rhythm=0"):
            h = self.c.signed("POST", target, WAV)
            st, b = self.c.json("POST", target, WAV, headers=h)
            self.assertEqual(st, 200)
            expect = dict(ANALYZE_OUT, request_id=h["X-ML-Nonce"], upload_sha256=sha(WAV), name=None)
            self.assertEqual(b, expect)
        self.assertEqual(self.fw.calls(), [])
        self.assertFalse(os.path.exists(self.rhythm_file()))

    def test_success_stores_and_cleans_up(self):
        os.environ["ML_LEAK_PROBE"] = "1"
        try:
            h = self.c.signed("POST", AR, WAV)
            st, b = self.c.json("POST", AR, WAV, headers=h)
        finally:
            del os.environ["ML_LEAK_PROBE"]
        self.assertEqual(st, 200, b)
        for k, v in ANALYZE_OUT.items():
            self.assertEqual(b[k], v)  # ASR result untouched
        r = b["rhythm"]
        self.assertEqual((r["status"], r["cache_hit"], r["stored"]), ("done", False, True))
        self.assertEqual((r["bpm"], r["key"]["tonic"], r["beat_count"], r["chord_segment_count"]), (120.0, "C", 7, 3))
        self.assertEqual(r["read_next"]["sheet"], "GET /sheet/" + RID64)  # stored, rhythm configured: ear check offered
        self.assertTrue(r["read_next"]["earcheck"].startswith("POST /earcheck/%s?start=<s>&end=<s>" % RID64))
        self.assertEqual((r["bpm_confidence_band"], r["bpm_estimates"]), ("excellent", RHYTHM_OUT["bpm_estimates"]))
        self.assertNotIn("beats", r)  # rows only via /timeline
        self.assertNotIn("chords", r)
        for k in (V2_SUMMARY_KEYS + V21_SUMMARY_KEYS + V22_SUMMARY_KEYS + V30_SUMMARY_KEYS + V40_SUMMARY_KEYS
                  + V43_SUMMARY_KEYS):  # 0.1.0 shape
            self.assertIsNone(r[k], k)
        calls = self.fw.calls()
        self.assertEqual(len(calls), 1)
        inbox_name = self.env.argv_log()[-1][1]
        # same promoted WAV, argv; the ASR words of the result on stdin
        self.assertEqual(calls[0]["argv"], ["--words-stdin", "--time-budget", "300", os.path.join(self.env.inbox, inbox_name)])
        self.assertEqual(json.loads(calls[0]["stdin"]), {"words": [[w["text"], w["start"], w["end"]] for w in WORDS]})
        self.assertTrue(calls[0]["exists"])
        self.assertTrue(calls[0]["lock_held"], "team flock held while the worker runs")
        self.assertEqual(calls[0]["path_env"], server.CHILD_ENV_PATH)
        # child env is built from scratch (the /usr/bin/python3 shim adds its own developer-tool variables)
        self.assertTrue({"HOME", "LANG", "PATH", "TMPDIR"} <= set(calls[0]["env_keys"]))
        self.assertNotIn("ML_LEAK_PROBE", calls[0]["env_keys"])
        p = self.rhythm_file()
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode), 0o700)
        with open(p) as f:  # the server adds the worker file's identity
            self.assertEqual(json.load(f), dict(RHYTHM_OUT, **self.ids()))
        self.assertEqual(os.listdir(os.path.dirname(p)), [RID64 + ".json"])  # no tmp left
        rec = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]
        self.assertEqual((rec["state"], rec["result_id"], rec["error_code"]), ("done", RID64, None))
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(os.listdir(self.env.inbox), [])
        # lock released afterwards
        fd = os.open(os.path.join(self.env.root, ".analyze.lock"), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_sing_notes_line_logged(self):
        self.fw.write(stderr_lines=["UserWarning: noise", "sing_notes: model structure differs; full decode"])
        st, b = self.c.json("POST", AR, WAV, headers=self.c.signed("POST", AR, WAV))
        self.assertEqual((st, b["rhythm"]["status"]), (200, "done"))
        notes = [e for e in self.env.log_events() if e.get("event") == "rhythm_note"]
        self.assertEqual([e["line"] for e in notes], ["sing_notes: model structure differs; full decode"])

    def test_cache_hit_reuses_stored(self):
        self.assertEqual(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"], False)
        self.env.write_control(analyze={"stdout": json.dumps(dict(ANALYZE_OUT, cache_hit=True))})
        st, b = self.c.json("POST", AR, WAV)
        self.assertEqual(st, 200)
        self.assertEqual((b["cache_hit"], b["rhythm"]["status"], b["rhythm"]["cache_hit"]), (True, "done", True))
        self.assertEqual(b["rhythm"]["read_next"]["sheet"], "GET /sheet/" + RID64)
        self.assertEqual(b["rhythm"]["bpm"], 120.0)
        self.assertNotIn("beats", b["rhythm"])
        self.assertEqual(len(self.fw.calls()), 1)  # not recomputed

    def test_worker_failure_keeps_200(self):
        self.fw.write(exit=1, stdout="", stderr_lines=['{"error":{"code":"analysis_failed","message":"x"}}'])
        self.assert_rhythm_failed("failed", "analysis_failed")
        self.fw.write(exit=1, stdout="", stderr_lines=["Traceback: boom"])
        self.assert_rhythm_failed("failed", "internal_error")

    def test_worker_invalid_output(self):
        for out in ("not json", json.dumps(dict(RHYTHM_OUT, beats=[3.0, 1.0])), json.dumps([1]),
                    json.dumps(dict(RHYTHM_OUT, bpm=float("nan"))),
                    json.dumps(dict(RHYTHM_OUT, bpm_estimates=[{"bpm": float("nan"), "share": 1}])),
                    json.dumps(dict(RHYTHM_OUT, bpm_estimates=[{"bpm": 120, "share": float("inf")}])),
                    json.dumps(dict(RHYTHM_OUT, bpm_estimates=[5])),
                    json.dumps(dict(RHYTHM_OUT, tool={"name": {"x": float("nan")}})),
                    json.dumps(dict(RHYTHM_OUT, extra=[float("-inf")])),
                    json.dumps(dict(RHYTHM_OUT, extra=1e400)),
                    json.dumps(RHYTHM_OUT).replace('"duration": 6.0', '"duration": 1' + "0" * 400),
                    json.dumps(RHYTHM_OUT).replace('"sample_rate": 44100', '"sample_rate": 1' + "0" * 400)):
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v2_fields_in_summary(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V2))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual(r["status"], "done", r)
        for k in V2_SUMMARY_KEYS:
            self.assertEqual(r[k], RHYTHM_OUT_V2[k], k)
        self.assertNotIn("chords", r)
        self.assertNotIn("chord_frame_segment_count", r)  # stored file only
        with open(self.rhythm_file()) as f:
            self.assertEqual(json.load(f), dict(RHYTHM_OUT_V2, **self.ids()))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual((r["key_change_count"], r["key_sections"][1]["strength"]), (1, None))

    def test_v2_invalid_output(self):
        for out in bad_v2():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v21_fields_in_summary(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V21))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual(r["status"], "done", r)
        for k in V2_SUMMARY_KEYS + V21_SUMMARY_KEYS:
            self.assertEqual(r[k], RHYTHM_OUT_V21[k], k)
        self.assertEqual((r["bpm"], r["bpm_alternatives"]), (120.0, [60.0, 240.0]))  # no tempo decision
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual(r["double_tempo_evidence"], DTE)

    def test_v21_invalid_output(self):
        for out in bad_v21():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v22_tempo_cnn_in_summary(self):
        for tc in (TC_DONE, TC_UNAVAILABLE, TC_FAILED):
            self.fw.write(stdout=json.dumps(dict(RHYTHM_OUT_V22, tempo_cnn=tc)))
            r = self.c.json("POST", AR, WAV)[1]["rhythm"]
            self.assertEqual((r["status"], r["cache_hit"]), ("done", False), r)
            self.assertEqual(r["tempo_cnn"], tc)
            self.assertEqual((r["bpm"], r["bpm_alternatives"]), (120.0, [60.0, 240.0]))  # no tempo decision
            self.assertEqual(r["double_tempo_evidence"], DTE)
            self.assertEqual(self.c.json("GET", "/report/" + RID64)[1]["rhythm"]["tempo_cnn"], tc)
            os.unlink(self.rhythm_file())  # next round recomputes
        # the tempo_cnn status never changes the rhythm status
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V22, tempo_cnn=dict(TC_DONE, top=[TC_DONE["top"][0]], local_count=1,
                                                                   bpm=30, local_bpm_min=30, local_bpm_median=30,
                                                                   local_bpm_max=300))))
        self.assertIs(server.check_rhythm(v), v)  # bounds inclusive, one top row allowed

    def test_v22_invalid_output(self):
        for out in bad_v22():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v30_sections_and_energy_summary(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V30))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual(r["status"], "done", r)
        for k in V30_SUMMARY_KEYS:
            self.assertEqual(r[k], RHYTHM_OUT_V30[k], k)
        self.assertNotIn("energy", r)  # values only via /timeline layer=energy
        self.assertEqual(r["tempo_cnn"], TC_DONE)
        with open(self.rhythm_file()) as f:
            self.assertEqual(json.load(f), dict(RHYTHM_OUT_V30, **self.ids()))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual([x["label"] for x in r["sections"]], ["A", "B", "A"])
        self.assertEqual(r["energy_summary"]["peaks"][0]["start"], 3.0)
        # valid edge shapes: no sections (no beats), one quiet-only summary, labels past Z, null summary
        many = [dict(SECTIONS[0], start=i * 0.1, end=(i + 1) * 0.1, label=rw.label_name(i)) for i in range(40)]
        for over in ({"sections": []}, {"sections": many}, {"energy_summary": None},
                     {"energy_summary": dict(ENERGY_SUMMARY, peaks=ENERGY_SUMMARY["peaks"][:1])},
                     {"energy": dict(ENERGY, values=[-120.0, 0.0, -50.0, -50.0, -50.0, -50.0, -50.0])}):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V30, **over)))
            self.assertIs(server.check_rhythm(v), v, over)
        self.assertEqual([x["label"] for x in many][24:28], ["Y", "Z", "AA", "AB"])

    def test_v30_invalid_output(self):
        for out in bad_v30():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v40_melody_summary(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V40))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual(r["status"], "done", r)
        self.assertEqual(r["melody"], MELODY)
        self.assertNotIn("melody_notes", r)  # rows only via /timeline layer=melody
        with open(self.rhythm_file()) as f:
            self.assertEqual(json.load(f), dict(RHYTHM_OUT_V40, **self.ids()))
        self.assertEqual(self.c.json("GET", "/report/" + RID64)[1]["rhythm"]["melody"], MELODY)
        # valid edge shapes: failed melody (no rows), no note at all, no sections, capped notes
        empty = rw.melody_summary([], [], 0.008, 750, "C", "major", SECTIONS)
        many = [_n(i * 0.1, i * 0.1 + 0.1, 60 + i % 12) for i in range(server.MELODY_MAX_NOTES)]
        capped = dict(rw.melody_summary(many, [], 0.008, 75000, "C", "major", []), note_count=6100,
                      notes_per_second=10.0, voiced_s=600.0, intervals=None)
        for over in ({"melody": MELODY_FAILED, "melody_notes": []}, {"melody": empty, "melody_notes": []},
                     {"melody": dict(MELODY, sections=[]), "sections": []},
                     {"melody": capped, "melody_notes": many, "sections": [], "duration": 600.0}):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, **over)))
            self.assertIs(server.check_rhythm(v), v, str(over)[:200])
        self.assertEqual(empty["sections"][0], {"label": "A", "start": 0.0, "end": 2.0, "note_count": 0, "low": None,
                                                "high": None, "median_midi": None, "notes_per_second": None,
                                                "peak": None, "direction": None})

    def test_v41_silence_fields(self):
        for over in ({"sections": SECTIONS_SILENCE}, {"energy_summary": ENERGY_SUMMARY_V41},
                     {"sections": [dict(s, label="silence", similarity_to_first=None) for s in SECTIONS]},
                     {"energy_summary": dict(ENERGY_SUMMARY_V41, peaks=[], quietest=None, silence_s=6.0)}):
            self.fw.write(stdout=json.dumps(dict(RHYTHM_OUT_V30, **over)))
            if os.path.exists(self.rhythm_file()):
                os.unlink(self.rhythm_file())  # else the stored file is a cache hit
            r = self.c.json("POST", AR, WAV)[1]["rhythm"]
            self.assertEqual((r["status"], r["cache_hit"]), ("done", False), (over, r))
            k = list(over)[0]
            self.assertEqual(r[k], over[k])
        m = rw.melody_summary(MELODY_NOTES, MELODY_VOICED, 0.008, 750, "C", "major", SECTIONS_SILENCE)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, sections=SECTIONS_SILENCE, melody=m)))
        self.assertIs(server.check_rhythm(v), v)  # melody rows aligned to silence sections too
        os.unlink(self.rhythm_file())  # a valid stored file would be a cache hit
        for out in bad_v41():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v42_note_count_raw(self):
        for raw in (7, 50):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY, note_count_raw=raw))))
            self.assertIs(server.check_rhythm(v), v, raw)
        empty = rw.melody_summary([], [], 0.008, 750, "C", "major", SECTIONS)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(empty, note_count_raw=0), melody_notes=[])))
        self.assertIs(server.check_rhythm(v), v)
        for raw in (6, -1, 7.0, True, None, "7", 10 ** 6):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY, note_count_raw=raw))))
            self.assertIsNone(server.check_rhythm(v), raw)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V40, melody=dict(MELODY_FAILED, note_count_raw=0), melody_notes=[])))
        self.assertIsNone(server.check_rhythm(v))  # done only

    def test_v40_boundary_note_density(self):
        # review F1: a note is attributed to the section holding its onset; its density counts the note's own full
        # length, so a note straddling a section edge (or a section whose only notes start near its end) stays valid
        secs = [dict(SECTIONS[0], start=0.0, end=10.0), dict(SECTIONS[1], start=10.0, end=20.0)]
        base = dict(RHYTHM_OUT_V40, duration=20.0, sections=secs)
        for notes in ([_n(9.992, 10.392, 69)], [_n(2.0, 2.5, 60), _n(9.992, 10.392, 69), _n(12.0, 12.5, 64)],
                      [_n(9.8, 9.95, 67), _n(9.95, 10.4, 69)], [_n(19.9, 20.0, 72)]):
            voiced = [round(i * 0.008, 6) for i in range(2500) if any(x["start"] <= i * 0.008 < x["end"] for x in notes)]
            m = rw.melody_summary(notes, voiced, 0.008, 2500, "C", "major", secs)
            v = json.loads(json.dumps(dict(base, melody=m, melody_notes=notes)))
            self.assertIs(server.check_rhythm(v), v, notes)
            for r in m["sections"]:
                inside = [x for x in notes if r["start"] <= x["start"] < r["end"] or (r is m["sections"][-1]
                                                                                       and x["start"] == r["end"])]
                self.assertEqual(r["notes_per_second"], rw.notes_per_second(inside))
        self.assertEqual(rw.melody_summary([_n(9.992, 10.392, 69)], [], 0.008, 2500, "C", "major", secs)
                         ["sections"][0]["notes_per_second"], 2.5)  # 1 note / 0.4 s, not 1 / 0.008 s voiced
        # the server recomputes the densities from the rows (when not capped): a wrong value is refused
        notes = [_n(9.992, 10.392, 69)]
        m = rw.melody_summary(notes, [], 0.008, 2500, "C", "major", secs)
        for bad in (dict(m, notes_per_second=2.6), dict(m, sections=[dict(m["sections"][0], notes_per_second=125.0),
                                                                    m["sections"][1]]),
                    dict(m, sections=[dict(m["sections"][0], note_count=0, low=None, high=None, median_midi=None,
                                           notes_per_second=None, peak=None),
                                      dict(m["sections"][1], note_count=1, low=69, high=69, median_midi=69,
                                           notes_per_second=2.5, peak={"midi": 69, "note": "A4", "time": 9.992})])):
            v = json.loads(json.dumps(dict(base, melody=bad, melody_notes=notes)))
            self.assertIsNone(server.check_rhythm(v))

    def test_v43_lyric_repeats_in_summary(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V43))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual((r["status"], r["lyric_repeats"]), ("done", LYRIC), r)
        self.assertNotIn("structure_debug", r)  # calibration data: stored file only
        with open(self.rhythm_file()) as f:
            self.assertEqual(json.load(f), dict(RHYTHM_OUT_V43, **self.ids()))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual(r["lyric_repeats"], LYRIC)
        self.assertNotIn("structure_debug", r)
        for ly in ({"status": "unavailable", "reason": "no_words"}, {"status": "failed", "code": "analysis_error"},
                   dict(LYRIC, blocks=[], group_count=0, coverage_s=0.0), dict(LYRIC, unit="word", n=4)):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V43, lyric_repeats=ly)))
            self.assertIs(server.check_rhythm(v), v, ly)
        single = {"sections": [dict(SECTIONS[0], end=6.0)], "melody": MELODY_FAILED, "melody_notes": []}
        for over in ({"structure_debug": None}, {"structure_debug": dict(SDEBUG, section_similarity=None)},
                     dict(single, structure_debug=dict(SDEBUG, novelty={"times": [], "values": []}, peaks=[],
                                                       section_similarity=None)),  # fewer than 32 beat intervals
                     dict(single, structure_debug=dict(SDEBUG, peaks=[SDEBUG["peaks"][1]],
                                                       section_similarity=[[1.0]]))):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V43, **over)))
            self.assertIs(server.check_rhythm(v), v, over)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V43, duration=20.0, lyric_repeats=LYRIC_6)))
        self.assertIs(server.check_rhythm(v), v)
        v = json.loads(json.dumps(RHYTHM_OUT_V40))  # a 0.4.2 file (neither field) stays valid; summary null
        self.assertIs(server.check_rhythm(v), v)
        self.assertIsNone(server.rhythm_summary(v)["lyric_repeats"])

    def test_v43_invalid_output(self):
        for out in bad_v43():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v50_genre_in_summary(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V50))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual((r["status"], r["genre"]), ("done", GENRE_SUMMARY), r)  # windows: timeline only
        self.assertEqual(r["lyric_repeats"], LYRIC)  # earlier fields unchanged
        with open(self.rhythm_file()) as f:
            self.assertEqual(json.load(f), dict(RHYTHM_OUT_V50, **self.ids()))
        self.assertEqual(self.c.json("GET", "/report/" + RID64)[1]["rhythm"]["genre"], GENRE_SUMMARY)
        g = GENRE_DONE
        self.assertEqual([t["style"] for t in g["top"]],
                         ["Parent07---Style007", "Parent07---Style022", "Parent08---Style008", "Parent00---Style000",
                          "Parent01---Style001"])  # ties (0.01): lower class index
        self.assertEqual(g["parents"][:2], [{"parent": "Parent07", "score": 0.6}, {"parent": "Parent08", "score": 0.2}])
        self.assertEqual(g["windows"], [{"start": 0.0, "end": 6.0, "patches": 5, "top": g["top"][:3]}])
        long = rw.genre_summary(genre_rows(70), [False] * 30 + [True] * 31 + [False] * 9, GENRE_LABELS, 70.0)
        for gn in ({"status": "unavailable", "reason": "too_short"}, {"status": "unavailable", "reason": "model_missing"},
                   {"status": "failed", "code": "inference_error"}, dict(g, silent_patches=1, windows=[
                       dict(g["windows"][0], patches=4)])):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V50, genre=gn)))
            self.assertIs(server.check_rhythm(v), v, gn)
            self.assertEqual(server.rhythm_summary(v)["genre"], gn if gn["status"] != "done" else
                             dict({k: x for k, x in gn.items() if k != "windows"}, window_count=1))
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V50, duration=70.0, genre=long)))
        self.assertIs(server.check_rhythm(v), v)
        self.assertEqual(server.rhythm_summary(v)["genre"]["window_count"], 2)
        v = json.loads(json.dumps(RHYTHM_OUT_V43))  # a 0.4.3/0.4.4 file (no genre) stays valid; summary null
        self.assertIs(server.check_rhythm(v), v)
        self.assertIsNone(server.rhythm_summary(v)["genre"])
        for old in (RHYTHM_OUT, RHYTHM_OUT_V22, RHYTHM_OUT_V40):
            v = json.loads(json.dumps(old))
            self.assertIs(server.check_rhythm(v), v)

    def test_v50_invalid_output(self):
        for out in bad_v50():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_words_from_result_file(self):
        # ASR failed / no words: an empty list goes to the worker; rhythm still runs
        self.write_result(words=[])
        self.assertEqual(self.c.json("POST", AR, WAV)[1]["rhythm"]["status"], "done")
        self.assertEqual(json.loads(self.fw.calls()[-1]["stdin"]), {"words": []})
        cases = ((None, {"words": None, "reason": "result_missing"}),
                 ("{", {"words": None, "reason": "result_invalid"}),
                 (json.dumps({"result_id": "cd" * 32, "words": WORDS}), {"words": None, "reason": "result_invalid"}),
                 (json.dumps({"result_id": RID64, "words": [dict(WORDS[0], start=float("nan"))]}),
                  {"words": None, "reason": "result_invalid"}),
                 (json.dumps({"result_id": RID64, "words": [dict(WORDS[0], text=5)]}),
                  {"words": None, "reason": "result_invalid"}),
                 (json.dumps({"result_id": RID64, "words": [dict(WORDS[0], text="x" * 201)]}),
                  {"words": None, "reason": "result_invalid"}),
                 (json.dumps({"result_id": RID64, "words": [WORDS[0]] * 20001}),
                  {"words": None, "reason": "result_invalid"}),
                 (json.dumps({"result_id": RID64, "words": "x"}), {"words": None, "reason": "result_invalid"}),
                 (json.dumps({"result_id": RID64}), {"words": []}),  # no words key: same as an empty ASR
                 (json.dumps({"result_id": RID64, "words": [{"text": "a", "start": 1, "end": 2}]}),
                  {"words": [["a", 1, 2]]}))
        path = os.path.join(self.env.root, "results", RID64 + ".json")
        for raw, want in cases:
            if raw is None:
                os.unlink(path)
            else:
                self.write_result(raw=raw)
            b, h = self.h.app.lyric_words(RID64)
            self.assertEqual((json.loads(b), h), (want, sha(b)), raw and raw[:60])
        # review F2: nesting too deep for the parser, or integers too large for a float, in a matching-id file
        for raw in ('{"result_id":"%s","words":%s%s}' % (RID64, "[" * 5000, "]" * 5000),
                    '{"result_id":"%s","words":[{"text":"a","start":1%s,"end":2}]}' % (RID64, "0" * 400),
                    '{"result_id":"%s","x":%s1%s}' % (RID64, "[" * 100000, "]" * 100000)):
            self.write_result(raw=raw)
            self.assertEqual(json.loads(self.h.app.lyric_words(RID64)[0]), {"words": None, "reason": "result_invalid"})
        self.write_result(raw='{"result_id":"%s","words":%s%s}' % (RID64, "[" * 5000, "]" * 5000))
        st, b = self.c.json("POST", AR, WAV)
        self.assertEqual((st, b["rhythm"]["status"]), (200, "done"), b)  # rhythm unaffected
        self.assertEqual(json.loads(self.fw.calls()[-1]["stdin"]), {"words": None, "reason": "result_invalid"})
        os.unlink(path)
        os.symlink(self.rhythm_file(), path)  # never followed
        self.assertEqual(json.loads(self.h.app.lyric_words(RID64)[0]), {"words": None, "reason": "result_invalid"})
        os.unlink(path)
        os.mkdir(path)
        self.assertEqual(json.loads(self.h.app.lyric_words(RID64)[0]), {"words": None, "reason": "result_invalid"})

    def test_v40_invalid_output(self):
        for out in bad_v40():
            self.fw.write(stdout=out)
            self.assert_rhythm_failed("failed", "invalid_output")

    def test_v21_null_ratio_needs_warning(self):
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=DTE_NULL, warnings=[DTE_WARN])))
        self.assertIs(server.check_rhythm(v), v)
        v = json.loads(json.dumps(dict(v, double_tempo_evidence=dict(DTE_NULL, pairs=0))))
        self.assertIs(server.check_rhythm(v), v)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=dict(DTE, mid_to_beat_ratio=0))))
        self.assertIs(server.check_rhythm(v), v)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=dict(DTE, pairs=0))))
        self.assertIsNone(server.check_rhythm(v))  # a ratio needs pairs
        # numeric ratio/fraction only past the worker's gates (>= 7 pairs, beat median >= 0.001)
        for over in ({"pairs": 1}, {"pairs": 6}, {"beat_onset_median": 0}, {"beat_onset_median": 0.0},
                     {"beat_onset_median": 0.0001}):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=dict(DTE, **over))))
            self.assertIsNone(server.check_rhythm(v), over)
            v = json.loads(json.dumps(dict(v, warnings=[DTE_WARN])))
            self.assertIsNone(server.check_rhythm(v), over)  # a warning does not excuse a numeric ratio
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, double_tempo_evidence=dict(DTE, pairs=7,
                                                                                 beat_onset_median=0.001))))
        self.assertIs(server.check_rhythm(v), v)
        for old in (RHYTHM_OUT, RHYTHM_OUT_V2, RHYTHM_OUT_V21):  # 0.1.0 / 0.2.0 / 0.2.1 shapes stay valid
            v = json.loads(json.dumps(old))
            self.assertIs(server.check_rhythm(v), v)

    def test_v2_single_section_and_empty_alternatives_valid(self):
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V2, bpm_alternatives=[], key_change_count=0, chords_grid="frame",
                                       key_sections=[V2_FIELDS["key_sections"][0]])))
        self.assertIs(server.check_rhythm(v), v)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V2, key_sections=[])))
        self.assertIsNone(server.check_rhythm(v))  # count 1 != max(0, 0 - 1)
        v0 = dict(v, key_change_count=0)
        self.assertIs(server.check_rhythm(v0), v0)

    def test_confidence_band_edges(self):
        self.assertEqual([server.confidence_band(c) for c in (0, 0.99, 1, 1.5, 1.51, 3.5, 3.51, 5.32)],
                         ["very_low", "very_low", "low", "low", "good", "good", "excellent", "excellent"])

    def test_null_chord_strength_accepted(self):
        v = json.loads(json.dumps(RHYTHM_OUT))
        v["chords"][0]["strength"] = None
        v["warnings"] = ["chord_strength_unavailable: non-finite native strength; segment strength is null"]
        self.assertIs(server.check_rhythm(v), v)
        self.assertIsNone(server.check_rhythm(dict(v, chords=[dict(v["chords"][0], strength="x")])))

    def test_busy_when_team_lock_held(self):
        fd = os.open(os.path.join(self.env.root, ".analyze.lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            t0 = time.time()
            self.assert_rhythm_failed("busy", "busy")
            self.assertLess(time.time() - t0, 5)  # never waits for the lock
        finally:
            os.close(fd)
        self.assertEqual(self.fw.calls(), [])

    def test_unavailable_bad_flag_and_signed_target(self):
        st, b = self.c.json("POST", A + "&rhythm=2", WAV)
        self.assertEqual((st, b["error"]["code"]), (400, "bad_request"))
        h = self.c.signed("POST", A, WAV)
        st, b = self.c.json("POST", AR, WAV, headers=h)  # the flag is part of the signed TARGET
        self.assertEqual(st, 401)
        self.assertEqual(self.env.argv_log(), [])


class RhythmCacheIdentityTest(RhythmBase):
    """Stored rhythm files are reused only when their worker_sha256 matches the worker file hashed at App start."""

    def write_stored(self, v):
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(v))

    def stored(self):
        with open(self.rhythm_file()) as f:
            return json.load(f)

    def test_matching_hash_reused(self):
        self.write_stored(dict(RHYTHM_OUT_V2, **self.ids()))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual((r["status"], r["cache_hit"], r["chords_grid"]), ("done", True, "beat"))
        self.assertEqual(self.fw.calls(), [])
        self.assertNotIn("worker_sha256", r)  # stored file and /status only

    def test_mismatched_or_missing_hash_recomputed(self):
        for old in (dict(RHYTHM_OUT_V2, worker_sha256="ab" * 32), RHYTHM_OUT_V2):  # other worker; 0.1/0.2.0 file
            self.write_stored(old)
            n = len(self.fw.calls())
            r = self.c.json("POST", AR, WAV)[1]["rhythm"]
            self.assertEqual((r["status"], r["cache_hit"], r["stored"]), ("done", False, True))
            self.assertEqual(len(self.fw.calls()), n + 1)
            self.assertEqual(self.stored(), dict(RHYTHM_OUT, **self.ids()))  # replaced
            self.assertEqual(os.listdir(os.path.dirname(self.rhythm_file())), [RID64 + ".json"])
            self.assertTrue(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"])  # now reused
            self.assertEqual(len(self.fw.calls()), n + 1)

    def test_recoverable_tempo_cnn_outcome_recomputed(self):
        cases = ({"status": "unavailable", "reason": "model_missing"},
                 {"status": "failed", "code": "inference_error"},
                 {"status": "failed", "code": "dependency_missing"})
        for tc in cases:
            self.write_stored(dict(RHYTHM_OUT_V2, tempo_cnn=tc, **self.ids()))
            n = len(self.fw.calls())
            r = self.c.json("POST", AR, WAV)[1]["rhythm"]
            self.assertEqual((r["status"], r["cache_hit"]), ("done", False), tc)
            self.assertEqual(len(self.fw.calls()), n + 1)
            self.assertNotIn("tempo_cnn", self.stored())  # replaced by the fresh run (fake worker emits none)

    def test_deterministic_tempo_cnn_failure_reused(self):
        for code in ("too_short", "non_finite", "inconsistent_output"):
            self.write_stored(dict(RHYTHM_OUT_V2, tempo_cnn={"status": "failed", "code": code},
                                   **self.ids()))
            n = len(self.fw.calls())
            r = self.c.json("POST", AR, WAV)[1]["rhythm"]
            self.assertEqual((r["status"], r["cache_hit"]), ("done", True), code)
            self.assertEqual(len(self.fw.calls()), n)

    def test_recoverable_melody_failure_recomputed(self):
        # review F2: environment-dependent melody failures are retried like tempo_cnn; non_finite stays cached
        for code in ("analysis_error", "dependency_missing"):
            failed = dict(RHYTHM_OUT_V40, melody={"status": "failed", "code": code}, melody_notes=[],
                          **self.ids())
            self.write_stored(failed)
            self.fw.write(stdout=json.dumps(RHYTHM_OUT_V40))  # the cause went away: melody done now
            n = len(self.fw.calls())
            r = self.c.json("POST", AR, WAV)[1]["rhythm"]
            self.assertEqual((r["status"], r["cache_hit"], r["melody"]), ("done", False, MELODY), code)
            self.assertEqual(len(self.fw.calls()), n + 1)
            self.assertEqual(self.stored()["melody"], MELODY)
            self.assertTrue(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"])  # now reused
            self.assertEqual(len(self.fw.calls()), n + 1)
            # a failed recompute keeps the stored file (and the next analyze retries again)
            self.write_stored(failed)
            self.fw.write(exit=1, stdout="", stderr_lines=['{"error":{"code":"analysis_failed","message":"x"}}'])
            r = self.c.json("POST", AR, WAV)[1]["rhythm"]
            self.assertEqual(r, {"status": "failed", "code": "analysis_failed"})
            self.assertEqual(self.stored(), failed)
            self.fw.write(exit=0, stderr_lines=[])
        self.write_stored(dict(RHYTHM_OUT_V40, melody={"status": "failed", "code": "non_finite"}, melody_notes=[],
                               **self.ids()))
        n = len(self.fw.calls())
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual((r["cache_hit"], r["melody"]), (True, {"status": "failed", "code": "non_finite"}))
        self.assertEqual(len(self.fw.calls()), n)

    def test_genre_retry_rule(self):
        # a model file missing/unreadable/off-pin or an environment error may pass later (retried, like tempo_cnn);
        # too_short, no_audible_patches and deterministic failures stay cached
        for gn, retry in (({"status": "unavailable", "reason": "model_missing"}, True),
                          ({"status": "unavailable", "reason": "sha256_mismatch"}, True),
                          ({"status": "unavailable", "reason": "not_regular_file"}, True),
                          ({"status": "failed", "code": "inference_error"}, True),
                          ({"status": "failed", "code": "dependency_missing"}, True),
                          ({"status": "unavailable", "reason": "too_short"}, False),
                          ({"status": "unavailable", "reason": "no_audible_patches"}, False),
                          ({"status": "failed", "code": "non_finite"}, False),
                          ({"status": "failed", "code": "class_count_mismatch"}, False), (GENRE_DONE, False)):
            self.write_stored(dict(RHYTHM_OUT_V50, genre=gn, **self.ids()))
            n = len(self.fw.calls())
            r = self.c.json("POST", AR, WAV)[1]["rhythm"]
            self.assertEqual((r["status"], r["cache_hit"]), ("done", not retry), gn)
            self.assertEqual(len(self.fw.calls()), n + (1 if retry else 0), gn)

    def test_changed_words_recomputed(self):
        # the cache key includes the words payload: a re-run ASR (new words for the same result_id) recomputes
        self.assertFalse(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"])
        self.assertTrue(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"])
        first = self.stored()["words_sha256"]
        self.write_result(words=WORDS[:3])
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual((r["status"], r["cache_hit"]), ("done", False))
        self.assertEqual(len(self.fw.calls()), 2)
        self.assertNotEqual(self.stored()["words_sha256"], first)
        self.assertEqual(self.stored()["words_sha256"], self.ids()["words_sha256"])
        self.write_stored(dict(RHYTHM_OUT_V43, worker_sha256=self.worker_sha()))  # no words hash: recomputed
        self.assertFalse(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"])
        self.fw.write(stdout=json.dumps(dict(RHYTHM_OUT, words_sha256="cd" * 32)))  # worker cannot set it either
        self.write_result(words=WORDS[:2])
        self.c.json("POST", AR, WAV)
        self.assertEqual(self.stored()["words_sha256"], self.ids()["words_sha256"])

    def test_unreadable_words_recomputed(self):
        # result_unreadable may pass later (retried, like a missing TempoCNN model); other reasons stay cached
        for reason, retry in (("result_unreadable", True), ("result_missing", False), ("no_words", False),
                              ("too_little_text", False)):
            self.write_stored(dict(RHYTHM_OUT_V43, lyric_repeats={"status": "unavailable", "reason": reason},
                                   **self.ids()))
            n = len(self.fw.calls())
            r = self.c.json("POST", AR, WAV)[1]["rhythm"]
            self.assertEqual(r["cache_hit"], not retry, reason)
            self.assertEqual(len(self.fw.calls()), n + retry)
        self.write_stored(dict(RHYTHM_OUT_V43, lyric_repeats={"status": "failed", "code": "analysis_error"},
                               **self.ids()))
        self.assertTrue(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"])  # pure code: deterministic

    def test_failed_recompute_keeps_old_file(self):
        self.write_stored(RHYTHM_OUT_V2)
        self.fw.write(exit=1, stdout="", stderr_lines=['{"error":{"code":"analysis_failed","message":"x"}}'])
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual(r, {"status": "failed", "code": "analysis_failed"})
        self.assertEqual(self.stored(), RHYTHM_OUT_V2)
        self.assertEqual(self.c.json("GET", "/report/" + RID64)[1]["rhythm"]["chords_grid"], "beat")  # read as is

    def test_worker_edited_between_runs(self):
        self.assertFalse(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"])
        first = self.stored()["worker_sha256"]
        with open(self.fw.path, "a") as f:
            f.write("# edited\n")
        self.assertTrue(self.c.json("POST", AR, WAV)[1]["rhythm"]["cache_hit"])  # hashed once at App start
        self.assertEqual(len(self.fw.calls()), 1)
        self.h.stop()
        self.h = Harness(self.env, rhythm={"python": PY, "worker": self.fw.path})
        self.c = Client(self.env, self.h.port)
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual((r["status"], r["cache_hit"]), ("done", False))
        self.assertEqual(len(self.fw.calls()), 2)
        self.assertNotEqual(self.stored()["worker_sha256"], first)
        self.assertEqual(self.stored()["worker_sha256"], self.worker_sha())

    def test_worker_cannot_set_its_own_hash(self):
        self.fw.write(stdout=json.dumps(dict(RHYTHM_OUT, worker_sha256="cd" * 32)))
        self.assertEqual(self.c.json("POST", AR, WAV)[1]["rhythm"]["status"], "done")
        self.assertEqual(self.stored()["worker_sha256"], self.worker_sha())

    def test_unreadable_worker_never_reuses(self):
        self.write_stored(dict(RHYTHM_OUT_V2, **self.ids()))
        self.h.app.rhythm_worker_sha256 = None  # as if the worker file could not be read at start
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual(r["cache_hit"], False)
        self.assertNotIn("worker_sha256", self.stored())
        self.assertIsNone(server.file_sha256(os.path.join(self.env.dir, "nope.py")))
        self.assertIsNone(server.file_sha256(self.env.dir))  # not a regular file

    def test_bad_hash_value_invalid(self):
        for bad in ("AB" * 32, "ab" * 31, "ab" * 32 + "\n", 5, None, ["ab" * 32]):
            v = json.loads(json.dumps(dict(RHYTHM_OUT, worker_sha256=bad)))
            self.assertIsNone(server.check_rhythm(v), bad)
            self.write_stored(v)
            self.assertEqual(self.c.json("GET", "/report/" + RID64)[1]["rhythm"],
                             {"status": "failed", "code": "invalid_result"})
        v = json.loads(json.dumps(dict(RHYTHM_OUT, worker_sha256="ab" * 32)))
        self.assertIs(server.check_rhythm(v), v)


class RhythmUnconfiguredTest(Base):
    def test_flag_refused_400_nothing_staged(self):
        h = self.c.signed("POST", AR, WAV)
        st, b = self.c.json("POST", AR, WAV, headers=h)
        self.assertEqual((st, b["error"]["code"]), (400, "rhythm_unavailable"))
        self.assertEqual(self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[0], 404)
        self.assertEqual(self.env.argv_log(), [])
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertFalse(os.path.exists(os.path.join(self.env.root, "results-rhythm")))
        self.assertEqual(self.c.json("POST", A + "&rhythm=0", WAV)[0], 200)

    def test_status_and_report_bytes_unchanged(self):
        st, h, raw = self.c.send("GET", "/status")
        self.assertEqual(raw, b'{"tool_version":"fake","engine":"x"}')
        st, h, raw = self.c.send("GET", "/report/" + RID64)
        self.assertEqual(raw, ('{"result_id":"%s","state":"complete", "x" : 1.50}' % RID64).encode())
        st, b = self.c.json("GET", "/timeline/" + RID64 + "?layer=beats")
        self.assertEqual((st, b["error"]["code"]), (404, "rhythm_not_found"))


class RhythmTimeoutTest(RhythmBase):
    limits = {"rhythm_timeout": 1.0}

    def test_timeout_kills_worker_keeps_200(self):
        self.fw.write(sleep=30)
        t0 = time.time()
        self.assert_rhythm_failed("timeout", "timeout")
        self.assertLess(time.time() - t0, 10)
        pid = self.fw.calls()[-1]["pid"]
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class RhythmBudgetTest(RhythmBase):
    limits = {"handler_total": 4.0}

    def test_no_time_left_skips_worker(self):
        st, b = self.c.json("POST", AR, WAV)
        self.assertEqual((st, b["rhythm"]), (200, {"status": "timeout", "code": "no_time_left"}))
        self.assertEqual(self.fw.calls(), [])


class RhythmReadTest(RhythmBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.c.json("POST", AR, WAV)[1]["rhythm"]["status"], "done")

    def tl(self, q):
        return self.c.json("GET", "/timeline/" + RID64 + "?" + q)

    def test_report_merge(self):
        st, b = self.c.json("GET", "/report/" + RID64)
        self.assertEqual(st, 200)
        self.assertEqual((b["result_id"], b["x"]), (RID64, 1.5))  # native fields kept
        r = b["rhythm"]
        self.assertEqual((r["status"], r["bpm"], r["bpm_confidence"], r["beat_count"], r["chord_segment_count"]),
                         ("done", 120.0, 3.9, 7, 3))
        self.assertEqual(r["key"]["tonic"], "C")
        self.assertEqual(r["downbeats"], {"status": "not_provided"})
        self.assertNotIn("beats", r)
        self.assertEqual(set(r["read_next"]), {"sheet", "zoom", "earcheck", "note"})  # the harness configures rhythm
        self.assertEqual(r["read_next"]["zoom"], "GET /sheet/%s?start=<s>&end=<s> (at most 30 s, bar by bar)" % RID64)
        cfg = self.h.app.cfg
        self.h.app.cfg = dict(cfg, rhythm=None)  # no rhythm config: /earcheck is unavailable, so not offered
        try:
            self.assertNotIn("earcheck", self.c.json("GET", "/report/" + RID64)[1]["rhythm"]["read_next"])
        finally:
            self.h.app.cfg = cfg
        # another result id without a stored file: native bytes unchanged
        other = "cd" * 32
        st, h, raw = self.c.send("GET", "/report/" + other)
        self.assertEqual(raw, ('{"result_id":"%s","state":"complete", "x" : 1.50}' % RID64).encode())
        # a damaged stored file is reported, never served
        with open(self.rhythm_file(), "w") as f:
            f.write("{")
        self.assertEqual(self.c.json("GET", "/report/" + RID64)[1]["rhythm"],
                         {"status": "failed", "code": "invalid_result"})
        self.assertEqual(self.tl("layer=beats")[1]["error"]["code"], "invalid_result")
        # valid JSON that is not a valid rhythm result: ASR report stays 200, rhythm marked invalid
        for bad in (json.dumps(RHYTHM_OUT).replace('"duration": 6.0', '"duration": 1' + "0" * 400),
                    json.dumps(dict(RHYTHM_OUT, bpm_estimates=[{"bpm": float("nan"), "share": 1}]))):
            with open(self.rhythm_file(), "w") as f:
                f.write(bad)
            st, b = self.c.json("GET", "/report/" + RID64)
            self.assertEqual((st, b["x"], b["rhythm"]), (200, 1.5, {"status": "failed", "code": "invalid_result"}))
            self.assertEqual(self.tl("layer=chords")[1]["error"]["code"], "invalid_result")

    def test_old_and_v2_stored_files(self):
        with open(self.rhythm_file()) as f:
            self.assertNotIn("key_sections", json.load(f))  # stored by the 0.1.0-shaped fake
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual((r["status"], r["bpm"]), ("done", 120.0))
        self.assertEqual([r[k] for k in V2_SUMMARY_KEYS], [None] * len(V2_SUMMARY_KEYS))
        self.assertEqual(len(self.tl("layer=chords")[1]["chords"]), 3)
        st, b = self.c.json("POST", AR, WAV)  # ml cache hit path reuses the old file as is
        self.assertEqual((b["rhythm"]["cache_hit"], b["rhythm"]["key_sections"]), (True, None))
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V2))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual((r["chords_grid"], r["bpm_alternatives"]), ("beat", [60.0, 240.0]))
        self.assertEqual([r[k] for k in V21_SUMMARY_KEYS], [None, None])  # 0.2.0 file: no evidence field
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V21))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual(r["double_tempo_evidence"]["mid_to_beat_ratio"], 1.011)
        self.assertIsNone(r["tempo_cnn"])  # 0.2.1 file: no tempo_cnn
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V22))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual(r["tempo_cnn"], TC_DONE)
        self.assertEqual([r[k] for k in V30_SUMMARY_KEYS], [None] * len(V30_SUMMARY_KEYS))  # 0.2.2 file
        st, b = self.tl("layer=energy")  # not stored before 0.3.0: same 404 as a missing rhythm file
        self.assertEqual((st, b["error"]["code"]), (404, "rhythm_not_found"))
        self.assertEqual(len(self.tl("layer=beats")[1]["beats"]), 7)  # other layers unchanged
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V30))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual((r["sections"], r["energy_summary"]), (SECTIONS, ENERGY_SUMMARY))
        self.assertIsNone(r["melody"])  # 0.3.x file: no melody
        st, b = self.tl("layer=melody")  # not stored before 0.4.0: same 404 as energy before 0.3.0
        self.assertEqual((st, b["error"]["code"]), (404, "rhythm_not_found"))
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V40))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual((r["melody"], r["lyric_repeats"]), (MELODY, None))  # 0.4.0-0.4.2 file: no lyric_repeats
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V43))
        r = self.c.json("GET", "/report/" + RID64)[1]["rhythm"]
        self.assertEqual((r["lyric_repeats"], r["genre"]), (LYRIC, None))  # 0.4.3/0.4.4 file: no genre
        st, b = self.tl("layer=genre")  # not stored before 0.5.0
        self.assertEqual((st, b["error"]["code"]), (404, "rhythm_not_found"))
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V50))
        self.assertEqual(self.c.json("GET", "/report/" + RID64)[1]["rhythm"]["genre"], GENRE_SUMMARY)
        for bad in bad_v2() + bad_v21() + bad_v22() + bad_v30() + bad_v40() + bad_v41() + bad_v43() + bad_v50():
            with open(self.rhythm_file(), "w") as f:
                f.write(bad)
            st, b = self.c.json("GET", "/report/" + RID64)
            self.assertEqual((st, b["x"], b["rhythm"]), (200, 1.5, {"status": "failed", "code": "invalid_result"}))

    def test_status_reports_availability(self):
        st, b = self.c.json("GET", "/status")
        self.assertEqual(b["rhythm"], {"configured": True, "python_present": True, "worker_present": True,
                                       "worker_sha256": self.worker_sha(), "separation_configured": False, "chord_model_configured": False, "bass_configured": False, "drums_configured": False, "sections_configured": False, "singing_configured": False,
                                       "earcheck_available": True})
        self.assertEqual(b["tool_version"], "fake")

    def test_timeline_beats_paging(self):
        calls = len(self.env.argv_log())
        st, b = self.tl("layer=beats&limit=3")
        self.assertEqual(st, 200)
        self.assertEqual((b["total"], b["cursor"], b["truncated"], b["continuation"]), (7, 0, True, 3))
        self.assertEqual(b["beats"], [{"time": 0.5}, {"time": 1.0}, {"time": 1.5}])
        self.assertEqual((b["layer"], b["layer_status"], b["clock"]), ("beats", "done", "seconds_from_audio_start"))
        b = self.tl("layer=beats&limit=3&cursor=6")[1]
        self.assertEqual((b["beats"], b["truncated"], b["continuation"]), ([{"time": 3.5}], False, None))
        b = self.tl("layer=beats&start=1&end=2.5")[1]
        self.assertEqual([x["time"] for x in b["beats"]], [1.0, 1.5, 2.0, 2.5])
        self.assertEqual(b["range"], {"start": 1.0, "end": 2.5})
        self.assertEqual(len(self.env.argv_log()), calls)  # served by the server, not ml

    def test_timeline_chords(self):
        b = self.tl("layer=chords&start=2.5&end=4.5")[1]
        self.assertEqual([c["label"] for c in b["chords"]], ["Am", "F"])
        self.assertEqual((b["total"], b["reliability"]), (2, "low_reference"))
        b = self.tl("layer=chords&limit=1&cursor=1")[1]
        self.assertEqual(([c["label"] for c in b["chords"]], b["continuation"]), (["Am"], 2))

    def test_timeline_energy(self):
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V30))
        calls = len(self.env.argv_log())
        st, b = self.tl("layer=energy&limit=2")
        self.assertEqual(st, 200, b)
        self.assertEqual(b["energy"], [{"time": 0.0, "value": -30.0}, {"time": 1.0, "value": -29.5}])
        self.assertEqual((b["total"], b["truncated"], b["continuation"], b["layer"], b["unit"], b["step_s"]),
                         (6, True, 2, "energy", "dBFS", 1.0))
        b = self.tl("layer=energy&limit=2&cursor=4")[1]
        self.assertEqual((b["energy"][-1], b["truncated"], b["continuation"]),
                         ({"time": 5.0, "value": -31.0}, False, None))
        b = self.tl("layer=energy&start=1.5&end=3")[1]  # rows whose time lies in [start, end]
        self.assertEqual([x["time"] for x in b["energy"]], [2.0, 3.0])
        for q, code in (("layer=energy&cursor=7", "invalid_argument"), ("layer=energy&end=601", "invalid_argument"),
                        ("layer=energy&limit=0", "bad_request"), ("layer=energies", "bad_request")):
            st, b = self.tl(q)
            self.assertEqual((st, b["error"]["code"]), (400, code), q)
        self.assertEqual(len(self.env.argv_log()), calls)  # served by the server, not ml
        # 600 values: the page byte cap holds at limit=200
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(dict(RHYTHM_OUT_V30, duration=600.0,
                                    energy=dict(ENERGY, values=[-123.45 + 3.45] * 600))))
        b = self.tl("layer=energy&limit=200&cursor=400")[1]
        self.assertEqual((b["total"], len(b["energy"]), b["continuation"]), (600, 200, None))
        self.assertLess(len(json.dumps(b)), server.MAX_WIRE_PAGE + 2000)

    def test_timeline_melody(self):
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(RHYTHM_OUT_V40))
        calls = len(self.env.argv_log())
        st, b = self.tl("layer=melody&limit=3")
        self.assertEqual(st, 200, b)
        self.assertEqual(b["melody"], MELODY_NOTES[:3])
        self.assertEqual(b["melody"][0], {"start": 0.0, "end": 0.5, "midi": 60, "note": "C4"})
        self.assertEqual((b["total"], b["truncated"], b["continuation"], b["layer"], b["layer_status"],
                          b["reliability"]), (7, True, 3, "melody", "done", "low_reference"))
        b = self.tl("layer=melody&limit=3&cursor=6")[1]
        self.assertEqual((b["melody"], b["truncated"], b["continuation"]), ([MELODY_NOTES[6]], False, None))
        b = self.tl("layer=melody&start=1.7&end=2.2")[1]  # rows overlapping [start, end]
        self.assertEqual([x["midi"] for x in b["melody"]], [67, 72])
        for q, code in (("layer=melody&cursor=8", "invalid_argument"), ("layer=melody&start=5&end=4", "invalid_argument"),
                        ("layer=melody&limit=201", "bad_request"), ("layer=melodies", "bad_request")):
            st, b = self.tl(q)
            self.assertEqual((st, b["error"]["code"]), (400, code), q)
        self.assertEqual(len(self.env.argv_log()), calls)  # served by the server, not ml
        with open(self.rhythm_file(), "w") as f:  # failed melody: empty layer with its status
            f.write(json.dumps(dict(RHYTHM_OUT_V40, melody=MELODY_FAILED, melody_notes=[])))
        b = self.tl("layer=melody")[1]
        self.assertEqual((b["melody"], b["total"], b["layer_status"]), ([], 0, "failed"))
        # the most notes a valid file can hold: the page byte cap holds at limit=200
        many = [_n(i * 0.1, i * 0.1 + 0.1, 60 + i % 12) for i in range(server.MELODY_MAX_NOTES)]
        m = dict(rw.melody_summary(many, [], 0.008, 75000, "C", "major", []), notes_per_second=10.0, voiced_s=600.0)
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(dict(RHYTHM_OUT_V40, duration=600.0, sections=[], melody=m, melody_notes=many)))
        b = self.tl("layer=melody&limit=200&cursor=5800")[1]
        self.assertEqual((b["total"], len(b["melody"]), b["continuation"]), (6000, 200, None))
        self.assertLess(len(json.dumps(b)), server.MAX_WIRE_PAGE + 2000)

    def test_timeline_genre(self):
        long = rw.genre_summary(genre_rows(70), [False] * 30 + [True] * 31 + [False] * 9, GENRE_LABELS, 70.0)
        with open(self.rhythm_file(), "w") as f:
            f.write(json.dumps(dict(RHYTHM_OUT_V50, duration=70.0, genre=long)))
        calls = len(self.env.argv_log())
        st, b = self.tl("layer=genre")
        self.assertEqual(st, 200, b)
        self.assertEqual(b["genre"], long["windows"])
        self.assertEqual([(w["start"], w["end"], w["patches"]) for w in b["genre"]], [(0.0, 30.0, 30), (30.0, 70.0, 9)])
        self.assertEqual((b["total"], b["truncated"], b["layer"], b["layer_status"], b["reliability"]),
                         (2, False, "genre", "done", "model_estimate"))
        self.assertEqual(self.tl("layer=genre&start=31&end=40")[1]["genre"], long["windows"][1:])  # overlapping
        b = self.tl("layer=genre&limit=1")[1]
        self.assertEqual((len(b["genre"]), b["continuation"]), (1, 1))
        self.assertEqual(self.tl("layer=genre&cursor=3")[0], 400)
        self.assertEqual(len(self.env.argv_log()), calls)  # served by the server, not ml
        with open(self.rhythm_file(), "w") as f:  # unavailable genre: empty layer with its status
            f.write(json.dumps(dict(RHYTHM_OUT_V50, genre={"status": "unavailable", "reason": "model_missing"})))
        b = self.tl("layer=genre")[1]
        self.assertEqual((b["genre"], b["total"], b["layer_status"]), ([], 0, "unavailable"))

    def test_timeline_errors_and_words_passthrough(self):
        for q, code in (("layer=beats&start=-1", "invalid_argument"), ("layer=beats&start=3&end=2", "invalid_argument"),
                        ("layer=beats&end=601", "invalid_argument"), ("layer=beats&cursor=8", "invalid_argument"),
                        ("layer=beats&limit=0", "bad_request"), ("layer=beats&limit=201", "bad_request"),
                        ("layer=beats&start=x", "bad_request"), ("layer=downbeats", "bad_request"),
                        ("layer=beats&foo=1", "bad_request")):
            st, b = self.tl(q)
            self.assertEqual((st, b["error"]["code"]), (400, code), q)
        self.assertEqual(self.tl("layer=beats&cursor=7")[1]["beats"], [])  # cursor == total is allowed
        st, b = self.c.json("GET", "/timeline/" + "cd" * 32 + "?layer=chords")
        self.assertEqual((st, b["error"]["code"]), (404, "rhythm_not_found"))
        st, h, raw = self.c.send("GET", "/timeline/" + RID64 + "?layer=words")
        self.assertEqual(raw, ('{"result_id":"%s","layer":"words","words":[]}' % RID64).encode())
        self.assertEqual(self.env.argv_log()[-1], ["timeline", RID64, "--layer=words"])


class RhythmConfigTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)

    def tearDown(self):
        self.env.cleanup()

    def load(self, **over):
        return server.load_config(self.env.write_config(self.env.config(**over)))

    def refused(self, rhythm):
        with self.assertRaises(server.ConfigError):
            self.load(rhythm=rhythm)

    def test_absent_ok_and_valid(self):
        self.assertIsNone(self.load()["rhythm"])
        prod = self.load(test_mode=False, bind=["en0"], port=8765, allow="192.0.2.0/24")
        self.assertIsNone(prod["rhythm"])
        cfg = self.load(rhythm={"python": PY, "worker": self.fw.path})
        self.assertEqual(cfg["rhythm"], {"python": PY, "worker": self.fw.path})
        app = server.App(cfg)
        app.close()
        self.assertTrue(os.path.isdir(os.path.join(self.env.root, "results-rhythm")))

    def test_bad_values_refused(self):
        d = os.path.join(self.env.dir, "d")
        os.mkdir(d)
        noexec = os.path.join(self.env.dir, "noexec")
        open(noexec, "w").close()
        os.chmod(noexec, 0o600)
        gw = os.path.join(self.env.dir, "gw.py")
        open(gw, "w").close()
        os.chmod(gw, 0o664)
        for bad in (None, [], "x", {}, {"python": PY}, {"python": PY, "worker": self.fw.path, "x": 1},
                    {"python": "python3", "worker": self.fw.path}, {"python": PY, "worker": "rhythm_worker.py"},
                    {"python": PY, "worker": self.fw.path + ".missing"}, {"python": PY, "worker": d},
                    {"python": noexec, "worker": self.fw.path}, {"python": PY, "worker": gw},
                    {"python": PY, "worker": 5}):
            self.refused(bad)

    def test_example_config_has_no_rhythm_key(self):
        with open(os.path.join(REPO, "examples", "api.example.json")) as f:
            self.assertNotIn("rhythm", json.load(f))


class WorkerLogicTest(unittest.TestCase):
    """rhythm_worker's pure helpers (stdlib only), run under /usr/bin/python3."""

    def test_bpm_alternatives(self):
        self.assertEqual(rw.bpm_alternatives(120.0), [60.0, 240.0])
        self.assertEqual(rw.bpm_alternatives(109.94), [54.97, 219.88])
        self.assertEqual(rw.bpm_alternatives(70.0), [140.0])  # 35 < 40 dropped
        self.assertEqual(rw.bpm_alternatives(180.0), [90.0])  # 360 > 300 dropped
        self.assertEqual(rw.bpm_alternatives(0.0), [])

    def test_key_windows(self):
        self.assertEqual(rw.key_windows(5.0), [(0.0, 5.0)])
        self.assertEqual(rw.key_windows(60.0), [(0.0, 20.0), (20.0, 40.0), (40.0, 60.0)])
        self.assertEqual(rw.key_windows(69.0), [(0.0, 20.0), (20.0, 40.0), (40.0, 69.0)])  # 9 s tail merged
        self.assertEqual(rw.key_windows(70.0)[-1], (60.0, 70.0))  # 10 s tail kept
        self.assertEqual(len(rw.key_windows(600.0)), 30)
        self.assertEqual(rw.key_windows(216.0)[-1], (200.0, 216.0))

    def test_merge_key_sections(self):
        rows = [(0, 20, "E", "major", 0.8), (20, 40, "E", "major", 0.6), (40, 60, "F", "major", 0.3),
                (60, 80, "F", "major", None), (80, 90, "E", "major", 0.9)]
        self.assertEqual(rw.merge_key_sections(rows), [
            {"start": 0.0, "end": 40.0, "tonic": "E", "scale": "major", "strength": 0.7},
            {"start": 40.0, "end": 80.0, "tonic": "F", "scale": "major", "strength": None},
            {"start": 80.0, "end": 90.0, "tonic": "E", "scale": "major", "strength": 0.9}])

    def test_double_tempo_evidence(self):
        hop = 0.01
        env = [0.0] * 1000  # 10 s
        beats = [0.5 * k + 0.5 for k in range(18)]  # 0.5 .. 9.0
        for b in beats:
            env[int(round(b / hop)) + 2] = 1.0  # peak 20 ms late: inside the +-30 ms pool
        d, w = rw.double_tempo_evidence(env, hop, beats)
        self.assertIsNone(w)
        self.assertEqual((d["pairs"], d["beat_onset_median"], d["mid_onset_median"]), (17, 1.0, 0.0))
        self.assertEqual((d["mid_to_beat_ratio"], d["mid_strong_fraction"], d["window_ms"]), (0.0, 0.0, 30.0))
        for b1, b2 in zip(beats, beats[1:]):
            env[int(round((b1 + b2) / 2 / hop))] = 0.6
        d, _ = rw.double_tempo_evidence(env, hop, beats)
        self.assertEqual((d["mid_onset_median"], d["mid_to_beat_ratio"], d["mid_strong_fraction"]), (0.6, 0.6, 1.0))
        env[int(round(0.75 / hop))] = 0.4  # one midpoint below 0.5 x its beats' mean
        d, _ = rw.double_tempo_evidence(env, hop, beats)
        self.assertEqual(d["mid_strong_fraction"], round(16 / 17.0, 3))
        # a peak 40 ms away from a midpoint falls outside the pool
        env2 = [0.0] * 1000
        for b in beats:
            env2[int(round(b / hop))] = 1.0
        env2[int(round(0.75 / hop)) + 4] = 1.0
        self.assertEqual(rw.double_tempo_evidence(env2, hop, beats)[0]["mid_strong_fraction"], 0.0)
        # too few pairs; gaps outside 0.12..1.5 s are skipped
        d, w = rw.double_tempo_evidence(env, hop, beats[:7])
        self.assertEqual((d["pairs"], d["mid_to_beat_ratio"], d["mid_strong_fraction"]), (6, None, None))
        self.assertTrue(w.startswith("double_tempo_evidence_unavailable:"))
        d, w = rw.double_tempo_evidence(env, hop, [0.5, 0.55, 3.0, 5.0])
        self.assertEqual((d["pairs"], d["beat_onset_median"]), (0, None))
        self.assertTrue(w)
        d, w = rw.double_tempo_evidence(env, hop, [])
        self.assertEqual((d["pairs"], d["mid_to_beat_ratio"]), (0, None))
        # silence at beats: null ratio, onset medians reported
        d, w = rw.double_tempo_evidence([0.0] * 1000, hop, beats)
        self.assertEqual((d["beat_onset_median"], d["mid_to_beat_ratio"]), (0.0, None))
        self.assertIn("~0", w)
        # pool clamps at the envelope edges
        self.assertEqual(rw._pool([0.3, 0.1], hop, -0.02, 0.03), 0.3)
        self.assertEqual(rw._pool([0.3, 0.1], hop, 5.0, 0.03), 0.0)

    def test_verify_model(self):
        d = tempfile.mkdtemp(prefix="mltc-")
        try:
            good = os.path.join(d, "m.pb")
            with open(good, "wb") as f:
                f.write(b"model bytes")
            h = sha(b"model bytes")
            self.assertIsNone(rw.verify_model(good, h))
            self.assertEqual(rw.verify_model(good), "sha256_mismatch")  # the real pin
            self.assertEqual(rw.verify_model(good, "00" * 32), "sha256_mismatch")
            self.assertEqual(rw.verify_model(good, h, cap=5), "model_too_large")
            self.assertEqual(rw.verify_model(os.path.join(d, "nope.pb"), h), "model_missing")
            self.assertEqual(rw.verify_model(os.path.join(good, "x"), h), "model_missing")  # parent not a dir
            self.assertEqual(rw.verify_model(d, h), "not_regular_file")
            link = os.path.join(d, "link.pb")
            os.symlink(good, link)
            self.assertEqual(rw.verify_model(link, h), "not_regular_file")  # O_NOFOLLOW
            fifo = os.path.join(d, "fifo.pb")
            os.mkfifo(fifo)
            self.assertEqual(rw.verify_model(fifo, h), "not_regular_file")  # O_NONBLOCK: no hang
            os.chmod(good, 0)
            if os.geteuid() != 0:
                self.assertEqual(rw.verify_model(good, h), "model_unreadable")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_tempo_cnn_summary(self):
        def row(i, p=0.8, rest=None):
            r = [0.0] * rw.TEMPO_CNN_CLASSES
            r[i] = p
            if rest is not None:
                r[rest] = 1.0 - p
            return r
        preds = [row(90, 0.8, 91), row(90, 0.6, 120), row(91, 0.7, 90), row(90, 0.9, 0)]
        d = rw.tempo_cnn_summary(preds, [False] * 4)
        self.assertEqual(set(d), server.TC_KEYS["done"] | {"silent_patches"})
        self.assertEqual((d["status"], d["bpm"], d["local_count"], d["silent_patches"]), ("done", 120.0, 4, 0))
        self.assertEqual((d["local_bpm_min"], d["local_bpm_median"], d["local_bpm_max"]), (120.0, 120.0, 121.0))
        # mean over the 4 rows: class 90 (120 BPM) (0.8+0.6+0.3+0.9)/4, class 91 (0.2+0.7)/4, class 120 0.4/4, ...
        self.assertEqual(d["top"], [{"bpm": 120.0, "prob": 0.65}, {"bpm": 121.0, "prob": 0.225},
                                    {"bpm": 150.0, "prob": 0.1}])
        self.assertEqual(d["model_sha256"], rw.TEMPO_CNN_SHA256)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, tempo_cnn=d)))
        self.assertIs(server.check_rhythm(v), v)
        # near-silent patches are excluded before everything else (a silent last patch voted 161 on a real song)
        d = rw.tempo_cnn_summary(preds + [row(131), row(131)], [True, False, False, False, True, True])
        self.assertEqual((d["bpm"], d["local_count"], d["silent_patches"]), (120.0, 3, 3))
        self.assertEqual(d["top"][0], {"bpm": 120.0, "prob": round((0.6 + 0.3 + 0.9) / 3, 3)})
        self.assertEqual(rw.tempo_cnn_summary([row(90)] * 2, [True, True]),
                         {"status": "failed", "code": "no_audible_patches"})
        # majority of the per-patch argmax; ties: higher mean probability, then lower BPM
        d = rw.tempo_cnn_summary([row(90, 0.6), row(91, 0.9), row(91, 0.5), row(90, 0.6)], [False] * 4)
        self.assertEqual(d["bpm"], 121.0)  # 2 votes each; class 91 mean 0.35 > class 90 mean 0.3
        d = rw.tempo_cnn_summary([row(91, 0.6), row(90, 0.6)], [False] * 2)
        self.assertEqual(d["bpm"], 120.0)  # full tie: lower BPM
        d = rw.tempo_cnn_summary([row(90, 0.5, 91)], [False])
        self.assertEqual(d["bpm"], 120.0)  # argmax tie inside a row: lower class
        # class index -> BPM: index 0 = 30, index 255 = 285; ties in the mean go to the lower class
        d = rw.tempo_cnn_summary([row(255, 0.6, 0)], [False])
        self.assertEqual(d["top"], [{"bpm": 285.0, "prob": 0.6}, {"bpm": 30.0, "prob": 0.4}, {"bpm": 31.0, "prob": 0.0}])
        d = rw.tempo_cnn_summary([row(0, 0.6, 255), row(255, 0.6, 0)], [False] * 2)
        self.assertEqual(d["top"], [{"bpm": 30.0, "prob": 0.5}, {"bpm": 285.0, "prob": 0.5}, {"bpm": 31.0, "prob": 0.0}])
        self.assertEqual((d["local_bpm_min"], d["local_bpm_median"], d["local_bpm_max"]), (30.0, 157.5, 285.0))
        self.assertEqual(rw.tempo_cnn_summary([], []), {"status": "failed", "code": "too_short"})
        bad_row = row(90)
        bad_row[3] = float("inf")
        self.assertEqual(rw.tempo_cnn_summary([bad_row], [False]), {"status": "failed", "code": "non_finite"})
        d = rw.tempo_cnn_summary([row(90)[:255]], [False])
        self.assertEqual(d, {"status": "failed", "code": "inconsistent_output",
                             "detail": {"code": "class_count_mismatch", "row": 0, "classes": 255}})
        for out in (d, {"status": "failed", "code": "too_short"}, {"status": "failed", "code": "no_audible_patches"},
                    {"status": "unavailable", "reason": "sha256_mismatch"},
                    # 0.3.1 files: TempoCNN vs predictor details and done without silent_patches stay valid
                    {"status": "failed", "code": "inconsistent_output",
                     "detail": {"code": "argmax_mismatch", "patch": 44, "local_bpm": 160.0, "argmax_bpm": 161.0,
                                "prob_gap": 0.080054}}, TC_DONE):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, tempo_cnn=out)))
            self.assertIs(server.check_rhythm(v), v)
        for bad in (-1, 1.0, True, None, 10 ** 6):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, tempo_cnn=dict(TC_DONE, silent_patches=bad))))
            self.assertIsNone(server.check_rhythm(v), bad)
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V21, tempo_cnn=dict(TC_FAILED, silent_patches=0))))
        self.assertIsNone(server.check_rhythm(v))  # only on done

    def test_patch_span(self):
        a, b = rw.patch_span(0)
        self.assertEqual((a, round(b, 3)), (0.0, 11.935))
        self.assertEqual(round(rw.patch_span(44)[0], 2), 261.55)  # the real song's failing last patch (274 s)


class GenreLogicTest(unittest.TestCase):
    """genre (worker 0.5.0): pure-Python parts; the models run only in RealWorkerTest."""

    def test_patch_span_and_windows(self):
        self.assertEqual(rw.genre_patch_span(0), (0.0, 2.032))
        self.assertEqual(round(rw.genre_patch_span(602)[1], 3), 599.216)  # last of the 603 patches measured for 600 s
        self.assertEqual(rw.key_windows(70.0, 30.0, 15.0), [(0.0, 30.0), (30.0, 70.0)])  # 10 s tail joins
        self.assertEqual(rw.key_windows(75.0, 30.0, 15.0), [(0.0, 30.0), (30.0, 60.0), (60.0, 75.0)])
        self.assertEqual(rw.key_windows(5.0, 30.0, 15.0), [(0.0, 5.0)])
        self.assertEqual(rw.key_windows(45.0), [(0.0, 20.0), (20.0, 45.0)])  # key_sections defaults unchanged

    def test_summary(self):
        d = GENRE_DONE
        self.assertEqual(set(d), server.GENRE_KEYS["done"])
        self.assertEqual((d["patches"], d["silent_patches"], d["patch_hop_s"], d["window_s"], d["reliability"]),
                         (5, 0, 0.992, 30.0, "model_estimate"))
        self.assertEqual((d["model_sha256"], d["embedding_sha256"]), (rw.GENRE_HEAD_SHA256, rw.GENRE_EMBED_SHA256))
        # song score = mean over audible patches; a silent patch is left out of the song and its window
        rows = genre_rows(3) + genre_rows(1, hot=((9, 0.9),))
        d = rw.genre_summary(rows, [False] * 4, GENRE_LABELS, 6.0)
        self.assertEqual(d["top"][:3], [{"style": "Parent07---Style007", "score": 0.452},  # (3 x 0.6 + 0.01) / 4
                                        {"style": "Parent09---Style009", "score": 0.233},
                                        {"style": "Parent07---Style022", "score": 0.227}])
        self.assertEqual(d["parents"][:2], [{"parent": "Parent07", "score": 0.452},  # max over its styles
                                            {"parent": "Parent09", "score": 0.233}])
        d = rw.genre_summary(rows, [False, False, False, True], GENRE_LABELS, 6.0)
        self.assertEqual((d["top"][0], d["silent_patches"], d["windows"][0]["patches"]),
                         ({"style": "Parent07---Style007", "score": 0.6}, 1, 3))
        # windows: a patch joins the window holding its centre (i * 0.992 + 1.016 s); a silent window keeps its row
        rows = genre_rows(30) + genre_rows(30, hot=((40, 0.7),))
        d = rw.genre_summary(rows, [False] * 30 + [True] * 30, GENRE_LABELS, 61.0)
        self.assertEqual([(w["start"], w["end"], w["patches"], w["top"]) for w in d["windows"]],
                         [(0.0, 30.0, 30, d["top"][:3]), (30.0, 61.0, 0, [])])
        d = rw.genre_summary(rows, [False] * 60, GENRE_LABELS, 61.0)
        self.assertEqual(d["windows"][1]["top"][0], {"style": "Parent10---Style040", "score": 0.7})
        self.assertEqual(d["windows"][1]["patches"], 30)
        for v in (d, rw.genre_summary(rows, [False] * 30 + [True] * 30, GENRE_LABELS, 61.0)):
            v = json.loads(json.dumps(dict(RHYTHM_OUT_V43, duration=61.0, genre=v)))
            self.assertIs(server.check_rhythm(v), v)
        # no patch / all silent / bad values never raise
        self.assertEqual(rw.genre_summary([], [], GENRE_LABELS, 1.5), {"status": "unavailable", "reason": "too_short"})
        self.assertEqual(rw.genre_summary(genre_rows(2), [True, True], GENRE_LABELS, 6.0),
                         {"status": "unavailable", "reason": "no_audible_patches"})
        bad = genre_rows(1)
        bad[0][5] = float("nan")
        self.assertEqual(rw.genre_summary(bad, [False], GENRE_LABELS, 6.0), {"status": "failed", "code": "non_finite"})
        self.assertEqual(rw.genre_summary([[0.1] * 399], [False], GENRE_LABELS, 6.0),
                         {"status": "failed", "code": "class_count_mismatch"})  # the head must give 400 columns
        self.assertEqual(rw.genre_summary(genre_rows(1) + [[0.1] * 401], [False] * 2, GENRE_LABELS, 6.0),
                         {"status": "failed", "code": "class_count_mismatch"})

    def test_labels(self):
        good = json.dumps({"name": "x", "classes": GENRE_LABELS}).encode()
        self.assertEqual(rw.genre_labels(good), GENRE_LABELS)
        for bad in (b"{", b"[]", json.dumps({"classes": GENRE_LABELS[:399]}).encode(),
                    json.dumps({"classes": GENRE_LABELS[:399] + ["NoSeparator"]}).encode(),
                    json.dumps({"classes": GENRE_LABELS[:399] + ["A---B---C"]}).encode(),
                    json.dumps({"classes": GENRE_LABELS[:399] + ["---B"]}).encode(),
                    json.dumps({"classes": GENRE_LABELS[:399] + [5]}).encode(), b"\xff"):
            self.assertIsNone(rw.genre_labels(bad), bad[:40])

    def test_pins_unavailable(self):
        # every file is checked before any model loads (no numpy/essentia needed): missing, off-pin, symlink
        d = tempfile.mkdtemp(prefix="mlgenre-")
        try:
            self.assertEqual(rw.genre(None, 6.0, [], d), {"status": "unavailable", "reason": "model_missing"})
            for name in (rw.GENRE_EMBED_FILE, rw.GENRE_HEAD_FILE, rw.GENRE_LABELS_FILE):
                with open(os.path.join(d, name), "wb") as f:
                    f.write(b"not the pinned bytes")
            self.assertEqual(rw.genre(None, 6.0, [], d), {"status": "unavailable", "reason": "sha256_mismatch"})
            os.unlink(os.path.join(d, rw.GENRE_EMBED_FILE))
            os.symlink(os.path.join(d, rw.GENRE_HEAD_FILE), os.path.join(d, rw.GENRE_EMBED_FILE))
            self.assertEqual(rw.genre(None, 6.0, [], d), {"status": "unavailable", "reason": "not_regular_file"})
            # read_pinned returns exactly the hashed bytes, or nothing
            p = os.path.join(d, rw.GENRE_LABELS_FILE)
            self.assertEqual(rw.read_pinned(p, sha(b"not the pinned bytes"), 100), (None, b"not the pinned bytes"))
            self.assertEqual(rw.read_pinned(p, "00" * 32, 100), ("sha256_mismatch", None))
            self.assertEqual(rw.read_pinned(p, sha(b"not the pinned bytes"), 5), ("model_too_large", None))
            self.assertEqual(rw.read_pinned(os.path.join(d, rw.GENRE_EMBED_FILE), "00" * 32, 100),
                             ("not_regular_file", None))
            self.assertEqual(rw.read_pinned(p, sha(b"not the pinned bytes"), 100, keep=False), (None, None))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_beat_chords_vote_and_merge(self):
        step = 0.05
        # frame labels flip every 0.15 s (C C C G G G ...) with C stronger: frame merge gives many tiny segments
        frames = [(round(i * step, 3), "C" if (i // 3) % 2 == 0 else "G", 0.9 if (i // 3) % 2 == 0 else 0.4)
                  for i in range(200)]
        beats = [0.25 + 0.5 * k for k in range(19)]
        frame_segs = rw.merge_frames(frames, 10.0)
        segs = rw.beat_chords(frames, beats, 10.0)
        self.assertGreater(len(frame_segs), 60)
        self.assertEqual([c["label"] for c in segs], ["C"])  # every beat votes C (larger summed strength)
        self.assertEqual((segs[0]["start"], segs[0]["end"]), (0.0, 10.0))
        self.assertEqual(segs[0]["strength"], 0.9)  # mean of the winning label's frames only

    def test_beat_chords_grid_head_tail_and_ties(self):
        frames = [(round(i * 0.1, 3), lab, 0.5) for i, lab in enumerate("AABBBBCCDDEE")]  # 0.0 .. 1.1
        beats = [0.15, 0.55, 0.95]
        segs = rw.beat_chords(frames, beats, 1.2)
        # head [0,0.15): A A; [0.15,0.55): B B B B; [0.55,0.95): C C D D (tie -> earliest, C); tail [0.95,1.2]: E E
        self.assertEqual([(c["start"], c["end"], c["label"]) for c in segs],
                         [(0.0, 0.15, "A"), (0.15, 0.55, "B"), (0.55, 0.95, "C"), (0.95, 1.2, "E")])
        # tie on summed strength and frame count -> earliest frame in the interval wins
        frames = [(0.0, "X", 0.5), (0.1, "Y", 0.5), (0.2, "Y", 0.5), (0.3, "X", 0.5)]
        self.assertEqual([c["label"] for c in rw.beat_chords(frames, [], 0.4)], ["X"])
        # equal sums: more frames wins
        frames = [(0.0, "X", 0.6), (0.1, "Y", 0.3), (0.2, "Y", 0.3)]
        self.assertEqual([c["label"] for c in rw.beat_chords(frames, [], 0.3)], ["Y"])

    def test_beat_chords_null_strength_and_empty_interval(self):
        frames = [(0.0, "C", None), (0.1, "C", None), (0.2, "G", 0.1), (0.3, "C", 0.5)]
        segs = rw.beat_chords(frames, [], 0.4)
        self.assertEqual((segs[0]["label"], segs[0]["strength"]), ("C", None))  # null counts 0, stays null
        frames = [(0.0, "G", None), (0.1, "G", None), (0.2, "C", 0.1)]
        segs = rw.beat_chords(frames, [], 0.3)
        self.assertEqual((segs[0]["label"], segs[0]["strength"]), ("C", 0.1))  # 0.1 > 0 + 0
        # a beat interval with no frame inside takes the frame nearest its midpoint
        frames = [(0.0, "C", 0.9), (1.0, "G", 0.9)]
        segs = rw.beat_chords(frames, [0.1, 0.2, 0.6], 1.5)
        self.assertEqual([(c["start"], c["end"], c["label"]) for c in segs], [(0.0, 0.6, "C"), (0.6, 1.5, "G")])
        self.assertEqual(rw.beat_chords([], [0.5], 1.0), [])
        # beats past the end are clamped; no zero-length segment
        segs = rw.beat_chords([(0.0, "C", 0.5)], [0.0, 1.0, 1.04], 1.0)
        self.assertEqual([(c["start"], c["end"]) for c in segs], [(0.0, 1.0)])


class StructureLogicTest(unittest.TestCase):
    """Pure helpers of worker 0.3.0 (energy summary, boundary picking, labels); no numpy needed."""

    def test_energy_summary(self):
        vals = [-40.0] * 4 + [-10.0] * 8 + [-30.0] * 8 + [-12.0] * 8 + [-50.0] * 2
        es = rw.energy_summary(vals, 30.0)
        self.assertEqual((es["unit"], es["window_s"], es["min"], es["max"]), ("dBFS", 8.0, -50.0, -10.0))
        self.assertEqual(es["median"], -12.0)
        self.assertEqual([(p["start"], p["end"], p["mean"]) for p in es["peaks"]],
                         [(4.0, 12.0, -10.0), (20.0, 28.0, -12.0), (12.0, 20.0, -30.0)])  # greedy, no overlap
        self.assertEqual(es["quietest"], {"start": 12.0, "end": 20.0, "mean": -30.0})  # lowest window mean
        es = rw.energy_summary([-20.0, -10.0, -30.0], 2.7)  # fewer values than a window: one window of all
        self.assertEqual((es["window_s"], es["peaks"], es["quietest"]),
                         (3.0, [{"start": 0.0, "end": 2.7, "mean": -20.0}], {"start": 0.0, "end": 2.7, "mean": -20.0}))
        self.assertIsNone(rw.energy_summary([], 1.0))
        self.assertEqual((es["silence_db"], es["silence_s"]), (-60.0, 0.0))

    def test_energy_summary_skips_silence(self):
        vals = [-120.0] * 10 + [-20.0] * 8 + [-30.0] * 8 + [-90.0] * 3 + [-25.0] * 9 + [-120.0] * 20
        es = rw.energy_summary(vals, 58.0)
        self.assertEqual((es["min"], es["silence_s"]), (-120.0, 33.0))  # min still shows the silence
        self.assertEqual([(p["start"], p["end"], p["mean"]) for p in es["peaks"]],
                         [(10.0, 18.0, -20.0), (29.0, 37.0, -25.0), (18.0, 26.0, -30.0)])
        self.assertEqual(es["quietest"], {"start": 18.0, "end": 26.0, "mean": -30.0})  # not 0-8 at -120
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V30, energy_summary=rw.energy_summary(ENERGY["values"], 6.0))))
        self.assertIs(server.check_rhythm(v), v)
        es = rw.energy_summary([-120.0] * 30, 30.0)  # everything silent: no window to rank
        self.assertEqual((es["peaks"], es["quietest"], es["silence_s"], es["median"]), ([], None, 30.0, -120.0))
        v = json.loads(json.dumps(dict(RHYTHM_OUT_V30, energy=dict(ENERGY, values=[-120.0] * 6),
                                       energy_summary=rw.energy_summary([-120.0] * 6, 6.0))))
        self.assertIs(server.check_rhythm(v), v)
        es = rw.energy_summary([-20.0, -70.0, -20.0], 3.0)  # short audio: one window, holding a silent value
        self.assertEqual((es["peaks"], es["quietest"], es["silence_s"]), ([], None, 1.0))

    def test_section_silent(self):
        vals = [-120.0] * 5 + [-20.0] * 5
        self.assertTrue(rw.section_silent(vals, 0.0, 5.0))
        self.assertFalse(rw.section_silent(vals, 4.0, 10.0))
        self.assertFalse(rw.section_silent(vals, 0.0, 10.0))  # exactly half: not more than half
        self.assertTrue(rw.section_silent(vals, 0.0, 7.0))
        self.assertTrue(rw.section_silent(vals, 2.2, 2.4))  # no midpoint inside: the window holding the middle

    def test_pick_boundaries(self):
        nov = [None] * 4 + [0.1, 0.2, 0.5, 0.2, 0.1, 0.16, 0.3, 0.1] + [0.05] * 8 + [None] * 4
        self.assertEqual(rw.pick_boundaries(nov, min_gap=3, thresh=0.15), [6, 10])
        self.assertEqual(rw.pick_boundaries(nov, min_gap=5, thresh=0.15), [6])  # 10 is within 5 of 6
        self.assertEqual(rw.pick_boundaries(nov, min_gap=3, thresh=0.6), [])
        tie = [None, None, 0.3, 0.3, None, None, None, None]
        self.assertEqual(rw.pick_boundaries(tie, min_gap=2, thresh=0.1), [2])  # tie: earliest
        self.assertEqual(rw.pick_boundaries([0.9, 0.9, 0.9], min_gap=2, thresh=0.1), [])  # too close to the ends
        flat = [None] * 3 + [0.2, 0.0] * 40 + [None] * 3
        self.assertEqual(rw.pick_boundaries(flat, min_gap=2, thresh=0.1), [3])  # equal peaks in reach: earliest
        self.assertEqual(rw.pick_boundaries(flat, min_gap=1, thresh=0.1, cap=5), [3, 5, 7, 9, 11])  # cap

    def test_assign_labels(self):
        sim = [[1.0, 0.2, 0.9, 0.1], [0.2, 1.0, 0.3, 0.8], [0.9, 0.3, 1.0, 0.2], [0.1, 0.8, 0.2, 1.0]]
        self.assertEqual(rw.assign_labels(sim, 0.75), [("A", 1.0), ("B", 1.0), ("A", 0.9), ("B", 0.8)])
        self.assertEqual(rw.assign_labels(sim, 0.95), [("A", 1.0), ("B", 1.0), ("C", 1.0), ("D", 1.0)])
        tie = [[1, 0, 0.8], [0, 1, 0.8], [0.8, 0.8, 1]]
        self.assertEqual(rw.assign_labels(tie, 0.75)[2], ("A", 0.8))  # tie: the earlier group
        self.assertEqual([rw.label_name(k) for k in (0, 25, 26, 27, 39)], ["A", "Z", "AA", "AB", "AN"])

    def test_top_chord_and_section_energy(self):
        ch = [{"start": 0.0, "end": 1.0, "label": "C"}, {"start": 1.0, "end": 3.0, "label": "G"},
              {"start": 3.0, "end": 4.0, "label": "C"}, {"start": 4.0, "end": 5.0, "label": "F"}]
        self.assertEqual(rw.top_chord(ch, 0.0, 4.0), "C")  # C 2 s vs G 2 s: tie -> earliest overlap
        self.assertEqual(rw.top_chord(ch, 0.5, 3.0), "G")
        self.assertIsNone(rw.top_chord(ch, 5.0, 6.0))
        self.assertIsNone(rw.top_chord([], 0.0, 1.0))
        vals = [-10.0, -20.0, -30.0, -40.0]
        self.assertEqual(rw.section_energy(vals, 0.0, 2.0), -15.0)  # midpoints 0.5, 1.5
        self.assertEqual(rw.section_energy(vals, 2.2, 2.4), -30.0)  # no midpoint inside: window holding the middle

    def test_segment_similarity(self):
        a, z = [1.0] + [0.0] * 11, [0.0] * 12
        self.assertAlmostEqual(rw._segment_similarity(a, a, z, z), 1.0)
        b = [0.0, 1.0] + [0.0] * 10
        self.assertAlmostEqual(rw._segment_similarity(a, b, z, [1.0] * 12), 0.5 * math.exp(-0.5))
        self.assertEqual(rw._segment_similarity([0.0] * 12, a, z, z), 0.5)  # silent chroma: cosine 0


def _seq(pairs, t0=0.0, gap=0.008):
    """[(midi, seconds)] -> touching note rows (gap s between pieces)."""
    out, t = [], t0
    for m, d in pairs:
        out.append(_n(round(t, 3), round(t + d, 3), m))
        t += d + gap
    return out


def _chars(text, t, step=0.3):
    """Char-level ASR words (Japanese Whisper style): one word per character, `step` s each, from t."""
    return [(c, round(t + i * step, 3), round(t + i * step + step * 0.9, 3)) for i, c in enumerate(text)], \
        t + len(text) * step


def _song(parts, step=0.3, gap=2.0):
    """parts: strings sung one after another with `gap` s between them -> (words, end time)."""
    words, t = [], 0.0
    for p in parts:
        w, t = _chars(p, t, step)
        words += w
        t += gap
    return words, t


CHORUS = "きみのなまえをよんでいるよずっと"  # 16 chars = 4.8 s at 0.3 s
VERSES = ("あさのひかりがまどをてらすころ", "かぜがふいてはなびらがまう", "よるのまちにあかりがともる",
          "ゆめのつづきをさがしてあるく")


class LyricLogicTest(unittest.TestCase):
    """lyric_repeats (worker 0.4.3, approximate matching 0.4.4): pure Python on ASR words; output checked by the server validator too."""

    def check(self, d, dur):
        self.assertTrue(server._lyric_repeats_ok(json.loads(json.dumps(d)), dur + 0.5), d)

    def test_chorus_three_times_one_group(self):
        words, end = _song([VERSES[0], CHORUS, VERSES[1], CHORUS, VERSES[2], VERSES[3], CHORUS])
        d, warns = rw.lyric_repeats(words, end)
        self.check(d, end)
        self.assertEqual((d["status"], d["unit"], d["n"], warns), ("done", "char", 8, []))
        self.assertEqual([b["group"] for b in d["blocks"]], [0, 0, 0])
        self.assertEqual(d["group_count"], 1)
        starts = [w[1] for w in words if w[0] == CHORUS[0] and words[words.index(w) + 1][0] == CHORUS[1]]
        self.assertEqual([b["start"] for b in d["blocks"]], starts)
        for b in d["blocks"]:
            self.assertAlmostEqual(b["end"] - b["start"], 4.77, delta=0.01)  # last char ends at 0.9 x its 0.3 s
        self.assertEqual(d["coverage_s"], round(sum(b["end"] - b["start"] for b in d["blocks"]), 3))

    def test_no_repeat_and_spread(self):
        words, end = _song(VERSES)
        d, _ = rw.lyric_repeats(words, end)
        self.assertEqual((d["status"], d["blocks"], d["group_count"], d["coverage_s"]), ("done", [], 0, 0.0))
        self.check(d, end)
        # the same line twice within 15 s (a doubled line) is not a repeat; >= 15 s apart it is
        words, end = _song([VERSES[0], CHORUS, CHORUS, VERSES[1]])
        self.assertEqual(rw.lyric_repeats(words, end)[0]["blocks"], [])
        words, end = _song([CHORUS, VERSES[0], VERSES[1], CHORUS])
        self.assertEqual(len(rw.lyric_repeats(words, end)[0]["blocks"]), 2)
        # a repeated fragment shorter than 4 s is dropped (8 chars = 2.4 s)
        words, end = _song([CHORUS[:9], VERSES[0], VERSES[1], VERSES[2], CHORUS[:9]])
        self.assertEqual(rw.lyric_repeats(words, end)[0]["blocks"], [])

    def test_two_refrains_two_groups_join(self):
        hook = "らららうたおうよあしたへむかって"
        words, end = _song([CHORUS, VERSES[0], hook, VERSES[1], CHORUS, VERSES[2], hook], gap=1.0)
        d, _ = rw.lyric_repeats(words, end)
        self.check(d, end)
        self.assertEqual([b["group"] for b in d["blocks"]], [0, 1, 0, 1])
        # covered chars closer than 3 s join one block: chorus + hook with a 2 s gap give one block per pair
        words, end = _song([CHORUS, hook, VERSES[0], VERSES[1], CHORUS, hook], gap=2.0)
        d, _ = rw.lyric_repeats(words, end)
        self.assertEqual([b["group"] for b in d["blocks"]], [0, 0])
        self.assertGreater(d["blocks"][0]["end"] - d["blocks"][0]["start"], 11.0)
        # with a 3.5 s gap they stay 4 blocks, but chorus+hook repeats as a whole: the n-grams across the pause
        # touch both blocks of each occurrence, so all four share one group (review F1 rule)
        words, end = _song([CHORUS, hook, VERSES[0], VERSES[1], CHORUS, hook], gap=3.5)
        d = rw.lyric_repeats(words, end)[0]
        self.assertEqual(([b["group"] for b in d["blocks"]], d["group_count"]), ([0, 0, 0, 0], 1))

    def test_occurrence_split_by_pauses_one_group(self):
        # review F1 (0.4.3): one repeated n-gram whose units fall in several blocks (pauses > 3 s) groups every
        # block it touches, not only the block of its first unit; blocks stay separate in time
        lines = ("remember", "forever", "together", "tomorrow")
        words = [(" " + w, t0 + 8.0 * i, t0 + 8.0 * i + 4.0) for t0 in (0.0, 64.0) for i, w in enumerate(lines)]
        d, _ = rw.lyric_repeats(words, 100.0)
        self.check(d, 100.0)
        self.assertEqual((d["unit"], d["n"]), ("word", 4))
        self.assertEqual([(b["start"], b["end"]) for b in d["blocks"]],
                         [(t0 + 8.0 * i, t0 + 8.0 * i + 4.0) for t0 in (0.0, 64.0) for i in range(4)])
        self.assertEqual(([b["group"] for b in d["blocks"]], d["group_count"]), ([0] * 8, 1))
        # char mode: an 8-char n-gram of 4 s characters with 4 s pauses, plus distinct filler to pass 50 chars
        words = [(c, t0 + 8.0 * i, t0 + 8.0 * i + 4.0)
                 for t0 in (0.0, 70.0) for i, c in enumerate("あいうえおかきく")]
        words += [(chr(0x4E00 + i), 130.5 + i * 0.18, 130.5 + i * 0.18 + 0.1) for i in range(50)]  # after both
        d, _ = rw.lyric_repeats(words, 140.0)
        self.check(d, 140.0)
        self.assertEqual(d["unit"], "char")
        self.assertEqual(len(d["blocks"]), 16)
        self.assertEqual(([b["group"] for b in d["blocks"]], d["group_count"]), ([0] * 16, 1))

    def test_unavailable(self):
        self.assertEqual(rw.lyric_repeats(None, 10.0), ({"status": "unavailable", "reason": "no_words_input"}, []))
        self.assertEqual(rw.lyric_repeats(None, 10.0, "result_missing")[0]["reason"], "result_missing")
        self.assertEqual(rw.lyric_repeats([], 10.0)[0], {"status": "unavailable", "reason": "no_words"})
        words, end = _chars("あ" * 49, 0.0)
        self.assertEqual(rw.lyric_repeats(words, end)[0], {"status": "unavailable", "reason": "too_little_text"})
        words, end = _chars("、。！ 「」…" * 20, 0.0)  # punctuation only
        self.assertEqual(rw.lyric_repeats(words, end)[0]["reason"], "too_little_text")
        words, end = _chars("あ" * 50, 0.0)
        self.assertEqual(rw.lyric_repeats(words, end)[0]["status"], "done")
        for d in (rw.lyric_repeats(None, 1.0)[0], rw.lyric_repeats([], 1.0)[0]):
            self.check(d, 1.0)

    def test_normalization(self):
        self.assertEqual(rw.lyric_norm(" Ｈｅｌｌｏ、World！ ﾗﾗﾗ"), "helloworldラララ")  # NFKC, lower, no punct
        self.assertEqual(rw.lyric_norm("ｶﾞ"), "ガ")  # half-width kana + voiced mark compose
        self.assertEqual(rw.lyric_norm("e\u0301"), "é")
        self.assertEqual(rw.lyric_norm("♪★ 123"), "123")  # symbols dropped, digits kept
        # the same chorus written with punctuation, full-width and case differences still matches
        a, t = _chars("ＬＯＶＥきみのなまえをよぶよ", 0.0)
        v1, t = _chars(VERSES[0] + VERSES[1], t + 2)
        b, t = _chars("love、きみの　なまえを「よぶよ」！", t + 2)
        v2, t = _chars(VERSES[2], t + 2)
        d, _ = rw.lyric_repeats(a + v1 + b + v2, t)
        self.assertEqual([x["group"] for x in d["blocks"]], [0, 0], d)

    def test_english_spaced_words(self):
        chorus = " we will find our way back home tonight".split(" ")[1:]
        verse = [" the city lights are fading into gray and cold", " i walked along the river where we used to go",
                 " no one waits for me at the station anymore"]
        words, t = [], 0.0
        for line in (verse[0], " " + " ".join(chorus), verse[1], " " + " ".join(chorus), verse[2],
                     " " + " ".join(chorus)):
            for w in line.split(" ")[1:]:
                words.append((" " + w.capitalize(), round(t, 3), round(t + 0.5, 3)))  # Whisper: leading space
                t += 0.6
            t += 2.0
        d, _ = rw.lyric_repeats(words, t)
        self.check(d, t)
        self.assertEqual((d["unit"], d["n"]), ("word", 4))
        self.assertEqual([b["group"] for b in d["blocks"]], [0, 0, 0])
        self.assertAlmostEqual(d["blocks"][0]["end"] - d["blocks"][0]["start"], 4.7, delta=0.01)  # 8 words
        # verses alone share only short words ("the", "we") and letter runs: no block with 4-word n-grams
        verses = [w for w in words if not any(w[1] >= b["start"] and w[2] <= b["end"] for b in d["blocks"])]
        d2 = rw.lyric_repeats(verses, t)[0]
        self.assertEqual((d2["unit"], d2["blocks"]), ("word", []))

    def test_order_clamp_and_cap(self):
        words, end = _song([CHORUS, VERSES[0], VERSES[1], CHORUS])
        d1 = rw.lyric_repeats(words, end)[0]
        self.assertEqual(rw.lyric_repeats(words[::-1], end)[0], d1)  # sorted by start
        d2 = rw.lyric_repeats(words + [("x", -5.0, 900.0)], 30.0)[0]  # clamped into [0, duration]
        self.check(d2, 30.0)
        self.assertTrue(all(0 <= b["start"] < b["end"] <= 30.0 for b in d2["blocks"]))
        # 45 distinct refrains each sung twice 20 s apart -> 90 blocks -> the 40 longest, in time order
        words, t = [], 0.0
        lines = ["%02dあいうえおかきくけこさしすせそ" % i for i in range(45)]
        for i, line in enumerate(lines):
            w, t = _chars(line + "ん" * (i % 3), t)
            words += w
            w, t = _chars("たちつてとなにぬねの" * 6, t + 1.0)  # >= 15 s apart, no shared 8-gram with the lines
            words += [(c, a, b) for c, a, b in w]
            t += 1.0
        for i, line in enumerate(lines):
            w, t = _chars(line + "ん" * (i % 3), t)
            words += w
            t += 4.0
        d, warns = rw.lyric_repeats(words, t)
        self.check(d, t)
        self.assertEqual(len(d["blocks"]), 40)
        self.assertEqual(len(warns), 1)
        self.assertTrue(warns[0].startswith("lyric_repeats_capped:"))
        self.assertEqual([b["start"] for b in d["blocks"]], sorted(b["start"] for b in d["blocks"]))

    def test_parse_words(self):
        ok = json.dumps({"words": [["a", 1, 2.5], ["", 3, 3]]}).encode()
        self.assertEqual(rw.parse_words(ok), ([("a", 1.0, 2.5), ("", 3.0, 3.0)], None))
        self.assertEqual(rw.parse_words(b'{"words":null,"reason":"result_missing"}'), (None, "result_missing"))
        for bad in (b"{", b"[]", b'{"words":null,"reason":"Bad"}', b'{"words":null}', b'{"words":5}',
                    b'{"words":[["a",1]]}', b'{"words":[["a",NaN,2]]}', b'{"words":[["a",true,2]]}',
                    b'{"words":[[5,1,2]]}', b'{"words":[["' + b"x" * 201 + b'",1,2]]}', b"\xff",
                    json.dumps({"words": [["a", 1, 2]] * 20001}).encode()):
            self.assertEqual(rw.parse_words(bad), (None, "words_invalid"), bad[:40])
        self.assertEqual(rw.parse_words(b" " * (rw.WORDS_MAX_BYTES + 1)), (None, "words_too_large"))
        # review F2: parser depth and float conversion failures are malformed input, not a worker failure
        for bad in (b'{"words":' + b"[" * 5000 + b"]" * 5000 + b"}", b"[" * 100000,
                    b'{"words":[["a",1' + b"0" * 400 + b',2]]}', b'{"words":[["a",1,-1' + b"0" * 400 + b']]}'):
            self.assertEqual(rw.parse_words(bad), (None, "words_invalid"), bad[:30])

    def test_structure_debug(self):
        nov = [None] * 3 + [0.1, 0.5, 0.1, 0.0, 0.05, 0.12, 0.05, 0.0, 0.2, 0.1] + [None] * 3
        times = [round(0.5 * i, 3) for i in range(len(nov))]
        d = rw.structure_debug(nov, times, [4], [[1.0, 0.3], [0.3, 1.0]])
        self.assertEqual(d["novelty"]["times"], times[3:13])
        self.assertEqual(d["novelty"]["values"], nov[3:13])
        self.assertEqual((d["kernel_beats"], d["min_gap_beats"], d["novelty_threshold"], d["group_threshold"]),
                         (16, 8, 0.15, 0.75))
        self.assertEqual(d["peaks"], [])  # +-8 beats: only index 8 is far enough from both ends, and 0.5 is in reach
        self.assertEqual(d["section_similarity"], [[1.0, 0.3], [0.3, 1.0]])
        long = [None] * 3 + [0.0] * 34 + [None] * 3
        long[10], long[18], long[25] = 0.5, 0.02, 0.1
        d = rw.structure_debug(long, [0.5 * i for i in range(40)], [10], None)
        self.assertEqual(d["peaks"], [{"time": 5.0, "value": 0.5, "selected": True},
                                      {"time": 12.5, "value": 0.1, "selected": False}])  # below threshold kept too
        self.assertEqual(len(d["novelty"]["times"]), 34)
        self.assertEqual(rw.novelty_peaks(nov, min_gap=2), [4, 8, 11])
        self.assertEqual(rw.novelty_peaks(nov, min_gap=2, thresh=0.15), [4, 11])
        self.assertEqual(rw.pick_boundaries(nov, min_gap=2), [4, 11])
        d = rw.structure_debug([None] * 4, [0.0, 1.0, 2.0, 3.0], [], None)
        self.assertEqual((d["novelty"], d["peaks"], d["section_similarity"]), ({"times": [], "values": []}, [], None))
        # a selected boundary is always kept, then the strongest others up to the cap
        big = [None] * 8 + [0.01 * (i % 7) if i % 9 else 0.9 for i in range(4000)] + [None] * 8
        d = rw.structure_debug(big, list(range(len(big))), [8], None)
        self.assertLessEqual(len(d["peaks"]), rw.SEC_DEBUG_PEAKS)
        self.assertIn({"time": 8, "value": 0.9, "selected": True}, d["peaks"])


class LyricApproxTest(unittest.TestCase):
    """lyric_repeats 0.4.4: katakana fold and approximate 10-char windows (char units only)."""

    def check(self, d, dur):
        self.assertTrue(server._lyric_repeats_ok(json.loads(json.dumps(d)), dur + 0.5), d)

    def test_misspelled_chorus_found_edges_trimmed(self):
        # one char changed every 5: no exact 8-gram survives, the 10-char windows within 2 edits do
        bad = "きみのなまえをよんでいるよずっと"
        bad = bad[:4] + "ば" + bad[5:9] + "た" + bad[10:14] + "し" + bad[15:]
        words, end = _song([VERSES[0], CHORUS, VERSES[1], VERSES[2], bad])
        d, warns = rw.lyric_repeats(words, end)
        self.check(d, end)
        self.assertEqual((d["status"], warns, d["group_count"]), ("done", [], 1))
        self.assertEqual(len(d["blocks"]), 2)
        starts = [w[1] for w in words if w[0] == "き" and words[words.index(w) + 1][0] == "み"]
        self.assertEqual([b["start"] for b in d["blocks"]], starts)  # trimmed: the verse before is not covered
        rw_prev = rw.LYRIC_APPROX_K
        try:
            rw.LYRIC_APPROX_K = 0  # exact windows only: nothing matches
            self.assertEqual(rw.lyric_repeats(words, end)[0]["blocks"], [])
        finally:
            rw.LYRIC_APPROX_K = rw_prev

    def test_katakana_fold(self):
        self.assertEqual(rw.kana_fold("ラララ ヴァンパイア ゃ"), "ららら ゔぁんぱいあ ゃ")
        words, end = _song([VERSES[0], CHORUS, VERSES[1], VERSES[2], rw.kana_fold(CHORUS).translate(
            {ord(c): ord(c) + 0x60 for c in "きみのなまえをよんでいるよずっと"})])
        d, _ = rw.lyric_repeats(words, end)
        self.assertEqual(len(d["blocks"]), 2)  # the same chorus in katakana matches

    def test_spread_exact_15s_under_time_shift(self):
        # review F1 (0.4.4): 16.4 - 1.4 < 15 in binary floats; an exact 15 s spread must count at every offset
        self.assertTrue(rw.lyric_spread_ok(1.4, 16.4))
        self.assertTrue(rw.lyric_spread_ok(17.3, 32.3))
        self.assertFalse(rw.lyric_spread_ok(1.4, 16.399))
        filler = "".join(chr(0x4e00 + k) for k in range(40))
        for shift in (0.0, 0.1, 0.4, 1.4, 7.3, 17.3):
            words = []
            for t0, text in ((0.0, "abcdefghij"), (15.0, "abcdxfghyj")):
                words += [(c, shift + t0 + 0.5 * k, shift + t0 + 0.5 * k + 0.45) for k, c in enumerate(text)]
            words += [(c, shift + 30.0 + 0.5 * k, shift + 30.0 + 0.5 * k + 0.45) for k, c in enumerate(filler)]
            d, _ = rw.lyric_repeats(words, shift + 60.0)
            self.assertEqual((len(d["blocks"]), d["group_count"]), (2, 1), shift)
            # just under 15 s: no pair (the exact 8-gram gate uses the same comparison)
            near = [(c, a - 0.001 if a >= shift + 15.0 and a < shift + 30.0 else a,
                     b - 0.001 if a >= shift + 15.0 and a < shift + 30.0 else b) for c, a, b in words]
            self.assertEqual(rw.lyric_repeats(near, shift + 60.0)[0]["blocks"], [], shift)
        words, end = _song([VERSES[0], CHORUS, VERSES[1], VERSES[2], CHORUS])
        shifted = [(c, a + 1.4, b + 1.4) for c, a, b in words]
        self.assertEqual(len(rw.lyric_repeats(shifted, end + 1.4)[0]["blocks"]), 2)

    def test_close_windows_not_paired(self):
        # the same misspelled line within 15 s is a doubled line, not a repeat
        bad = CHORUS[:6] + "x" + CHORUS[7:]
        words, end = _song([CHORUS, bad, VERSES[0], VERSES[1]], gap=0.5)
        self.assertEqual(rw.lyric_repeats(words, end)[0]["blocks"], [])

    def test_pairs_capped_and_frequent_seeds_skipped(self):
        units = rw.lyric_units(_song([VERSES[0], CHORUS, VERSES[1], VERSES[2], CHORUS])[0], 60.0)[0]
        prev = rw.LYRIC_APPROX_MAX_CHECKS
        try:
            rw.LYRIC_APPROX_MAX_CHECKS = 3
            pairs, capped = rw.lyric_approx_pairs(units)
            self.assertTrue(capped)
            self.assertLessEqual(len(pairs), 3)
            words, end = _song([VERSES[0], CHORUS, VERSES[1], VERSES[2], CHORUS])
            self.assertEqual(rw.lyric_repeats(words, end)[1], ["lyric_repeats_approx_capped: 3 window checks"])
        finally:
            rw.LYRIC_APPROX_MAX_CHECKS = prev
        # a seed repeated more than LYRIC_APPROX_SEED_MAX times gives no approximate pair
        filler = [(c, 0.2 * i, 0.2 * i + 0.18) for i, c in enumerate("らららら" * 70)]
        self.assertEqual(rw.lyric_approx_pairs(filler), ([], False))
        # bounded on a hard input: 12000 chars over 4 letters stops at the check cap
        import random
        rnd = random.Random(1)
        words = [(rnd.choice("あいうえ"), i * 0.05, i * 0.05 + 0.05) for i in range(12000)]
        d, warns = rw.lyric_repeats(words, 600.0)
        self.check(d, 600.0)
        self.assertEqual(warns, ["lyric_repeats_approx_capped: %d window checks" % rw.LYRIC_APPROX_MAX_CHECKS])


class ScoopJoinTest(unittest.TestCase):
    """Worker 0.20.0 scoop join on vocal notes (pure)."""

    def test_scoop_into_note(self):
        # the synthetic 200-cent scoop through melody(): G#4 0.104 s, gap 16 ms, A4 0.368 s
        m = rw.scoop_join([_n(5.208, 5.312, 68), _n(5.328, 5.696, 69)])
        self.assertEqual(m, [_n(5.208, 5.696, 69)])
        self.assertEqual(rw.scoop_join([_n(0.0, 0.12, 67), _n(0.15, 0.4, 69)]), [_n(0.0, 0.4, 69)])  # 2 up, 120 ms, 30 ms gap

    def test_left_alone(self):
        cases = (
            [_n(0.0, 0.121, 68), _n(0.121, 0.6, 69)],  # 121 ms: longer than a scoop
            [_n(0.0, 0.1, 68), _n(0.131, 0.6, 69)],  # 31 ms gap
            [_n(0.0, 0.1, 70), _n(0.1, 0.6, 69)],  # falls into the note
            [_n(0.0, 0.1, 66), _n(0.1, 0.6, 69)],  # 3 semitones
            [_n(0.0, 0.1, 68), _n(0.1, 0.199, 69)],  # next note under twice as long
            [_n(0.0, 0.1, 69), _n(0.1, 0.6, 69)],  # same pitch (merge's job)
            [_n(0.0, 0.1, 64), _n(0.1, 0.2, 65), _n(0.2, 0.3, 67)],  # quick rising run, no long target
        )
        for notes in cases:
            self.assertEqual(rw.scoop_join(notes), notes, notes)
        self.assertEqual(rw.scoop_join([]), [])

    def test_dip_inside_held_note(self):
        # A4 with a 0.1 s dip to G#4: the scoop rule takes the dip into the second A; melody() then joins the two As
        n = [_n(0.0, 0.5, 69), _n(0.5, 0.6, 68), _n(0.6, 1.0, 69)]
        self.assertEqual(rw.scoop_join(n), [_n(0.0, 0.5, 69), _n(0.5, 1.0, 69)])
        self.assertEqual(rw.same_pitch_join(rw.scoop_join(n), rw.MERGE_GAP), [_n(0.0, 1.0, 69)])

    def test_once_left_to_right(self):
        # two scoops in a row each join their own target; the joined note is not a scoop source again
        n = [_n(0.0, 0.1, 67), _n(0.1, 0.5, 68), _n(0.5, 0.6, 69), _n(0.6, 1.0, 70)]
        self.assertEqual(rw.scoop_join(n), [_n(0.0, 0.5, 68), _n(0.5, 1.0, 70)])


class MergeLogicTest(unittest.TestCase):
    """Worker 0.4.2 note merge (pure; the 0.2 s contour median needs numpy and is covered by RealWorkerTest)."""

    def midis(self, notes):
        return [x["midi"] for x in rw.merge_notes(notes)]

    def test_oscillation_joins(self):
        # real-song shapes from 0.4.1 (8 songs): 70/72 every ~0.1 s around 71; 64/66 pieces around 65
        self.assertEqual(self.midis(_seq([(70, 0.1), (72, 0.1)] * 6)), [71])  # mean 71, not an extreme
        self.assertEqual(self.midis(_seq([(m, 0.12) for m in (65, 64, 66, 64, 66, 64, 66, 64)])), [65])
        m = rw.merge_notes(_seq([(70, 0.1), (71, 0.1)] * 3))
        self.assertEqual((len(m), m[0]["midi"], m[0]["note"]), (1, 71, "B4"))  # mean 70.5 rounds half up
        self.assertEqual((m[0]["start"], m[0]["end"]), (0.0, 0.64))
        # 2-semitone run: middle-pitch pieces may be long (< 0.5 s), the excursions must be short
        self.assertEqual(self.midis(_seq([(72, 0.1), (71, 0.33), (70, 0.09), (71, 0.34), (72, 0.09), (71, 0.4)])),
                         [71])
        self.assertEqual(self.midis(_seq([(72, 0.1), (71, 0.6), (70, 0.09), (71, 0.34), (72, 0.09)])),
                         [72, 71, 70, 71, 72])  # centre piece 0.6 s: not vibrato
        # A-B-A-B = 2 sign changes is enough; one turn (A-B-A) is not
        self.assertEqual(self.midis(_seq([(72, 0.1), (74, 0.1), (72, 0.1), (74, 0.1)])), [73])  # trill: merged
        self.assertEqual(self.midis(_seq([(72, 0.1), (74, 0.1), (72, 0.6)])), [72, 74, 72])  # mordent
        # a merged vibrato absorbs touching notes of its own pitch (same-pitch join afterwards)
        self.assertEqual(self.midis(_seq([(71, 0.6)] + [(70, 0.1), (72, 0.1)] * 3 + [(71, 0.8)])), [71])

    def test_kept_intact(self):
        for pairs in ([(m, 0.12) for m in (60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62, 60)],  # fast run
                      [(74, 0.12), (72, 0.12), (71, 0.12), (72, 0.6)],  # turn: one sign change
                      [(77, 0.15), (78, 0.15), (76, 1.1), (78, 0.15), (76, 1.1), (78, 0.15)],  # long alternation
                      [(72, 0.1), (75, 0.1), (72, 0.1), (75, 0.1)],  # oscillation wider than 2 semitones
                      [(60, 0.1), (61, 0.1), (62, 0.1), (61, 0.1)]):  # rises then turns once
            notes = _seq(pairs)
            self.assertEqual(rw.merge_notes(notes), notes, pairs)
        # pieces further apart than 30 ms are not touching
        self.assertEqual(self.midis(_seq([(70, 0.1), (72, 0.1)] * 4, gap=0.04)), [70, 72] * 4)

    def test_thresholds_offset_independent(self):
        # review (0.4.2) F1: four exact 200 ms pieces with 8 ms gaps are not "shorter than 0.2 s" at any offset
        # (float subtraction gave 10.2 - 10.0 = 0.1999999999999993 and merged them at 10 s but not at 0 s)
        for off in (0.0, 10.0, 123.456):
            notes = [_n(round(off + k * 0.208, 3), round(off + k * 0.208 + 0.2, 3), m)
                     for k, m in enumerate((60, 61, 60, 61))]
            self.assertEqual(rw.merge_notes(notes), notes, off)
            short = [dict(x, end=round(x["end"] - 0.001, 3)) for x in notes]  # 199 ms: merged
            self.assertEqual([x["midi"] for x in rw.merge_notes(short)], [61], off)  # mean 60.5 -> half up
            # middle-pitch allowance in a 2-semitone run: exactly 500 ms is not shorter than 0.5 s
            for centre, want in ((0.5, [72, 71, 70, 71, 72, 71]), (0.499, [71])):
                run = [_n(round(off, 3), round(off + 0.1, 3), 72)]
                for m, d in ((71, centre), (70, 0.1), (71, 0.3), (72, 0.1), (71, 0.3)):
                    t = run[-1]["end"] + 0.008
                    run.append(_n(round(t, 3), round(t + d, 3), m))
                self.assertEqual([x["midi"] for x in rw.merge_notes(run)], want, (off, centre))
            # 30 ms gap joins, 31 ms does not, same units at every offset
            for g, want in ((0.03, 1), (0.031, 2)):
                pair = [_n(round(off, 3), round(off + 0.5, 3), 72), _n(round(off + 0.5 + g, 3), round(off + 0.9, 3), 72)]
                self.assertEqual(len(rw.merge_notes(pair)), want, (off, g))
                osc = _seq([(70, 0.1), (72, 0.1)] * 3, t0=off, gap=g)
                self.assertEqual(len(rw.merge_notes(osc)), 1 if want == 1 else 6, (off, g))

    def test_half_up_offset_independent(self):
        # review (0.4.2) F2: exact mean 60.5 (four 104 ms pieces) rounds up to 61 at every offset (the float mean
        # 60.49999999999999 gave 60 at offset 0 and 61 at offset 1)
        for off in (0.0, 1.0, 10.0, 77.777):
            notes = [_n(round(off + k * 0.104, 3), round(off + (k + 1) * 0.104, 3), m)
                     for k, m in enumerate((60, 61, 60, 61))]
            m = rw.merge_notes(notes)
            self.assertEqual((len(m), m[0]["midi"]), (1, 61), off)
            self.assertEqual((m[0]["start"], m[0]["end"]), (notes[0]["start"], notes[-1]["end"]))
            below = [dict(notes[0], end=round(notes[0]["end"] + 0.001, 3)),
                     dict(notes[1], start=round(notes[1]["start"] + 0.001, 3))] + notes[2:]  # 60 weighs 1 ms more
            self.assertEqual(rw.merge_notes(below)[0]["midi"], 60, off)

    def test_same_pitch_join(self):
        m = rw.merge_notes([_n(0.0, 0.5, 72), _n(0.52, 0.9, 72), _n(0.93, 1.2, 72), _n(1.3, 1.6, 72)])
        self.assertEqual([(x["start"], x["end"]) for x in m], [(0.0, 1.2), (1.3, 1.6)])  # 20, 30 ms join; 100 ms not
        self.assertEqual(rw.merge_notes([_n(0.0, 0.5, 72), _n(0.531, 0.9, 72)])[1]["start"], 0.531)  # 31 ms
        self.assertEqual(rw.merge_notes([]), [])
        self.assertEqual(rw.merge_notes(MELODY_NOTES), MELODY_NOTES[:4] + [_n(2.0, 3.0, 72), MELODY_NOTES[6]])
        src = [_n(0.0, 0.5, 72), _n(0.51, 0.9, 72)]
        rw.merge_notes(src)
        self.assertEqual(src[0]["end"], 0.5)  # input rows untouched


class MelodyLogicTest(unittest.TestCase):
    """Pure helpers of worker 0.4.0 (note names, intervals, direction, summary); no numpy needed."""

    def test_note_name(self):
        self.assertEqual([rw.note_name(m) for m in (0, 21, 60, 61, 69, 71, 72, 127)],
                         ["C-1", "A0", "C4", "C#4", "A4", "B4", "C5", "G9"])
        self.assertTrue(all(rw.note_name(m) == server.note_name(m) for m in range(128)))

    def test_intervals(self):
        ns = [_n(0, 0.5, 60), _n(0.5, 1, 60), _n(1, 1.5, 62), _n(1.5, 2, 61), _n(2, 2.5, 67), _n(4, 4.5, 50)]
        iv = rw.melody_intervals(ns)  # 0, +2, -1, +6; the 1.5 s gap before the last note is a phrase break
        self.assertEqual(iv, {"count": 4, "repeat": 0.25, "step": 0.5, "leap": 0.25, "up": 0.5, "down": 0.25,
                              "max_gap_s": 1.0})
        self.assertEqual(rw.melody_intervals(ns, gap=2.0)["count"], 5)
        self.assertIsNone(rw.melody_intervals(ns[:1]))
        self.assertIsNone(rw.melody_intervals([]))

    def test_direction(self):
        up = [_n(i, i + 0.5, 60 + i) for i in range(4)]  # +3 semitones fitted over 3 s
        self.assertEqual(rw.melody_direction(up), "rising")
        self.assertEqual(rw.melody_direction([_n(i, i + 0.5, 60 - i) for i in range(4)]), "falling")
        self.assertEqual(rw.melody_direction([_n(i, i + 0.5, 60 + i % 2) for i in range(6)]), "flat")
        self.assertEqual(rw.melody_direction([_n(i, i + 0.5, 60 + i // 2) for i in range(4)]), "flat")  # fitted 1.6 < 2
        self.assertIsNone(rw.melody_direction(up[:3]))

    def test_summary(self):
        m = MELODY
        self.assertEqual((m["status"], m["note_count"], m["median_midi"]), ("done", 7, 67))  # weighted: 67 reaches half of 3.4 s
        self.assertEqual(m["range"], {"low": {"midi": 60, "note": "C4"}, "high": {"midi": 72, "note": "C5"},
                                      "p5": {"midi": 60, "note": "C4"}, "p95": {"midi": 72, "note": "C5"}})
        self.assertEqual(m["voiced_s"], round(len(MELODY_VOICED) * 0.008, 3))
        self.assertEqual(m["notes_per_second"], round(7 / 3.4, 3))  # notes / summed note duration
        self.assertEqual(m["sections"][0]["notes_per_second"], round(4 / 1.9, 3))
        # durations: C 0.5, D 0.5, E 0.5, G 0.4, C5 1.0, A 0.5 of 3.4 s
        self.assertEqual(m["scale_degrees"]["bins"],
                         [round(x / 3.4, 3) for x in (1.5, 0, 0.5, 0, 0.5, 0, 0, 0.4, 0, 0.5, 0, 0)])
        self.assertEqual(m["intervals"]["count"], 6)  # 2.5-3.0 -> 4.0 is a 1.0 s gap: still counted (<= 1.0)
        self.assertEqual([(s["label"], s["note_count"], s["low"], s["high"], s["median_midi"], s["direction"])
                          for s in m["sections"]],
                         [("A", 4, 60, 67, 62, "rising"), ("B", 2, 72, 72, 72, None), ("A", 1, 69, 69, 69, None)])
        self.assertEqual(m["sections"][0]["peak"], {"midi": 67, "note": "G4", "time": 1.5})
        self.assertEqual(m["sections"][1]["peak"]["time"], 2.0)  # ties: earliest
        g = rw.melody_summary(MELODY_NOTES, MELODY_VOICED, 0.008, 750, "Eb", "minor", [])
        self.assertEqual((g["scale_degrees"]["tonic"], g["scale_degrees"]["bins"][9], g["sections"]),
                         ("Eb", round(1.5 / 3.4, 3), []))  # C is degree 9 above Eb
        self.assertIsNone(rw.melody_summary(MELODY_NOTES, MELODY_VOICED, 0.008, 750, "X", "major", [])["scale_degrees"])


MODEL_PRESENT = os.path.isfile(rw.TEMPO_CNN_MODEL)
GENRE_PRESENT = all(os.path.isfile(os.path.join(rw.GENRE_DIR, f))
                    for f in (rw.GENRE_EMBED_FILE, rw.GENRE_HEAD_FILE, rw.GENRE_LABELS_FILE))
SYNTH_RUN = (60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62, 60)  # synth_audio.RUN
SYNTH_MELODY = (72, 74, 76, 79, 76, 74, 72, 71, 69, 71, 72, 74, 76, 77, 79, 81)  # synth_audio.MELODY (numpy-free copy)


def run_worker(wav, *opts, words=None):
    """words: bytes for --words-stdin (the option is added), or None (no option, stdin empty)."""
    if words is not None:
        opts = opts + ("--words-stdin",)
    p = subprocess.run([ESSENTIA_PY, "-I", WORKER] + list(opts) + [wav], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=300, input=words or b"",
                       env={"PATH": server.CHILD_ENV_PATH, "HOME": os.path.expanduser("~"), "LANG": "en_US.UTF-8",
                            "TMPDIR": tempfile.gettempdir()})
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.decode())


@unittest.skipUnless(os.path.isfile(ESSENTIA_PY), "Essentia python missing")
class RealWorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="mlrhythm-")
        cls.out = {}
        for kind, secs in (("click", "30"), ("chords", "30"), ("modulation", "60"), ("kick100", "30"),
                           ("kick100mid", "30"), ("abab", "60"), ("melody", "30"), ("melody_ab", "60")):
            wav = os.path.join(cls.dir, kind + ".wav")
            subprocess.run([ESSENTIA_PY, "-I", SYNTH, kind, secs, wav], check=True, timeout=120)
            assert server.strict_wav_check(wav)
            cls.out[kind] = run_worker(wav)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_shape(self):
        for v in self.out.values():
            self.assertIsNotNone(server.check_rhythm(v))
            self.assertEqual(v["downbeats"]["status"], "not_provided")
            self.assertEqual((v["sample_rate"], v["chords_reliability"]), (44100, "low_reference"))
            self.assertIn(v["duration"], (30.0, 60.0))
            self.assertEqual(v["tool"]["essentia_version"], "2.1-beta6-dev")
            self.assertEqual(v["tool"]["version"], "0.26.0")
            g = v["genre"]
            self.assertEqual(g["status"], "done" if GENRE_PRESENT else "unavailable", g)
            if GENRE_PRESENT:  # measured: 29 patches for 30 s, 59 for 60 s (hop 0.992 s, first from ~2.04 s)
                self.assertEqual((g["patches"], len(g["windows"]), g["patch_hop_s"]),
                                 ({30.0: 29, 60.0: 59}[v["duration"]], int(v["duration"] // 30), 0.992))
                self.assertEqual((len(g["top"]), len(g["parents"]), g["model"]),
                                 (5, 5, "genre_discogs400-discogs-effnet-1"))
            self.assertEqual(v["lyric_repeats"], {"status": "unavailable", "reason": "no_words_input"})
            sd = v["structure_debug"]
            self.assertEqual([p["time"] for p in sd["peaks"] if p["selected"]], [x["start"] for x in v["sections"][1:]])
            self.assertEqual(len(sd["novelty"]["times"]), max(0, len([b for b in v["beats"]
                                                                      if 0 < b < v["duration"]]) - 32))
            self.assertEqual(v["melody"]["status"], "done")
            self.assertGreaterEqual(v["melody"]["note_count_raw"], v["melody"]["note_count"])
            self.assertEqual(len(v["melody_notes"]), v["melody"]["note_count"])
            e = v["energy"]
            self.assertEqual((e["unit"], e["step_s"], len(e["values"])), ("dBFS", 1.0, int(v["duration"])))
            self.assertEqual(v["energy_summary"]["window_s"], 8.0)
            self.assertEqual(v["sections_reliability"], "low_reference")
            ss = v["sections"]
            self.assertEqual((ss[0]["start"], ss[0]["label"], ss[-1]["end"]), (0.0, "A", v["duration"]))
            self.assertEqual([a["end"] for a in ss[:-1]], [b["start"] for b in ss[1:]])  # contiguous
            self.assertEqual(v["tempo_cnn"]["status"], "done" if MODEL_PRESENT else "unavailable", v["tempo_cnn"])
            self.assertIsNotNone(v["double_tempo_evidence"]["mid_to_beat_ratio"])
            self.assertTrue(v["double_tempo_evidence_note"])
            self.assertEqual(v["key"]["profile"], "bgate")
            self.assertEqual(v["key_change_count"], len(v["key_sections"]) - 1)
            self.assertEqual((v["key_sections"][0]["start"], v["key_sections"][-1]["end"]), (0.0, v["duration"]))
            self.assertEqual(v["chords_grid"], "beat")
            self.assertTrue(v["bpm_alternatives_note"])

    def test_click_120_bpm(self):
        v = self.out["click"]
        self.assertAlmostEqual(v["bpm"], 120.0, delta=2.0)
        self.assertGreater(len(v["beats"]), 50)
        gaps = [b - a for a, b in zip(v["beats"], v["beats"][1:])]
        self.assertAlmostEqual(sorted(gaps)[len(gaps) // 2], 0.5, delta=0.02)
        self.assertEqual(v["bpm_alternatives"], [60.0, 240.0])

    @unittest.skipUnless(MODEL_PRESENT, "TempoCNN model missing")
    def test_tempo_cnn_click_120(self):
        # measured (worker 0.2.2, 30 s): click 120 (top 120 .817 / 121 .171 / 161 .003), kick100 100, kick100mid 100
        tc = self.out["click"]["tempo_cnn"]
        self.assertAlmostEqual(tc["bpm"], 120.0, delta=2.0)
        self.assertAlmostEqual(tc["top"][0]["bpm"], 120.0, delta=2.0)
        self.assertEqual((tc["model"], tc["model_sha256"], tc["local_count"]),
                         ("deeptemp-k16-3", rw.TEMPO_CNN_SHA256, 4))  # 30 s: patches of ~11.9 s every ~5.9 s
        self.assertAlmostEqual(self.out["kick100"]["tempo_cnn"]["bpm"], 100.0, delta=2.0)
        self.assertEqual(self.out["click"]["bpm_alternatives"], [60.0, 240.0])  # bpm fields untouched

    def test_tempo_cnn_model_unavailable_rest_runs(self):
        wav = os.path.join(self.dir, "click.wav")
        other = os.path.join(self.dir, "other.pb")
        shutil.copyfile(SYNTH, other)
        link = os.path.join(self.dir, "link.pb")
        os.symlink(rw.TEMPO_CNN_MODEL, link)
        for model, reason in ((os.path.join(self.dir, "missing.pb"), "model_missing"), (other, "sha256_mismatch"),
                              (link, "not_regular_file")):
            v = run_worker(wav, "--tempo-cnn-model", model)
            self.assertEqual(v["tempo_cnn"], {"status": "unavailable", "reason": reason})
            self.assertIsNotNone(server.check_rhythm(v))
            self.assertAlmostEqual(v["bpm"], 120.0, delta=2.0)
            self.assertGreater(len(v["beats"]), 50)
            self.assertTrue(v["key"]["tonic"])
            self.assertTrue(v["chords"])
            ref = dict(self.out["click"], tempo_cnn=v["tempo_cnn"])
            self.assertEqual(v, ref)  # everything else identical to the run with the model

    @unittest.skipUnless(MODEL_PRESENT, "TempoCNN model missing")
    def test_tempo_cnn_long_patching(self):
        # 241 s (an odd length, ~4 min like the real song that failed): counts and argmax agree with the aligned
        # batch/patch parameters, so the result is done with no patch-count warning
        wav = os.path.join(self.dir, "chords241.wav")
        subprocess.run([ESSENTIA_PY, "-I", SYNTH, "chords", "241", wav], check=True, timeout=120)
        v = run_worker(wav)
        tc = v["tempo_cnn"]
        self.assertEqual(tc["status"], "done", tc)
        self.assertEqual(tc["local_count"], 39)  # measured: 39 patches for 240-241 s
        self.assertAlmostEqual(tc["bpm"], 120.0, delta=2.0)
        self.assertFalse(any(w.startswith("tempo_cnn_patch_count_mismatch") for w in v["warnings"]))
        self.assertIsNotNone(server.check_rhythm(v))

    @unittest.skipUnless(MODEL_PRESENT, "TempoCNN model missing")
    def test_tempo_cnn_short_audio_and_copy(self):
        short = os.path.join(self.dir, "short.wav")
        subprocess.run([ESSENTIA_PY, "-I", SYNTH, "click", "8", short], check=True, timeout=120)
        self.assertEqual(run_worker(short)["tempo_cnn"], {"status": "failed", "code": "too_short"})  # < one patch
        copy = os.path.join(self.dir, "copy.pb")  # a byte-identical copy passes the pin
        shutil.copyfile(rw.TEMPO_CNN_MODEL, copy)
        self.assertEqual(run_worker(os.path.join(self.dir, "click.wav"), "--tempo-cnn-model", copy)["tempo_cnn"],
                         self.out["click"]["tempo_cnn"])

    def test_genre_models_unavailable_rest_runs(self):
        wav = os.path.join(self.dir, "click.wav")
        off = os.path.join(self.dir, "genre-off")
        links = os.path.join(self.dir, "genre-links")
        os.mkdir(off)
        os.mkdir(links)
        for name in (rw.GENRE_EMBED_FILE, rw.GENRE_HEAD_FILE, rw.GENRE_LABELS_FILE):
            shutil.copyfile(SYNTH, os.path.join(off, name))
            os.symlink(os.path.join(rw.GENRE_DIR, name), os.path.join(links, name))
        for d, reason in ((os.path.join(self.dir, "genre-missing"), "model_missing"), (off, "sha256_mismatch"),
                          (links, "not_regular_file")):
            v = run_worker(wav, "--genre-models", d)
            self.assertEqual(v["genre"], {"status": "unavailable", "reason": reason})
            self.assertIsNotNone(server.check_rhythm(v))
            self.assertEqual(v, dict(self.out["click"], genre=v["genre"]))  # everything else identical

    @unittest.skipUnless(GENRE_PRESENT, "Discogs genre models missing")
    def test_genre_short_audio_and_copy(self):
        short = os.path.join(self.dir, "short2.wav")
        subprocess.run([ESSENTIA_PY, "-I", SYNTH, "chords", "1.5", short], check=True, timeout=120)
        self.assertEqual(run_worker(short)["genre"], {"status": "unavailable", "reason": "too_short"})  # < 1 patch
        three = os.path.join(self.dir, "three.wav")
        subprocess.run([ESSENTIA_PY, "-I", SYNTH, "chords", "3", three], check=True, timeout=120)
        g = run_worker(three)["genre"]
        self.assertEqual((g["status"], g["patches"], [(w["start"], w["end"]) for w in g["windows"]]),
                         ("done", 1, [(0.0, 3.0)]))
        copy = os.path.join(self.dir, "genre-copy")  # byte-identical copies pass the pins
        os.mkdir(copy)
        for name in (rw.GENRE_EMBED_FILE, rw.GENRE_HEAD_FILE, rw.GENRE_LABELS_FILE):
            shutil.copyfile(os.path.join(rw.GENRE_DIR, name), os.path.join(copy, name))
        self.assertEqual(run_worker(os.path.join(self.dir, "chords.wav"), "--genre-models", copy)["genre"],
                         self.out["chords"]["genre"])

    def test_double_tempo_evidence_midpoint_hits(self):
        a, b = self.out["kick100"], self.out["kick100mid"]
        da, db = a["double_tempo_evidence"], b["double_tempo_evidence"]
        # measured (worker 0.2.1): a 0.0 / b 1.011, fraction 0.0 / 1.0; the tracker said 100.04 BPM for both.
        # Only the direction is asserted, not the tracker's tempo for b (100 or 200 would both be legitimate).
        self.assertGreater(db["mid_to_beat_ratio"], da["mid_to_beat_ratio"] + 0.5)
        self.assertGreater(db["mid_strong_fraction"], da["mid_strong_fraction"] + 0.5)
        self.assertLess(self.out["click"]["double_tempo_evidence"]["mid_to_beat_ratio"], 0.2)
        self.assertGreater(min(da["pairs"], db["pairs"]), 30)

    def test_beat_grid_chords(self):
        for v in self.out.values():
            beats, chords = v["beats"], v["chords"]
            min_gap = min(b - a for a, b in zip(beats, beats[1:]))
            edges = {0.0, v["duration"]} | set(beats)
            for c in chords:
                self.assertIn(c["start"], edges)
                self.assertIn(c["end"], edges)
                if c["start"] >= beats[0] and c["end"] <= beats[-1]:  # not head or tail
                    self.assertGreaterEqual(c["end"] - c["start"], min_gap - 0.002, c)
            self.assertLessEqual(len(chords), v["chord_frame_segment_count"])
        # clicks carry no harmony, so frame labels flip; the beat vote removes most of that
        v = self.out["click"]
        self.assertLess(len(v["chords"]), v["chord_frame_segment_count"])

    def test_modulation_key_sections(self):
        v = self.out["modulation"]
        ks = v["key_sections"]
        self.assertEqual((ks[0]["tonic"], ks[0]["scale"]), ("C", "major"))
        self.assertEqual((ks[-1]["tonic"], ks[-1]["scale"]), ("D", "major"))
        # the true boundary is 30 s; with 20 s windows the C section ends at 20 or 40 and the D section starts at
        # 20 or 40 (tolerance one window). A window straddling the change may report a third key (seen: G major).
        self.assertIn(ks[0]["end"], (20.0, 40.0))
        self.assertIn(ks[-1]["start"], (20.0, 40.0))
        self.assertLessEqual(len(ks), 3)
        self.assertTrue(any(w.startswith("key_changes:") for w in v["warnings"]))
        self.assertEqual(self.out["chords"]["key_change_count"], 0)
        self.assertFalse(any(w.startswith("key_changes:") for w in self.out["chords"]["warnings"]))

    def test_chords_key_c_major_or_a_minor(self):
        v = self.out["chords"]
        self.assertIn((v["key"]["tonic"], v["key"]["scale"]), (("C", "major"), ("A", "minor")))
        self.assertTrue(v["chords"])
        labels = {c["label"] for c in v["chords"]}
        self.assertTrue(labels & {"C", "Am", "F", "G"}, labels)

    def test_sections_abab(self):
        # measured (worker 0.3.0): A 0-15, B 15-30, A 30-45, B 45-60 exactly on the block edges (beats at 15/30/45 s);
        # energy_mean A -26.68 / B -18.77 dB; peaks 15-23, 45-53, 23-31; quietest 30-38
        v = self.out["abab"]
        ss = v["sections"]
        self.assertEqual([s["label"] for s in ss], ["A", "B", "A", "B"], ss)
        for s, edge in zip(ss[1:], (15.0, 30.0, 45.0)):
            self.assertAlmostEqual(s["start"], edge, delta=2.0)
        a = [s["energy_mean"] for s in ss if s["label"] == "A"]
        b = [s["energy_mean"] for s in ss if s["label"] == "B"]
        self.assertGreater(min(b), max(a) + 5.0)
        self.assertEqual([s["top_chord"] in ("C", "Am", "F", "G") for s in ss], [True, False, True, False])
        self.assertEqual([s["top_chord"] in ("E", "C#m", "A", "B") for s in ss], [False, True, False, True])
        self.assertGreater(min(s["similarity_to_first"] for s in ss), 0.9)
        sim = v["structure_debug"]["section_similarity"]  # 0.4.3: the full matrix behind the labels
        self.assertEqual(len(sim), 4)
        self.assertGreater(min(sim[0][2], sim[1][3]), 0.9)
        self.assertLess(max(sim[0][1], sim[0][3], sim[1][2]), 0.75)
        self.assertGreaterEqual(min(p["value"] for p in v["structure_debug"]["peaks"] if p["selected"]), 0.15)
        es = v["energy_summary"]
        b_spans = [(s["start"], s["end"]) for s in ss if s["label"] == "B"]
        for p in es["peaks"][:2]:  # the two loudest windows lie inside B sections
            self.assertTrue(any(lo - 0.5 <= p["start"] and p["end"] <= hi + 0.5 for lo, hi in b_spans), p)
        self.assertFalse(any(lo <= es["quietest"]["start"] < hi for lo, hi in b_spans))
        self.assertGreater(es["max"] - es["min"], 5.0)

    def test_melody_line(self):
        # measured (worker 0.4.0): all 60 notes of the 30 s line (16 notes x 0.5 s, A4..A5, C major) at the right
        # MIDI; voiced 0.952; intervals step 0.814 / leap 0.186 (3 leaps of 16 per cycle), up 0.61
        v = self.out["melody"]
        m, ns = v["melody"], v["melody_notes"]
        line = [SYNTH_MELODY[i % 16] for i in range(60)]
        self.assertEqual((m["range"]["low"], m["range"]["high"]), ({"midi": 69, "note": "A4"}, {"midi": 81, "note": "A5"}))
        self.assertLessEqual(abs(m["note_count"] - 60), 3)
        self.assertGreater(m["voiced_fraction"], 0.85)
        self.assertEqual([n["midi"] for n in ns][:16], line[:16])
        for n in ns[:16]:  # onsets within 50 ms of the 0.5 s grid
            self.assertLess(abs(n["start"] - round(n["start"] * 2) / 2.0), 0.05, n)
        iv = m["intervals"]
        self.assertAlmostEqual(iv["leap"], 3 / 16.0, delta=0.05)
        self.assertAlmostEqual(iv["step"], 13 / 16.0, delta=0.05)
        self.assertLess(iv["repeat"], 0.05)
        self.assertAlmostEqual(m["notes_per_second"], 2.0, delta=0.3)
        self.assertEqual((v["key"]["tonic"], v["key"]["scale"]), ("C", "major"))
        sd = m["scale_degrees"]
        self.assertEqual((sd["tonic"], sd["scale"]), ("C", "major"))
        self.assertLess(sum(sd["bins"][k] for k in (1, 3, 6, 8, 10)), 0.02)  # no note outside C major
        self.assertGreater(min(sd["bins"][k] for k in (0, 2, 4, 5, 7, 9, 11)), 0.0)
        self.assertEqual([s["direction"] for s in m["sections"]], ["flat"])  # one repeating loop

    def test_melody_sections_b_higher(self):
        # measured (worker 0.4.0): A 60-67 (median 64, peak G4), B 76-83 (median 80, peak B5), all flat; 2.11-2.40
        # notes per second of note time; 125 notes for 120 written with 0.4.1, 118 with 0.4.2 (raw 119; the contour
        # median removed most short glide notes at block edges)
        v = self.out["melody_ab"]
        ms = v["melody"]["sections"]
        self.assertEqual([(s["label"], s["start"], s["end"]) for s in ms],
                         [(s["label"], s["start"], s["end"]) for s in v["sections"]])
        self.assertEqual([s["label"] for s in ms], ["A", "B", "A", "B"])
        for s in ms:
            lo, hi = (60, 67) if s["label"] == "A" else (76, 83)
            self.assertAlmostEqual(s["median_midi"], (lo + hi) / 2.0, delta=2.0, msg=s)
            self.assertEqual(s["peak"]["midi"], hi, s)
            self.assertGreaterEqual(s["low"], lo - 1, s)
            self.assertAlmostEqual(s["notes_per_second"], 2.0, delta=0.5)
            self.assertEqual(s["direction"], "flat")
        a = max(s["median_midi"] for s in ms if s["label"] == "A")
        b = min(s["median_midi"] for s in ms if s["label"] == "B")
        self.assertGreaterEqual(b - a, 12)
        self.assertEqual(sum(s["note_count"] for s in ms), v["melody"]["note_count"])

    def test_vibrato_merges(self):
        # measured (worker 0.4.2, 5.5 Hz vibrato, held notes 74 72 69 71 of 1.46 s): +-50 cents 0.4.1 12 notes ->
        # 4 (raw 4); +-100 cents 0.4.1 53 notes (mean 0.096 s) -> 6 (raw 6): the 4 held notes plus two ~0.1 s
        # glide pieces (73, 71) where Melodia bridges the 40 ms rest between notes
        for kind in ("vibrato50", "vibrato100"):
            wav = os.path.join(self.dir, kind + ".wav")
            subprocess.run([ESSENTIA_PY, "-I", SYNTH, kind, "7", wav], check=True, timeout=120)
            v = run_worker(wav)
            self.assertIsNotNone(server.check_rhythm(v))
            ns = v["melody_notes"]
            held = [x for x in ns if x["end"] - x["start"] >= 1.2]
            self.assertEqual([x["midi"] for x in held], [74, 72, 69, 71], (kind, ns))
            self.assertTrue(all(x["end"] - x["start"] < 0.15 for x in ns if x not in held), (kind, ns))
            self.assertLessEqual(len(ns) - len(held), 3, kind)  # at most one glide piece per transition
            for a, b in zip(held, held[1:]):
                self.assertLess(b["start"] - a["end"], 0.2)

    def test_runs_ornaments_and_repeats_kept(self):
        wav = os.path.join(self.dir, "ornaments.wav")
        subprocess.run([ESSENTIA_PY, "-I", SYNTH, "ornaments", "6", wav], check=True, timeout=120)
        m = run_worker(wav)
        written = list(SYNTH_RUN) + [72, 74, 72, 74, 72, 71, 72]
        self.assertEqual([x["midi"] for x in m["melody_notes"]], written)  # nothing merged, nothing lost
        self.assertEqual(m["melody"]["note_count_raw"], len(written))
        wav = os.path.join(self.dir, "repeats.wav")
        subprocess.run([ESSENTIA_PY, "-I", SYNTH, "repeats", "3", wav], check=True, timeout=120)
        ns = run_worker(wav)["melody_notes"]
        self.assertEqual([x["midi"] for x in ns], [72] * 4, ns)  # 0.15 s rests: four notes, not one
        self.assertTrue(all(b["start"] - a["end"] > 0.03 for a, b in zip(ns, ns[1:])))

    def test_melody_silence_and_short(self):
        for kind, secs in (("silence", "5"), ("click", "1.5"), ("chords", "1.2")):
            wav = os.path.join(self.dir, "m-%s-%s.wav" % (kind, secs))
            subprocess.run([ESSENTIA_PY, "-I", SYNTH, kind, secs, wav], check=True, timeout=120)
            v = run_worker(wav)
            self.assertIsNotNone(server.check_rhythm(v), kind)
            m = v["melody"]
            self.assertEqual(m["status"], "done", m)
            if kind != "chords":  # silence and clicks carry no pitch
                self.assertEqual((m["note_count"], m["voiced_fraction"], m["range"], m["scale_degrees"],
                                  v["melody_notes"]), (0, 0.0, None, None, []), kind)
                self.assertIn("no_melody: no pitched note was found", v["warnings"])

    def test_silent_edges(self):
        # measured (worker 0.4.1): sections silence 0-9.96, A B A B, silence 69.97-90 (worker 0.4.0 labelled both
        # silent ends A, the music B/C); tempo_cnn 120 with 4 of 14 patches excluded (0.4.0, same tempo code as 0.3.1:
        # inconsistent_output, argmax_mismatch on the last patch, inside the silent tail); quietest 10-18 at -17.37
        # instead of -120
        wav = os.path.join(self.dir, "silent_edges.wav")
        subprocess.run([ESSENTIA_PY, "-I", SYNTH, "silent_edges", "90", wav], check=True, timeout=120)
        v = run_worker(wav)
        self.assertIsNotNone(server.check_rhythm(v))
        ss = v["sections"]
        self.assertEqual([s["label"] for s in ss], ["silence", "A", "B", "A", "B", "silence"], ss)
        self.assertAlmostEqual(ss[0]["end"], 10.0, delta=1.0)
        self.assertAlmostEqual(ss[-1]["start"], 70.0, delta=1.0)
        self.assertEqual((ss[0]["similarity_to_first"], ss[-1]["similarity_to_first"]), (None, None))
        es = v["energy_summary"]
        self.assertEqual((es["silence_s"], es["min"]), (30.0, -120.0))
        self.assertGreater(es["quietest"]["mean"], -60.0)
        self.assertTrue(10.0 <= es["quietest"]["start"] and es["quietest"]["end"] <= 70.0, es["quietest"])
        for p in es["peaks"]:
            self.assertTrue(10.0 <= p["start"] and p["end"] <= 70.0, p)
        self.assertEqual([s["label"] for s in v["melody"]["sections"]], [s["label"] for s in ss])
        self.assertEqual((v["melody"]["sections"][0]["note_count"], v["melody"]["sections"][-1]["note_count"]), (0, 0))
        if MODEL_PRESENT:
            tc = v["tempo_cnn"]
            self.assertEqual(tc["status"], "done", tc)
            self.assertAlmostEqual(tc["bpm"], 120.0, delta=2.0)
            self.assertEqual((tc["local_count"] + tc["silent_patches"], tc["silent_patches"]), (14, 4))
            self.assertGreaterEqual(tc["local_bpm_min"], 110.0)  # no vote from a silent patch

    def test_all_silent(self):
        wav = os.path.join(self.dir, "silence30.wav")
        subprocess.run([ESSENTIA_PY, "-I", SYNTH, "silence", "30", wav], check=True, timeout=120)
        v = run_worker(wav)
        self.assertIsNotNone(server.check_rhythm(v))
        self.assertEqual([s["label"] for s in v["sections"]], ["silence"])
        es = v["energy_summary"]
        self.assertEqual((es["peaks"], es["quietest"], es["silence_s"]), ([], None, 30.0))
        self.assertEqual(v["melody"]["note_count"], 0)
        if MODEL_PRESENT:
            self.assertEqual(v["tempo_cnn"], {"status": "failed", "code": "no_audible_patches"})
        if GENRE_PRESENT:
            self.assertEqual(v["genre"], {"status": "unavailable", "reason": "no_audible_patches"})

    def test_sections_single_texture(self):
        # one loop, one texture: no boundary (interior novelty stays <= ~0.12, threshold 0.15)
        for kind in ("chords", "click", "kick100"):
            self.assertEqual([s["label"] for s in self.out[kind]["sections"]], ["A"], kind)
        energy = self.out["chords"]["energy_summary"]
        self.assertLess(energy["max"] - energy["min"], 1.0)

    def test_errors_one_json_line(self):
        bad = os.path.join(self.dir, "bad.wav")
        with open(bad, "wb") as f:
            f.write(b"RIFF0000WAVEjunk")
        for args in ([bad], ["rel.wav"], [], ["--tempo-cnn-model", "rel.pb", bad], ["--tempo-cnn-model", bad],
                     ["--x", "/a.pb", bad], ["--words-stdin"], ["--words-stdin", "--words-stdin", bad],
                     ["--words-stdin", "rel.wav"], [bad, "--words-stdin"]):
            p = subprocess.run([ESSENTIA_PY, "-I", WORKER] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=60)
            self.assertEqual(p.returncode, 1)
            self.assertEqual(p.stdout, b"")
            lines = p.stderr.decode().strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn(json.loads(lines[0])["error"]["code"], ("invalid_audio", "invalid_argument"))

    def test_lyric_repeats_from_stdin(self):
        # the worker computes lyric_repeats from the words on stdin; no/too few words never fail the rest
        wav = os.path.join(self.dir, "chords.wav")
        words, end = _song([CHORUS, VERSES[0], CHORUS, VERSES[1], CHORUS], gap=1.0)  # 27.8 s
        self.assertLess(end, 30.0)
        words = [list(w) for w in words]
        v = run_worker(wav, words=json.dumps({"words": words}).encode())
        self.assertIsNotNone(server.check_rhythm(v))
        ly = v["lyric_repeats"]
        self.assertEqual((ly["status"], ly["unit"], [b["group"] for b in ly["blocks"]]), ("done", "char", [0, 0, 0]))
        self.assertEqual(ly, rw.lyric_repeats([tuple(w) for w in words], v["duration"])[0])
        for payload, want in ((b'{"words":[]}', {"status": "unavailable", "reason": "no_words"}),
                              (b'{"words":null,"reason":"result_missing"}',
                               {"status": "unavailable", "reason": "result_missing"}),
                              (b"not json", {"status": "unavailable", "reason": "words_invalid"}),
                              (b'{"words":' + b"[" * 5000 + b"]" * 5000 + b"}",  # review F2: too deep
                               {"status": "unavailable", "reason": "words_invalid"}),
                              (b'{"words":[["a",1' + b"0" * 400 + b',2]]}',  # review F2: overflows a float
                               {"status": "unavailable", "reason": "words_invalid"})):
            v = run_worker(wav, words=payload)
            self.assertEqual((v["lyric_repeats"], v["melody"]["status"]), (want, "done"))
            self.assertIsNotNone(server.check_rhythm(v))

    def test_end_to_end_through_server(self):
        env = Env()
        try:
            h = Harness(env, rhythm={"python": ESSENTIA_PY, "worker": WORKER})
            try:
                with open(os.path.join(self.dir, "chords.wav"), "rb") as f:
                    body = f.read()
                st, b = Client(env, h.port).json("POST", AR, body, timeout=120)
                self.assertEqual(st, 200, b)
                self.assertEqual(b["rhythm"]["status"], "done", b["rhythm"])
                self.assertIn(b["rhythm"]["key"]["tonic"], ("C", "A"))
                self.assertEqual(b["rhythm"]["tempo_cnn"]["status"], "done" if MODEL_PRESENT else "unavailable")
                self.assertEqual([s["label"] for s in b["rhythm"]["sections"]], ["A"])
                self.assertEqual(b["rhythm"]["energy_summary"]["unit"], "dBFS")
                self.assertEqual(b["rhythm"]["melody"]["status"], "done")
                self.assertNotIn("melody_notes", b["rhythm"])
                # the fake ml stores no result file: no words, rhythm done anyway
                self.assertEqual(b["rhythm"]["lyric_repeats"], {"status": "unavailable", "reason": "result_missing"})
                self.assertNotIn("structure_debug", b["rhythm"])
                rid = b["result_id"]
                with open(os.path.join(env.root, "results-rhythm", rid + ".json")) as f:
                    stored = json.load(f)
                self.assertFalse(any(p["selected"] for p in stored["structure_debug"]["peaks"]))  # one section
                self.assertTrue(all(p["value"] < 0.15 for p in stored["structure_debug"]["peaks"]))
                self.assertEqual(len(stored["words_sha256"]), 64)
                st, t = Client(env, h.port).json("GET", "/timeline/%s?layer=energy&limit=200" % rid)
                self.assertEqual((st, t["total"], len(t["energy"])), (200, 30, 30))
                st, t = Client(env, h.port).json("GET", "/timeline/%s?layer=melody&limit=200" % rid)
                self.assertEqual((st, t["layer_status"], t["total"]), (200, "done", b["rhythm"]["melody"]["note_count"]))
                st, t = Client(env, h.port).json("GET", "/timeline/%s?layer=genre" % rid)
                self.assertEqual((st, t["layer_status"], t["genre"]), (200, stored["genre"]["status"],
                                                                      stored["genre"].get("windows", [])))
                self.assertNotIn("windows", b["rhythm"]["genre"])
            finally:
                h.stop()
        finally:
            env.cleanup()


if __name__ == "__main__":
    unittest.main()
