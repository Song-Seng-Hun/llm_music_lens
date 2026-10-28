"""Section overview (rhythm summary `overview`): computed at read time from a stored 0.4.3 rhythm file plus the ASR
words of the same result; synthetic fixtures only (no audio, no Essentia)."""
import copy
import json
import os
import unittest

from helpers import RID64, server
from test_rhythm import AR, RHYTHM_OUT_V40, RHYTHM_OUT_V43, WAV, RhythmBase, rw

LOUD, VERSE, QUIET, SIL = -14.0, -20.0, -26.0, -120.0


def _words(t0, t1, step=0.3):
    """Char-level ASR words [text, start, end] every `step` s in [t0, t1)."""
    out, t = [], t0
    while t < t1 - 1e-9:
        out.append(["あ", round(t, 3), round(t + 0.25, 3)])
        t += step
    return out


def _notes(t0, t1, midi, length=0.5):
    out, t = [], t0
    while t < t1 - 1e-9:
        out.append({"start": round(t, 3), "end": round(min(t + length - 0.05, t1), 3), "midi": midi,
                    "note": rw.note_name(midi)})
        t += length
    return out


def _chords(t0, t1, labels, length=2.0):
    out, t, k = [], t0, 0
    while t < t1 - 1e-9:
        out.append({"start": round(t, 3), "end": round(min(t + length, t1), 3), "label": labels[k % len(labels)],
                    "strength": 0.8})
        t += length
        k += 1
    return out


def _sections(bounds, dur, values):
    """Contiguous sections on `bounds`; silent ones labelled silence, the others A, B, A, B, ..."""
    out, edges, k = [], [0.0] + list(bounds) + [dur], 0
    for a, b in zip(edges, edges[1:]):
        sel = [x for i, x in enumerate(values) if a <= i + 0.5 < b] or [values[0]]
        silent = rw.section_silent(values, a, b)
        out.append({"start": a, "end": b, "label": "silence" if silent else "AB"[k % 2],
                    "energy_mean": round(sum(sel) / len(sel), 2), "top_chord": None,
                    "similarity_to_first": None if silent else 1.0})
        k += 0 if silent else 1
    return out


def stored(dur, values, blocks, notes=None, chords=None, bounds=(), lyric=None):
    """A valid stored 0.4.3 rhythm dict (check_rhythm passes) from energy values, lyric blocks and melody notes."""
    v = copy.deepcopy(RHYTHM_OUT_V43)
    secs = _sections(bounds, dur, values)
    v.update(duration=dur, beats=[round(0.5 * i, 3) for i in range(1, int(dur * 2))],
             chords=chords if chords is not None else _chords(0.0, dur, ["C", "Am"]),
             energy={"unit": "dBFS", "step_s": 1.0, "values": values},
             energy_summary=rw.energy_summary(values, dur), sections=secs, structure_debug=None)
    if notes is None:
        v.update(melody={"status": "failed", "code": "analysis_error"}, melody_notes=[])
    else:
        voiced = [round(i * 0.008, 6) for i in range(int(dur / 0.008))
                  if any(n["start"] <= i * 0.008 < n["end"] for n in notes)]
        v.update(melody=rw.melody_summary(notes, voiced, 0.008, int(dur / 0.008), "C", "major", secs),
                 melody_notes=notes)
    if lyric is None:
        bl = [{"start": a, "end": b, "group": g} for a, b, g in blocks]
        lyric = dict(RHYTHM_OUT_V43["lyric_repeats"], blocks=bl, group_count=len({g for _, _, g in blocks}),
                     coverage_s=round(sum(b - a for a, b, _ in blocks), 3))
    v["lyric_repeats"] = lyric
    v = json.loads(json.dumps(v))
    assert server.check_rhythm(v) is v, "fixture must be a valid stored file"
    return v


def backbone():
    """120 s: silence 0-6, instrumental 6-20, chorus 20-35, verse 35-60, chorus 60-75, interlude 75-87, verse 87-100,
    hook 100-112, outro 112-120 (no words). Choruses loud and high, verses medium."""
    vals = [SIL] * 6 + [QUIET] * 14 + [LOUD] * 15 + [VERSE] * 25 + [LOUD] * 15 + [QUIET] * 12 + [VERSE] * 13 \
        + [LOUD - 1] * 12 + [QUIET] * 8
    words = _words(20, 35) + _words(35, 60) + _words(60, 75) + _words(87, 100) + _words(100, 112)
    notes = (_notes(6, 20, 60) + _notes(20, 35, 72) + _notes(35, 60, 64) + _notes(60, 75, 72) + _notes(75, 87, 60)
             + _notes(87, 100, 64) + _notes(100, 112, 69))
    chords = _chords(0, 20, ["C", "Am"]) + _chords(20, 35, ["F", "G", "C"]) + _chords(35, 60, ["Am", "Em"]) \
        + _chords(60, 75, ["F", "G", "C"]) + _chords(75, 100, ["Am", "Em"]) + _chords(100, 112, ["Dm", "G"]) \
        + _chords(112, 120, ["C"])
    v = stored(120.0, vals, [(20.0, 35.0, 0), (60.0, 75.0, 0), (100.0, 112.0, 1)], notes=notes, chords=chords)
    return v, words


def assert_shape(t, o, dur):
    """Independent strict check of a done overview: exact keys, contiguity, caps, ranks, groups."""
    t.assertEqual(set(o), {"status", "source", "method", "reliability", "segment_count", "repeat_groups",
                           "dropped_repeats", "segments", "notes"})
    t.assertEqual((o["status"], o["reliability"]), ("done", "low_reference"))
    segs = o["segments"]
    t.assertTrue(1 <= len(segs) <= 40)
    t.assertEqual(o["segment_count"], len(segs))
    t.assertEqual(segs[0]["start"], 0.0)
    t.assertAlmostEqual(segs[-1]["end"], dur, places=3)
    for a, b in zip(segs, segs[1:]):
        t.assertEqual(a["end"], b["start"])
    for s in segs:
        t.assertEqual(set(s), {"start", "end", "kind", "group", "label", "energy", "quiet", "chords", "melody",
                               "words"})
        t.assertIs(s["quiet"], s["kind"] == "repeat" and s["energy"]["rel_db"] is not None
                   and s["energy"]["rel_db"] <= -12)
        t.assertLess(s["start"], s["end"])
        t.assertEqual(set(s["energy"]), {"mean_db", "rel_db", "rank"})
        t.assertEqual(set(s["chords"]), {"top", "changes_per_min"})
        t.assertLessEqual(len(s["chords"]["top"]), 3)
        t.assertEqual(s["group"] is not None, s["kind"] == "repeat")
        if s["melody"] is not None:
            t.assertEqual(set(s["melody"]), {"note_count", "low", "high", "median_midi", "rel_semitones"})
        if s["words"] is not None:
            t.assertEqual(set(s["words"]), {"count"})
        if o["source"] == "lyric":
            t.assertIn(s["kind"], ("repeat", "vocal", "instrumental", "silence", "gap"))
            t.assertIsNone(s["label"])
            if s["kind"] != "repeat":
                t.assertGreaterEqual(s["end"] - s["start"], 4.0 - 1e-6, s)  # tiny pieces were merged
        else:
            t.assertIn(s["kind"], ("section", "silence"))
    ranks = sorted(s["energy"]["rank"] for s in segs if s["energy"]["rank"] is not None)
    t.assertEqual(ranks, list(range(1, len(ranks) + 1)))
    t.assertTrue(server.overview_ok(o, dur))


def seq(o):
    return [(s["kind"], s["group"], s["start"], s["end"]) for s in o["segments"]]


class OverviewLogicTest(unittest.TestCase):
    def test_lyric_backbone(self):
        v, words = backbone()
        before = copy.deepcopy((v, words))
        o = server.rhythm_overview(v, words)
        assert_shape(self, o, 120.0)
        self.assertEqual(o["source"], "lyric")
        self.assertEqual(seq(o), [("silence", None, 0.0, 6.0), ("instrumental", None, 6.0, 20.0),
                                  ("repeat", 0, 20.0, 35.0), ("vocal", None, 35.0, 60.0), ("repeat", 0, 60.0, 75.0),
                                  ("instrumental", None, 75.0, 87.0), ("vocal", None, 87.0, 100.0),
                                  ("repeat", 1, 100.0, 112.0), ("instrumental", None, 112.0, 120.0)])
        self.assertEqual(o["repeat_groups"], [{"group": 0, "count": 2, "seconds": 30.0},
                                              {"group": 1, "count": 1, "seconds": 12.0}])
        s = o["segments"]
        # energy: song median of non-silent windows is -20 (verse); choruses +6 dB, ranked 1 and 2 (ties earliest)
        self.assertEqual(s[2]["energy"], {"mean_db": LOUD, "rel_db": 6.0, "rank": 1})
        self.assertEqual(s[4]["energy"], {"mean_db": LOUD, "rel_db": 6.0, "rank": 2})
        self.assertEqual(s[7]["energy"]["rank"], 3)
        self.assertEqual(s[0]["energy"], {"mean_db": SIL, "rel_db": -100.0, "rank": None})  # silence: no rank
        # chords: top by duration with share of the segment, changes per minute inside it
        self.assertEqual(s[2]["chords"]["top"], [{"label": "F", "share": 0.4}, {"label": "G", "share": 0.333},
                                                 {"label": "C", "share": 0.267}])
        self.assertEqual(s[2]["chords"]["changes_per_min"], round(7 * 60 / 15, 2))
        self.assertEqual(s[8]["chords"], {"top": [{"label": "C", "share": 1.0}], "changes_per_min": 0.0})
        # melody vs the song median (duration-weighted, the worker's rule)
        song = v["melody"]["median_midi"]
        self.assertEqual(s[2]["melody"], {"note_count": 30, "low": 72, "high": 72, "median_midi": 72,
                                          "rel_semitones": 72 - song})
        self.assertEqual(s[0]["melody"], {"note_count": 0, "low": None, "high": None, "median_midi": None,
                                          "rel_semitones": None})
        # words: counts only, onset in [start, end)
        self.assertEqual([x["words"]["count"] for x in s], [0, 0, 50, 84, 50, 0, 44, 40, 0])
        self.assertNotIn("あ", json.dumps(o, ensure_ascii=False))  # never any word text
        self.assertEqual(before, (v, words))  # pure: inputs untouched
        self.assertEqual(server.rhythm_overview(v, words), o)  # deterministic
        self.assertEqual(json.dumps(server.rhythm_overview(copy.deepcopy(v), copy.deepcopy(words))), json.dumps(o))

    def test_silent_and_quiet_repeats(self):
        # hallucinated repeat over a silent tail (90-100) next to a silent gap (85-90): silence, group null, counted
        # in dropped_repeats, joined with the neighbouring silence; a repeat 14 dB under the median is quiet
        vals = [VERSE] * 20 + [LOUD] * 10 + [VERSE] * 20 + [VERSE - 14] * 10 + [VERSE] * 25 + [SIL] * 15
        v = stored(100.0, vals, [(20.0, 30.0, 0), (50.0, 60.0, 0), (90.0, 100.0, 0)])
        o = server.rhythm_overview(v, _words(0, 85) + _words(90, 100))
        assert_shape(self, o, 100.0)
        self.assertEqual([(k, g, a, b) for k, g, a, b in seq(o)],
                         [("vocal", None, 0.0, 20.0), ("repeat", 0, 20.0, 30.0), ("vocal", None, 30.0, 50.0),
                          ("repeat", 0, 50.0, 60.0), ("vocal", None, 60.0, 85.0), ("silence", None, 85.0, 100.0)])
        self.assertEqual((o["dropped_repeats"], o["repeat_groups"]), (1, [{"group": 0, "count": 2, "seconds": 20.0}]))
        self.assertEqual([s["quiet"] for s in o["segments"]], [False, False, False, True, False, False])
        self.assertEqual(o["segments"][3]["energy"]["rel_db"], -14.0)
        self.assertEqual(o["segments"][5]["words"], {"count": 34})  # the dropped repeat's words stay counted
        # every repeat silent: no repeat left, still source lyric
        v = stored(30.0, [SIL] * 30, [(5.0, 25.0, 0)])
        o = server.rhythm_overview(v, _words(5, 25))
        assert_shape(self, o, 30.0)
        self.assertEqual((seq(o), o["dropped_repeats"], o["repeat_groups"], o["source"]),
                         ([("silence", None, 0.0, 30.0)], 1, [], "lyric"))

    def test_long_gap_split_at_sections(self):
        vals = [VERSE] * 150
        v = stored(150.0, vals, [(100.0, 110.0, 0)], bounds=(30.0, 55.0, 80.0, 102.0))
        o = server.rhythm_overview(v, _words(0, 150))
        assert_shape(self, o, 150.0)
        # 0-100 (100 s): nearest boundary to 50 is 55, then 30 in 0-55 and 80 in 55-100; 110-150 (40 s) stays;
        # 102 lies inside the repeat and is ignored
        self.assertEqual([(k, a, b) for k, _, a, b in seq(o)],
                         [("vocal", 0.0, 30.0), ("vocal", 30.0, 55.0), ("vocal", 55.0, 80.0), ("vocal", 80.0, 100.0),
                          ("repeat", 100.0, 110.0), ("vocal", 110.0, 150.0)])
        # no boundary at least 4 s inside: the long gap stays whole
        v = stored(150.0, vals, [(100.0, 110.0, 0)], bounds=(2.0, 98.0))
        self.assertEqual(seq(server.rhythm_overview(v, _words(0, 150)))[0], ("vocal", None, 0.0, 100.0))

    def test_tiny_pieces_merge(self):
        vals = [VERSE] * 80
        words = _words(0, 47) + _words(70, 80)
        # 3.5 s gap between two repeats joins the previous repeat; a 2 s vocal piece (45-47) before a wordless
        # 23 s stretch joins that longer non-repeat neighbour; a 2 s head before the first block joins the block
        v = stored(80.0, vals, [(2.0, 20.0, 0), (23.5, 45.0, 0)])
        o = server.rhythm_overview(v, words)
        assert_shape(self, o, 80.0)
        self.assertEqual(seq(o), [("repeat", 0, 0.0, 23.5), ("repeat", 0, 23.5, 45.0),
                                  ("instrumental", None, 45.0, 70.0), ("vocal", None, 70.0, 80.0)])
        self.assertEqual(o["repeat_groups"], [{"group": 0, "count": 2, "seconds": 45.0}])

    def test_silence_edges(self):
        vals = [VERSE] * 30 + [SIL] * 5 + [VERSE] * 25 + [SIL] * 3
        v = stored(63.0, vals, [(40.0, 50.0, 0)])
        o = server.rhythm_overview(v, _words(0, 63))
        assert_shape(self, o, 63.0)
        # a 5 s silent run cuts the gap; the 3 s silent tail is shorter than 4 s and stays in its piece
        self.assertEqual([(k, a, b) for k, _, a, b in seq(o)],
                         [("vocal", 0.0, 30.0), ("silence", 30.0, 35.0), ("vocal", 35.0, 40.0),
                          ("repeat", 40.0, 50.0), ("vocal", 50.0, 63.0)])
        self.assertIsNone(o["segments"][1]["energy"]["rank"])
        self.assertEqual(o["segments"][4]["energy"]["mean_db"], VERSE)  # silent windows left out of a loud piece

    def test_audio_fallback(self):
        vals = [SIL] * 10 + [QUIET] * 30 + [LOUD] * 30
        for lyric in ({"status": "unavailable", "reason": "too_little_text"},
                      {"status": "failed", "code": "analysis_error"},
                      dict(RHYTHM_OUT_V43["lyric_repeats"], blocks=[], group_count=0, coverage_s=0.0)):
            v = stored(70.0, vals, [], bounds=(10.0, 40.0), lyric=lyric)
            o = server.rhythm_overview(v, None)
            assert_shape(self, o, 70.0)
            self.assertEqual((o["source"], o["repeat_groups"], o["dropped_repeats"]), ("audio", [], 0))
            self.assertEqual([(s["kind"], s["label"], s["start"], s["end"]) for s in o["segments"]],
                             [("silence", "silence", 0.0, 10.0), ("section", "A", 10.0, 40.0),
                              ("section", "B", 40.0, 70.0)])
            self.assertEqual([s["energy"]["rank"] for s in o["segments"]], [None, 2, 1])
            self.assertEqual([s["words"] for s in o["segments"]], [None] * 3)
        # no repeats and no sections (no beats): failed no_structure
        v = stored(70.0, vals, [], lyric={"status": "unavailable", "reason": "no_words"})
        v = json.loads(json.dumps(dict(v, sections=[], beats=[])))
        self.assertIs(server.check_rhythm(v), v)
        self.assertEqual(server.rhythm_overview(v, None), {"status": "failed", "code": "no_structure"})

    def test_melody_missing_and_no_words(self):
        vals = [VERSE] * 60
        v = stored(60.0, vals, [(20.0, 30.0, 0)], notes=None)  # melody failed
        o = server.rhythm_overview(v, _words(0, 60))
        assert_shape(self, o, 60.0)
        self.assertEqual({s["melody"] is None for s in o["segments"]}, {True})
        # no words, melody present: vocal decided by melody notes, word counts null
        v = stored(60.0, vals, [(20.0, 30.0, 0)], notes=_notes(0, 10, 64) + _notes(20, 30, 67))
        o = server.rhythm_overview(v, None)
        assert_shape(self, o, 60.0)
        self.assertIn("melody notes", o["method"])
        self.assertEqual([(k, a, b) for k, _, a, b in seq(o)],
                         [("vocal", 0.0, 20.0), ("repeat", 20.0, 30.0), ("instrumental", 30.0, 60.0)])
        self.assertEqual({s["words"] is None for s in o["segments"]}, {True})
        self.assertEqual(o["segments"][1]["melody"]["rel_semitones"], 67 - v["melody"]["median_midi"])
        # neither words nor melody: non-silent gaps are kind gap
        v = stored(60.0, vals, [(20.0, 30.0, 0)])
        o = server.rhythm_overview(v, None)
        assert_shape(self, o, 60.0)
        self.assertEqual([s["kind"] for s in o["segments"]], ["gap", "repeat", "gap"])
        # an empty word list (ASR heard nothing) is not "no words": counts 0, everything wordless
        o = server.rhythm_overview(v, [])
        self.assertEqual([(s["kind"], s["words"]) for s in o["segments"]],
                         [("instrumental", {"count": 0}), ("repeat", {"count": 0}), ("instrumental", {"count": 0})])

    def test_segment_cap(self):
        # 40 repeats of 4 s with 5 s gaps = 81 pieces: the shortest non-repeat pieces join until 40 remain
        blocks = [(5.0 + 9 * k, 9.0 + 9 * k, k % 3) for k in range(40)]
        v = stored(370.0, [VERSE] * 370, blocks)
        o = server.rhythm_overview(v, _words(0, 370))
        assert_shape(self, o, 370.0)
        self.assertEqual(o["segment_count"], 40)
        self.assertEqual({s["kind"] for s in o["segments"]}, {"repeat"})
        self.assertEqual(sum(g["count"] for g in o["repeat_groups"]), 40)

    def test_old_files_null(self):
        for base in (RHYTHM_OUT_V40, {k: x for k, x in RHYTHM_OUT_V43.items() if k != "lyric_repeats"}):
            v = json.loads(json.dumps(base))
            self.assertIs(server.check_rhythm(v), v)
            self.assertIsNone(server.rhythm_overview(v, None))
            self.assertIsNone(server.rhythm_summary(v)["overview"])

    def test_edge_inputs_never_raise(self):
        v, words = backbone()
        # inputs that bypass check_rhythm: internal errors become failed, never an exception
        for bad in (dict(v, energy={"step_s": 1.0, "values": ["x"]}), dict(v, chords=None), dict(v, duration="x"),
                    dict(v, lyric_repeats=dict(v["lyric_repeats"], blocks=[{"start": 1}])), dict(v, melody_notes=5)):
            o = server.rhythm_overview(bad, words)
            self.assertEqual(o["status"], "failed", bad.keys())
            self.assertTrue(server.overview_ok(o, 120.0))
        self.assertEqual(server.rhythm_overview(v, [["a", "x", 1]])["status"], "failed")
        self.assertIsNone(server.rhythm_overview(None, None))
        # blocks past the duration (allowed by the +0.5 s tolerance) are clamped; zero-length words count
        v2 = stored(40.0, [VERSE] * 40, [(30.0, 40.3, 0)])
        o = server.rhythm_overview(v2, [["a", 5.0, 5.0], ["a", 39.9, 40.4], ["a", 41.0, 42.0]])
        assert_shape(self, o, 40.0)
        self.assertEqual(o["segments"][-1]["end"], 40.0)
        self.assertEqual([s["words"]["count"] for s in o["segments"]], [1, 1])
        # a short song with one block: everything merges into the repeat
        v3 = stored(6.0, [VERSE] * 6, [(0.5, 5.0, 0)])
        self.assertEqual(seq(server.rhythm_overview(v3, None)), [("repeat", 0, 0.0, 6.0)])

    def test_shape_check_rejects(self):
        v, words = backbone()
        o = server.rhythm_overview(v, words)
        self.assertTrue(server.overview_ok(o, 120.0))
        s0 = o["segments"][0]

        def segs(k, **over):
            return dict(o, segments=[dict(s, **over) if i == k else s for i, s in enumerate(o["segments"])])

        bad = [dict(o, extra=1), dict(o, source="x"), dict(o, reliability="high"), dict(o, segment_count=8),
               dict(o, segments=[]), dict(o, segments=o["segments"][1:]), dict(o, segments=o["segments"][:-1]),
               dict(o, segments=[o["segments"][0]] * 41), dict(o, repeat_groups=[]), dict(o, method=""),
               segs(0, end=5.0), segs(0, kind="section"), segs(0, group=0), segs(2, group=None), segs(2, label="A"),
               segs(0, energy=dict(s0["energy"], rank=1)), segs(2, energy=dict(o["segments"][2]["energy"], rank=None)),
               segs(2, energy=dict(o["segments"][2]["energy"], rank=2)), segs(0, extra=1),
               segs(2, chords={"top": [{"label": "C", "share": 0.2}, {"label": "F", "share": 0.4}],
                               "changes_per_min": 1.0}),
               segs(2, chords={"top": [{"label": "C", "share": 1.5}], "changes_per_min": 1.0}),
               segs(2, melody=dict(o["segments"][2]["melody"], low=80)), segs(0, melody=None),
               segs(2, quiet=True), segs(0, quiet=None), dict(o, dropped_repeats=-1), dict(o, dropped_repeats=True),
               segs(0, melody=dict(s0["melody"], low=60)), segs(1, words=None), segs(1, words={"count": -1}),
               {"status": "failed"}, {"status": "failed", "code": "Bad"}, {"status": "done"}, None]
        for b in bad:
            self.assertFalse(server.overview_ok(b, 120.0), b if not isinstance(b, dict) else
                             {k: x for k, x in b.items() if k != "segments"})


class OverviewServedTest(RhythmBase):
    """overview through the real output paths: analyze (fresh and cache hit) and /report."""

    def report(self):
        st, b = self.c.json("GET", "/report/" + RID64)
        self.assertEqual((st, b["x"]), (200, 1.5))
        return b["rhythm"]

    def test_analyze_and_report(self):
        self.fw.write(stdout=json.dumps(RHYTHM_OUT_V43))
        r = self.c.json("POST", AR, WAV)[1]["rhythm"]
        o = r["overview"]
        assert_shape(self, o, 6.0)
        self.assertEqual(seq(o), [("repeat", 0, 0.0, 6.0)])
        self.assertEqual(o["segments"][0]["words"], {"count": 5})  # the 5 words of the result file
        self.assertEqual(self.report()["overview"], o)
        r2 = self.c.json("POST", AR, WAV)[1]["rhythm"]
        self.assertEqual((r2["cache_hit"], r2["overview"]), (True, o))
        with open(self.rhythm_file()) as f:
            self.assertNotIn("overview", json.load(f))  # read time only, never stored

    def test_stored_fixture_words_and_failures(self):
        v, words = backbone()
        self.write_result(words=[{"text": t, "start": s, "end": e} for t, s, e in words])
        sha = self.h.app.lyric_words(RID64)[1]
        with open(self.rhythm_file(), "w") as f:
            json.dump(dict(v, words_sha256=sha), f)
        o = self.report()["overview"]
        assert_shape(self, o, 120.0)
        self.assertEqual(o, server.rhythm_overview(v, words))
        # words changed since the rhythm run (sha mismatch) or result file gone/broken: counts null, report intact
        changed = [{"text": t, "start": a, "end": b} for t, a, b in words[:3]]  # valid, but not the stored words
        for raw in (json.dumps({"result_id": RID64, "words": changed}), "{", None):
            if raw is None:
                os.unlink(os.path.join(self.env.root, "results", RID64 + ".json"))
            else:
                self.write_result(raw=raw)
            o = self.report()["overview"]
            self.assertEqual(o["status"], "done")
            self.assertEqual({s["words"] is None for s in o["segments"]}, {True})
            self.assertIn("melody notes", o["method"])
        # an internal error or a bad shape gives overview failed; the rest of the report is intact
        orig = server._Ov.build
        try:
            server._Ov.build = lambda self: 1 / 0
            r = self.report()
            self.assertEqual((r["status"], r["overview"]), ("done", {"status": "failed", "code": "internal_error"}))
            self.assertEqual(r["lyric_repeats"], v["lyric_repeats"])
            server._Ov.build = lambda self: {"status": "done"}
            self.assertEqual(self.report()["overview"], {"status": "failed", "code": "invalid_overview"})
        finally:
            server._Ov.build = orig
        orig = self.h.app.lyric_words
        try:
            self.h.app.lyric_words = lambda rid: 1 / 0
            self.assertEqual(self.report()["overview"], {"status": "failed", "code": "internal_error"})
        finally:
            self.h.app.lyric_words = orig
        # an old (0.4.2) stored file: overview null, no error
        with open(self.rhythm_file(), "w") as f:
            json.dump(RHYTHM_OUT_V40, f)
        r = self.report()
        self.assertEqual((r["status"], r["overview"]), ("done", None))


if __name__ == "__main__":
    unittest.main()
