"""LLM sheet (sheet.py): build/render from synthetic results, and the GET /sheet/<id> route on a stored result."""
import json
import os
import sys
import unittest

from helpers import RID64
from test_rhythm import RHYTHM_OUT_V40, RhythmBase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sheet  # noqa: E402


def base(**kw):
    return json.loads(json.dumps(dict(RHYTHM_OUT_V40, **kw)))


def bar(s, e, chord, kick=(), snare=()):
    return {"start": s, "end": e, "chord": chord, "kick": None if kick is None else list(kick),
            "snare": None if snare is None else list(snare)}


class MelodySourceTest(unittest.TestCase):
    def test_long_method_cut_at_a_word(self):
        m = "Demucs htdemucs_ft vocal at 44.1 kHz, then RMVPE threshold 0.03 on CPU in 30 s pieces; sampled every 10 ms. Next."
        s = sheet.melody_source({"method": m})
        self.assertTrue(s.endswith(" ..."), s)
        self.assertIn(s[len("melody estimate: "):-4].split()[-1], [w.rstrip(",;:") for w in m.split()])  # last word whole
        self.assertLessEqual(len(s), len("melody estimate: ") + 95)
        self.assertEqual(sheet.melody_source({"method": "Short one. Rest"}), "melody estimate: Short one")
        self.assertEqual(sheet.melody_source({}), "unknown method")


class OldWorkerTest(unittest.TestCase):
    """A result without bars, parts or drums (worker 0.4.x shape)."""

    def setUp(self):
        self.sh = sheet.build(base(), RID64)

    def test_sections_and_timed_chords(self):
        secs = self.sh["sections"]
        self.assertEqual([s["id"] for s in secs], ["S01", "S02", "S03"])
        self.assertEqual(secs[0]["chords"], {"status": "detected", "changes": [[0.0, "C"]]})
        self.assertNotIn("bars", secs[0])

    def test_unmeasured_layers_are_unavailable(self):
        for s in self.sh["sections"]:
            self.assertEqual((s["parts"], s["drums"]), ({"status": "unavailable"}, {"status": "unavailable"}))
        self.assertEqual(self.sh["song"]["layers"]["drums"], {"status": "absent", "reliability": "unknown"})

    def test_render(self):
        t = sheet.render(json.loads(json.dumps(self.sh)))
        self.assertIn("S01 label A | 0:00.00-0:02.00 | bars unavailable", t)
        self.assertIn("chords (no bar grid) 0:02.00 Am", t)
        self.assertIn("drums  unavailable", t)
        self.assertNotIn("none", t.split("drill down")[0].replace("unknown", ""))


class TempoTest(unittest.TestCase):
    def v(self, bpm, bar_len, n=4, rows=6):
        bars = {"status": "done", "reliability": "low_reference", "beats_per_bar": n, "steps": 4 * n,
                "rows": [bar(i * bar_len, (i + 1) * bar_len, "C") for i in range(rows)]}
        return base(bpm=bpm, bars=bars)

    def test_2x_split_shows_bar_tempo(self):
        sh = sheet.build(self.v(142.93, 3.32), RID64)
        t = sh["song"]["tempo"]
        self.assertEqual((t["bpm"], t["source"], t["essentia_bpm"]), (72.29, "bars", 142.93))
        self.assertIn("tempo   72.3 BPM from bars (Essentia 142.9 BPM, confidence 3.9 of ~5.3, uncalibrated, is the other level); feel", sheet.render(sh))

    def test_half_split_and_three_beats(self):
        t = sheet.build(self.v(60.0, 1.5, n=3), RID64)["song"]["tempo"]   # bars 120
        self.assertEqual((t["bpm"], t["source"]), (120.0, "bars"))

    def test_agreeing_or_not_2x_keeps_essentia(self):
        for bpm, bl in ((120.0, 2.0), (90.0, 2.0)):   # bars 120: ratio 1.0, then 0.75
            sh = sheet.build(self.v(bpm, bl), RID64)
            self.assertEqual((sh["song"]["tempo"]["bpm"], sh["song"]["tempo"]["source"]), (bpm, "essentia"))
            self.assertNotIn("from bars", sheet.render(sh))

    def test_no_meter_or_one_bar_keeps_essentia(self):
        v = self.v(142.93, 3.32); del v["bars"]["beats_per_bar"]
        self.assertEqual(sheet.build(v, RID64)["song"]["tempo"]["source"], "essentia")
        t = sheet.build(self.v(142.93, 3.32, rows=1), RID64)["song"]["tempo"]
        self.assertEqual((t["source"], t["bar_bpm"]), ("essentia", None))
        self.assertEqual(sheet.build(base(), RID64)["song"]["tempo"]["bpm"], 120.0)


class BarsTest(unittest.TestCase):
    def v(self, **kw):
        bars = [bar(0.0, 1.0, "C", [0], [4]), bar(1.0, 2.0, "G", [0], [4]),          # S01
                bar(2.0, 3.0, "Am", [], []), bar(3.0, 4.0, "F", [0, 8], [4, 12]),     # S02
                bar(4.0, 5.0, "C", None, None)]                                       # S03, 5-6 s uncovered
        d = dict(rows=bars, status="done", reliability="low_reference")
        return base(**dict({"bars": d, "drums": {"status": "done", "reliability": "reference_candidate"}}, **kw))

    def test_per_bar_same_as_and_outside_bars(self):
        v = self.v(chords=[{"start": 0, "end": 1, "label": "C"}, {"start": 1, "end": 2, "label": "G"},
                           {"start": 2, "end": 5, "label": "Am"}, {"start": 5, "end": 6, "label": "N"}],
                   sections=[dict(RHYTHM_OUT_V40["sections"][0]), dict(RHYTHM_OUT_V40["sections"][0], start=2.0, end=4.0),
                             dict(RHYTHM_OUT_V40["sections"][2])])
        v["bars"]["rows"][2:4] = [bar(2.0, 3.0, "C", [0], [4]), bar(3.0, 4.0, "G", [0], [4])]
        s = sheet.build(v)["sections"]
        self.assertEqual(s[0]["chords"], {"status": "detected", "per_bar": ["C", "G"]})
        self.assertEqual(s[1]["chords"]["same_as"], "S01")
        self.assertEqual(s[2]["uncovered"], [[5.0, 6.0]])
        self.assertEqual(s[2]["chords"]["outside_bars"], [{"span": [5.0, 6.0], "changes": [[5.0, "N.C."]]}])
        self.assertIn("chords outside bars 0:05.00-0:06.00: 0:05.00 N.C.", sheet.render(sheet.build(v)))

    def test_bar_chord_uses_display(self):
        v = self.v(chords=[{"start": 0, "end": 0.25, "label": "C", "display": "C"},          # bar 1: C/E held longer
                           {"start": 0.25, "end": 1, "label": "C", "display": "C/E"},
                           {"start": 1, "end": 2, "label": "G", "display": "G"},
                           {"start": 2, "end": 3, "label": "A#", "display": "Bb"},          # key spelling
                           {"start": 3, "end": 4, "label": "F", "display": "F/A"},
                           {"start": 4, "end": 5, "label": "C"}])                           # older row: no display
        v["bars"]["rows"][2]["chord"] = "A#"
        s = sheet.build(v)["sections"]
        self.assertEqual(s[0]["chords"]["per_bar"], ["C/E", "G"])
        self.assertEqual(s[1]["chords"]["per_bar"], ["Bb", "F/A"])
        self.assertEqual(s[2]["chords"]["per_bar"], ["C"])
        self.assertIn("chords | C/E | G |", sheet.render(sheet.build(v)))

    def test_bar_chord_tie_and_no_display(self):
        rows = [{"start": 0, "end": 0.5, "label": "C", "display": "C/G"}, {"start": 0.5, "end": 1, "label": "C", "display": "C"}]
        self.assertEqual(sheet.bar_chord(rows, bar(0.0, 1.0, "C")), "C/G")    # tie: first seen
        self.assertEqual(sheet.bar_chord([{"start": 0, "end": 1, "label": "C"}], bar(0.0, 1.0, "C")), "C")
        self.assertIsNone(sheet.bar_chord(rows, bar(0.0, 1.0, None)))

    def test_drums_detected_empty_unmeasured(self):
        sh = sheet.build(self.v())
        s = sh["sections"]
        self.assertEqual((s[0]["drums"]["status"], s[0]["drums"]["groove"], s[0]["drums"]["groove_bars"]), ("detected", "G1", 2))
        self.assertEqual((s[1]["drums"]["empty_bars"], s[1]["drums"]["groove"]), (1, "G2"))
        self.assertEqual(s[2]["drums"], {"status": "unavailable", "bars": 1})  # kick/snare None = not measured
        self.assertEqual(sh["song"]["grooves"]["G1"], {"kick": [0], "snare": [4]})

    def test_drums_all_measured_empty(self):
        v = self.v()
        v["bars"]["rows"][0:2] = [bar(0.0, 1.0, "C"), bar(1.0, 2.0, "G")]
        self.assertEqual(sheet.build(v)["sections"][0]["drums"]["status"], "empty")

    def test_drums_skipped(self):
        v = self.v(drums={"status": "skipped", "reason": "adtof_failed"})
        self.assertEqual(sheet.build(v)["sections"][0]["drums"], {"status": "unavailable"})

    def test_straddling(self):
        v = self.v()
        v["bars"]["rows"][1] = bar(1.0, 2.5, "G", [0], [4])  # midpoint 1.75 in S01, 0.5 s outside
        self.assertEqual(sheet.build(v)["sections"][0]["straddling_bars"], [2])


class SectionTest(unittest.TestCase):
    def test_short_section_kept(self):
        secs = [dict(RHYTHM_OUT_V40["sections"][0], end=1.71), dict(RHYTHM_OUT_V40["sections"][1], start=1.71, end=2.0, label="C"),
                dict(RHYTHM_OUT_V40["sections"][1]), dict(RHYTHM_OUT_V40["sections"][2])]
        s = sheet.build(base(sections=secs))["sections"]
        self.assertEqual([(x["id"], x["label"], x["start"], x["end"]) for x in s][1], ("S02", "C", 1.71, 2.0))

    def test_function_shown_with_label(self):
        secs = [dict(x, function=f) for x, f in zip(RHYTHM_OUT_V40["sections"], ["intro", "chorus", None])]
        t = sheet.render(sheet.build(base(sections=secs)))
        self.assertIn("S02 label B (role chorus) |", t)
        self.assertIn("S03 label A |", t)

    def test_key_changes_inside_section(self):
        v = base(sections=[{"start": 0.0, "end": 6.0, "label": "A", "energy_mean": -20.0}])
        s = sheet.build(v)["sections"][0]
        self.assertEqual((s["key_estimate"], s["key_changes"]), ("C", [[0.0, "C"], [4.0, "D"]]))

    def test_melody_failed_vs_skipped(self):
        self.assertEqual(sheet.build(base(melody={"status": "failed"}))["sections"][0]["melody"], {"status": "failed"})
        self.assertEqual(sheet.build(base(melody={"status": "skipped"}))["sections"][0]["melody"], {"status": "unavailable"})

    def test_parts_compare_only_adjacent_measured(self):
        sec = RHYTHM_OUT_V40["sections"]
        rows = [dict(start=sec[0]["start"], end=sec[0]["end"], vocals=0.9, bass=0.1, drums=0.0),
                dict(start=sec[2]["start"], end=sec[2]["end"], vocals=0.1, bass=0.8, drums=0.0)]
        s = sheet.build(base(parts={"status": "done", "sections": rows}))["sections"]
        self.assertEqual(s[1]["parts"], {"status": "unavailable"})
        self.assertNotIn("crossed_50_up", s[2]["parts"])  # S02 had no row, so no comparison across the gap


def zbar(s, e, chord, kick=(0, 8), snare=(4, 12), split=None):
    """A bar row in the stored shape (worker 0.19+)."""
    return {"start": s, "end": e, "chord": chord, "split": split, "scale": "C", "degree": "1",
            "kick": None if kick is None else list(kick), "snare": None if snare is None else list(snare)}


def zn(s, e, m, name):
    return {"start": s, "end": e, "midi": m, "note": name}


class ZoomTest(unittest.TestCase):
    def v(self, **kw):
        rows = [zbar(0.0, 2.0, "C"), zbar(2.0, 4.0, "G", split=["G", "Am"]), zbar(5.0, 6.0, "F", None, None)]  # 4-5 s: gap
        return base(**dict({"bars": {"status": "done", "reliability": "low_reference", "rows": rows},
                            "melody_notes": [zn(0.0, 0.5, 60, "C4"), zn(0.5, 1.0, 62, "D4"), zn(2.25, 2.5, 67, "G4"), zn(4.2, 4.6, 64, "E4")],
                            "bass_notes": [zn(0.0, 1.0, 36, "C2")],
                            "other_notes": [zn(0.0, 1.0, 48, "C3"), zn(0.5, 1.0, 55, "G3")],
                            "drum_hits": [{"time": 0.0, "class": "kick"}, {"time": 0.5, "class": "hihat"},
                                          {"time": 5.5, "class": "kick"}, {"time": 4.5, "class": "tom"}]}, **kw))

    def test_bars_steps_and_layers(self):
        z = json.loads(json.dumps(sheet.zoom(self.v(), 0.0, 6.0, RID64)))
        b = z["bars"]
        self.assertEqual([x["n"] for x in b], [1, 2, 3])
        self.assertEqual(b[0]["melody"], [["C4", 0, 4], ["D4", 4, 4]])   # 0.5 s of a 2 s bar = 4 steps
        self.assertEqual(b[1]["melody"], [["G4", 2, 2]])
        self.assertEqual((b[0]["bass"], b[0]["other"]), ([["C2", 0, 8]], {"count": 2, "low": "C3", "high": "G3"}))
        self.assertEqual(b[0]["hits"], {"hihat": 1})           # kick and snare come from the bar row
        self.assertEqual(b[2]["hits"], {"kick": 1})            # bar without kick/snare data counts every class
        self.assertEqual(b[0]["section"], "S01")
        o = z["outside_bars"]
        self.assertEqual((o["spans"], o["melody"], o["hits"]), ([[4.0, 5.0]], [["E4", 4.2, 0.4]], {"tom": 1}))
        t = sheet.render_zoom(z)
        self.assertIn("bar 2 | 0:02.00-0:04.00 | S02 | G>Am (scale C, degree 1)", t)
        self.assertIn("  drums  kick x.......x....... snare ....o.......o...; other hits hihat 1", t)
        self.assertIn("  melody C4@0+4 D4@4+4", t)
        self.assertIn("  drums  kick/snare not measured; hits kick 1", t)
        self.assertIn("outside the bar grid, gaps 0:04.00-0:05.00 (onset", t)
        self.assertIn("  melody E4 0:04.20+0.40", t)

    def test_split_halves_use_display(self):
        v = self.v(chords=[{"start": 0, "end": 2, "label": "C", "display": "C"},
                           {"start": 2, "end": 3, "label": "G", "display": "G/B"},
                           {"start": 3, "end": 4, "label": "Am", "display": "Am"}])
        z = sheet.zoom(v, 0.0, 6.0, RID64)
        self.assertEqual(z["bars"][1]["chord"], ["G/B", "Am"])
        self.assertIn("bar 2 | 0:02.00-0:04.00 | S02 | G/B>Am", sheet.render_zoom(z))

    def test_three_beat_grid(self):
        v = self.v()
        v["bars"].update(beats_per_bar=3, steps=12)
        v["bars"]["rows"][0].update(kick=[0, 6], snare=[4, 8])
        z = sheet.zoom(v, 0.0, 6.0, RID64)
        self.assertEqual(z["bars"][0]["melody"], [["C4", 0, 3], ["D4", 3, 3]])   # 0.5 s of a 2 s bar = 3 of 12 steps
        t = sheet.render_zoom(z)
        self.assertIn("  drums  kick x.....x..... snare ....o...o...;", t)
        self.assertIn("12 steps per bar", t)
        v["drums"] = {"status": "done", "reliability": "reference_candidate"}
        sh = sheet.build(v, RID64)
        self.assertEqual(sh["song"]["meter"], {"beats_per_bar": 3, "steps": 12})
        r = sheet.render(sh)
        self.assertIn("beats per bar 3", r)
        self.assertIn("kick x.....x..... snare ....o...o...", r)

    def test_meter_unknown_before_0_22(self):
        sh = sheet.build(self.v(), RID64)
        self.assertEqual(sh["song"]["meter"], {"beats_per_bar": None, "steps": 16})
        self.assertIn("beats per bar unknown", sheet.render(sh))

    def test_span_edges(self):
        z = sheet.zoom(self.v(), 0.4, 2.3)
        self.assertEqual([(x["n"], x["partial"]) for x in z["bars"]], [(1, True), (2, True)])
        self.assertEqual(z["bars"][0]["melody"], [["D4", 4, 4]])   # C4 starts before the span
        self.assertEqual(z["bars"][1]["melody"], [["G4", 2, 2]])

    def test_old_worker_rows_by_time(self):
        z = json.loads(json.dumps(sheet.zoom(base(), 0.0, 6.0)))  # worker 0.4.x: no bars, bass, other or drums
        self.assertIsNone(z["bars"])
        self.assertEqual((z["layers"]["bass"], z["layers"]["hits"], z["layers"]["melody"]), ("unavailable", "unavailable", "detected"))
        self.assertEqual(z["outside_bars"]["spans"], [[0.0, 6.0]])
        self.assertNotIn("bass", z["outside_bars"])
        t = sheet.render_zoom(z)
        self.assertIn("bars    unavailable in this result", t)
        self.assertIn("rows by time (onset time+duration s):", t)
        self.assertIn("  chords 0:00.00 C", t)

    def test_failed_melody_not_empty(self):
        z = sheet.zoom(self.v(melody={"status": "failed", "code": "analysis_error"}, melody_notes=[]), 0.0, 6.0)
        self.assertEqual(z["layers"]["melody"], "failed")
        self.assertNotIn("melody", z["bars"][0])

    def test_skipped_layers_unavailable(self):
        z = sheet.zoom(self.v(bass={"status": "skipped", "reason": "x"}, bass_notes=[],
                              drums={"status": "skipped", "reason": "x"}, drum_hits=[]), 0.0, 6.0)
        self.assertEqual((z["layers"]["bass"], z["layers"]["hits"], z["layers"]["other"]), ("unavailable", "unavailable", "detected"))
        self.assertNotIn("bass", z["bars"][0])
        self.assertNotIn("hits", z["bars"][0])

    def test_last_half_step_goes_to_next_bar(self):
        v = self.v(melody_notes=[zn(1.97, 2.2, 60, "C4"), zn(3.97, 4.1, 62, "D4")])  # bar 2 ends at a gap
        z = sheet.zoom(v, 0.0, 6.0)
        self.assertEqual((z["bars"][0]["melody"], z["bars"][1]["melody"]), ([], [["C4", 0, 2]]))
        self.assertEqual(z["outside_bars"]["melody"], [["D4", 3.97, 0.13]])

    def test_bars_without_chords_get_timed_chords(self):
        v = self.v()
        for r in v["bars"]["rows"]:
            r["chord"] = None
        z = sheet.zoom(v, 0.0, 6.0)
        self.assertEqual(z["bars"][0]["chords_timed"], [[0.0, "C"]])
        self.assertIn("| S01 | chords (timed, no bar chords) 0:00.00 C", sheet.render_zoom(z))

    def test_short_gap_chords_listed(self):
        v = self.v()
        v["bars"]["rows"][2]["start"] = 4.5  # a 0.5 s gap, under GAP
        z = sheet.zoom(v, 0.0, 6.0)
        self.assertEqual(z["outside_bars"]["spans"], [])
        self.assertEqual(z["outside_bars"]["chords"], [[4.0, "F"]])

    def test_sheet_points_at_zoom(self):
        self.assertIn("zoom: GET /sheet/%s?start=<s>&end=<s>" % RID64, sheet.render(sheet.build(base(), RID64)))


class SheetRouteTest(RhythmBase):
    def store(self, raw=None):
        os.makedirs(os.path.dirname(self.rhythm_file()), 0o700, exist_ok=True)
        with open(self.rhythm_file(), "w") as f:
            f.write(raw if raw is not None else json.dumps(dict(RHYTHM_OUT_V40, **self.ids())))

    def test_text_and_json(self):
        self.store()
        st, b = self.c.json("GET", "/sheet/" + RID64)
        self.assertEqual((st, b["result_id"], b["format"]), (200, RID64, "music-lens-sheet/1"))
        self.assertTrue(b["text"].startswith("MUSIC LENS SHEET"))
        st, b = self.c.json("GET", "/sheet/%s?format=json" % RID64)
        self.assertEqual((st, [s["id"] for s in b["sheet"]["sections"]]), (200, ["S01", "S02", "S03"]))

    def test_errors(self):
        self.assertEqual(self.c.json("GET", "/sheet/" + RID64)[1]["error"]["code"], "rhythm_not_found")
        self.store()
        st, b = self.c.json("GET", "/sheet/%s?format=xml" % RID64)
        self.assertEqual((st, b["error"]["code"]), (400, "bad_request"))
        self.store("{")
        st, b = self.c.json("GET", "/sheet/" + RID64)
        self.assertEqual((st, b["error"]["code"]), (500, "invalid_result"))

    def test_zoom(self):
        self.store()
        st, b = self.c.json("GET", "/sheet/%s?start=0&end=3.5" % RID64)
        self.assertEqual((st, b["format"]), (200, "music-lens-sheet-zoom/1"))
        self.assertTrue(b["text"].startswith("MUSIC LENS SHEET ZOOM"))
        st, b = self.c.json("GET", "/sheet/%s?start=1&end=2&format=json" % RID64)
        self.assertEqual((st, b["zoom"]["span"]), (200, [1.0, 2.0]))

    def test_zoom_bad_spans(self):
        self.store()
        for q in ("start=0", "end=3", "start=0&end=30.5", "start=2&end=2", "start=3&end=2", "start=-0&end=1",
                  "start=6&end=7", "start=1e1&end=12", "start=0&end=1&start=0"):
            st, b = self.c.json("GET", "/sheet/%s?%s" % (RID64, q))
            self.assertEqual((st, b["error"]["code"]), (400, "bad_request"), q)
            self.assertEqual("at most 30 s" in b["error"].get("detail", ""), q in ("start=0&end=30.5", "start=2&end=2", "start=3&end=2"), q)
        self.assertEqual(self.c.json("GET", "/sheet/%s?start=0&end=30" % RID64)[0], 200)
        self.assertEqual(self.c.json("GET", "/sheet/%s?start=2.2&end=32.2" % RID64)[0], 200)  # 30 s despite float error


if __name__ == "__main__":
    unittest.main()
