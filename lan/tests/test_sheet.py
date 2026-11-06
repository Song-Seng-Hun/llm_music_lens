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

    def test_drums_failed(self):
        v = self.v(drums={"status": "failed", "code": "x"})
        self.assertEqual(sheet.build(v)["sections"][0]["drums"], {"status": "failed"})

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


if __name__ == "__main__":
    unittest.main()
