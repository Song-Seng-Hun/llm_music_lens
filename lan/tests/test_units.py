"""Musical units (worker 0.14.0, 0.15.0, 0.16.0, 0.18.0, 0.22.0): downbeats, bars, scale and degree, swing, two chords in a bar,
beats per bar."""
import json
import unittest

from helpers import server
from test_rhythm import RHYTHM_OUT_V40, rw

DONE = {"status": "done"}
HITS = [{"time": 0.0, "class": "kick"}, {"time": 0.5, "class": "snare"}, {"time": 1.99, "class": "kick"},
        {"time": 2.5, "class": "hihat"}, {"time": 4.5, "class": "kick"}]
BEATS = [0.0, 2.0, 4.0]
CHORDS = [{"start": 0.0, "end": 1.5, "label": "Am"}, {"start": 1.5, "end": 4.0, "label": "F"}]


def stored(**fields):
    base = dict(RHYTHM_OUT_V40, downbeats={"status": "done", "method": rw.DOWNBEAT_METHOD, "reliability": "reference_candidate",
                                           "count": 3},
                bars=rw.bars_field(BEATS, CHORDS, DONE, HITS, True, 4.0))
    return json.loads(json.dumps(dict(base, **fields)))


class DownbeatTest(unittest.TestCase):
    def test_keeps_times_inside_the_song(self):
        self.assertEqual(rw.clean_downbeats([0.5, 1.0004, 9.0], 2.0), [0.5, 1.0])

    def test_unusable_times_give_none(self):
        for times in (None, [1.0], [1.0, 0.5], [0.5, 0.5], [0.5, float("nan")], "x", list(range(rw.BAR_MAX + 1))):
            self.assertEqual(rw.clean_downbeats(times, 1e6), [])


class BarTest(unittest.TestCase):
    def test_nearest_step_and_next_bar(self):
        self.assertEqual(rw.bar_steps([0.0, 0.5, 1.99], BEATS), {0: [0, 4], 1: [0]})

    def test_hits_outside_the_bars_are_dropped(self):
        self.assertEqual(rw.bar_steps([-1.0, 4.0, 5.0], BEATS), {})
        self.assertEqual(rw.bar_steps([3.99], BEATS), {})

    def test_rows(self):
        rows = rw.bars_field(BEATS, CHORDS, DONE, HITS, True, 4.0)["rows"]
        self.assertEqual(rows, [{"start": 0.0, "end": 2.0, "chord": "Am", "split": None, "scale": "C", "degree": "6", "kick": [0], "snare": [4]},
                                {"start": 2.0, "end": 4.0, "chord": "F", "split": None, "scale": "C", "degree": "4", "kick": [0], "snare": []}])

    def test_missing_layers_are_none(self):
        row = rw.bars_field(BEATS, CHORDS, {"status": "skipped", "reason": "x"}, [], False, 4.0)["rows"][0]
        self.assertEqual((row["chord"], row["split"], row["scale"], row["degree"], row["kick"], row["snare"]), (None,) * 6)

    def test_no_downbeats_no_field(self):
        self.assertIsNone(rw.bars_field([], CHORDS, DONE, HITS, True, 4.0))
        self.assertIsNone(rw.bars_field(None, CHORDS, DONE, HITS, True, 4.0))


def chords(*rows):
    return [{"start": a, "end": b, "label": label} for a, b, label in rows]


class SplitTest(unittest.TestCase):
    def test_two_roots_split(self):
        self.assertEqual(rw.bar_split(chords((0.0, 1.0, "Am"), (1.0, 2.0, "F")), 0.0, 2.0), ["Am", "F"])

    def test_the_share_is_inclusive(self):
        c = chords((0.0, 0.6, "Am"), (0.6, 1.0, "E"), (1.0, 1.6, "F"), (1.6, 2.0, "G"))
        self.assertEqual(rw.bar_split(c, 0.0, 2.0), ["Am", "F"])

    def test_a_half_with_no_clear_chord_stays_one(self):
        self.assertIsNone(rw.bar_split(chords((0.0, 1.0, "Am"), (1.0, 1.5, "F"), (1.5, 2.0, "G")), 0.0, 2.0))

    def test_one_label_held_in_pieces_counts_whole(self):
        c = chords((0.0, 1.0, "Am"), (1.0, 1.3, "F"), (1.3, 1.4, "G"), (1.4, 1.8, "F"), (1.8, 2.0, "C"))
        self.assertEqual(rw.bar_split(c, 0.0, 2.0), ["Am", "F"])

    def test_same_root_stays_one(self):
        self.assertIsNone(rw.bar_split(chords((0.0, 1.0, "C"), (1.0, 2.0, "Cmaj7")), 0.0, 2.0))
        self.assertIsNone(rw.bar_split(chords((0.0, 1.0, "C#"), (1.0, 2.0, "Db")), 0.0, 2.0))

    def test_no_chord_stays_one(self):
        self.assertIsNone(rw.bar_split(chords((0.0, 1.0, "N"), (1.0, 2.0, "F")), 0.0, 2.0))
        self.assertIsNone(rw.bar_split(chords((1.0, 2.0, "F")), 0.0, 2.0))

    def test_the_bar_chord_does_not_change(self):
        rows = rw.bars_field([0.0, 2.0], chords((0.0, 1.0, "Am"), (1.0, 2.0, "F")), DONE, [], True, 2.0)["rows"]
        self.assertEqual((rows[0]["chord"], rows[0]["split"]), ("Am", ["Am", "F"]))


class ScaleTest(unittest.TestCase):
    def test_chord_tones(self):
        for label, want in (("Am", [9, 0, 4]), ("F#maj7", [6, 10, 1, 5]), ("G7", [7, 11, 2, 5]), ("C:min7", [0, 3, 7, 10]),
                            ("Bb/D", [10, 2, 5]), ("Dsus4", [2, 7, 9]), ("Bhdim7", [11, 2, 5, 9]), ("Bm7b5", [11, 2, 5, 9]),
                            ("AmMaj7", [9, 0, 4, 8]), ("Fdim7", [5, 8, 11, 2]), ("Eaug", [4, 8, 0]), ("Cm6", [0, 3, 7]), ("C6", [0, 4, 7]),
                            ("Csus2", [0, 2, 7])):
            self.assertEqual(rw.chord_tones(label), want, label)
        for label in ("N", "X", "", None, "?"):
            self.assertIsNone(rw.chord_tones(label))

    def test_a_long_change_moves_and_a_short_visit_stays(self):
        c, d = ["C", "F", "Dm", "Am"] * 6, ["D", "A", "Bm", "Em"] * 6
        self.assertEqual(rw.scale_path(c + d), [0] * 24 + [2] * 24)
        self.assertEqual(rw.scale_path(c + ["Ab", "Db"] + c), [0] * 50)

    def test_no_chords_at_all(self):
        self.assertIsNone(rw.scale_path(["N", None]))

    def test_bars_run_on_past_the_last_downbeat(self):
        self.assertEqual(rw.bars_to_song_end([0.0, 2.0, 4.0], 7.0), [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (6.0, 7.0)])
        self.assertEqual(rw.bars_to_song_end([0.0, 2.0], 2.5), [(0.0, 2.0), (2.0, 2.5)])

    def test_chords_past_the_last_downbeat_count_but_get_no_row(self):
        chords = [{"start": 0.0, "end": 4.0, "label": "C"}, {"start": 4.0, "end": 40.0, "label": "F"},
                  {"start": 40.0, "end": 60.0, "label": "Bb"}]
        out = rw.bar_scales([0.0, 2.0, 4.0], chords, 60.0)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], ("F", "5"))

    def test_bar_without_a_chord(self):
        out = rw.bar_scales([0.0, 2.0, 4.0], [{"start": 0.0, "end": 2.0, "label": "N"}, {"start": 2.0, "end": 4.0, "label": "C"}], 4.0)
        self.assertEqual(out[0][1], None)
        self.assertEqual(out[1][1], "1")


def hats(positions, beats=40, cls="hihat"):
    """Hits at these positions inside each beat, on bars of 4 beats of 0.5 s."""
    return [{"time": round((b + p) * 0.5, 3), "class": cls} for b in range(beats) for p in positions]


GRID = [i * 2.0 for i in range(11)]  # 10 bars, 40 beats


class SwingTest(unittest.TestCase):
    def test_straight_and_swung(self):
        for pos, feel in ((0.5, "straight"), (0.66, "swung")):
            s = rw.swing_field(GRID, DONE, hats([0.0, pos]))
            self.assertEqual((s["off_beat_phase"], s["hits"], s["feel"]), (pos, 40, feel))

    def test_the_boundary_is_swung(self):
        self.assertEqual(rw.swing_field(GRID, DONE, hats([0.58]))["feel"], "swung")

    def test_other_classes_and_positions_do_not_count(self):
        s = rw.swing_field(GRID, DONE, hats([0.5], cls="kick") + hats([0.25, 0.9]))
        self.assertEqual((s["off_beat_phase"], s["hits"], s["feel"]), (None, 0, None))
        self.assertEqual(rw.swing_field(GRID, DONE, hats([0.5], cls="cymbal"))["hits"], 40)

    def test_too_few_hits(self):
        s = rw.swing_field(GRID, DONE, hats([0.5], beats=29))
        self.assertEqual((s["off_beat_phase"], s["hits"], s["feel"]), (None, 29, None))
        self.assertEqual(rw.swing_field(GRID, DONE, hats([0.5], beats=30))["feel"], "straight")

    def test_hits_outside_the_bars_are_dropped(self):
        self.assertEqual(rw.swing_field([10.0, 12.0], DONE, hats([0.5]))["hits"], 4)

    def test_no_field_without_bars_or_drums(self):
        self.assertIsNone(rw.swing_field([], DONE, hats([0.5])))
        self.assertIsNone(rw.swing_field(None, DONE, hats([0.5])))
        self.assertIsNone(rw.swing_field(GRID, {"status": "skipped", "reason": "x"}, []))
        self.assertIsNone(rw.swing_field(GRID, None, []))


class CheckTest(unittest.TestCase):
    def test_accepts_the_worker_fields(self):
        self.assertIsNotNone(server.check_rhythm(stored()))

    def rejected(self, change):
        v = stored()
        change(v)
        self.assertIsNone(server.check_rhythm(v))

    def test_rejects_bad_fields(self):
        self.rejected(lambda v: v.pop("bars"))
        self.rejected(lambda v: v["downbeats"].update(count=9))
        self.rejected(lambda v: v["downbeats"].update(count="3"))
        self.rejected(lambda v: v["bars"]["rows"][0].update(kick=[16]))
        self.rejected(lambda v: v["bars"]["rows"][0].update(kick=[4, 0]))
        self.rejected(lambda v: v["bars"]["rows"][0].update(snare=[1, 1]))
        self.rejected(lambda v: v["bars"]["rows"][1].update(start=2.5))
        self.rejected(lambda v: v["bars"]["rows"][0].update(extra=1))
        self.rejected(lambda v: v["bars"]["rows"][0].update(scale="H"))
        self.rejected(lambda v: v["bars"]["rows"][0].update(degree="8"))
        self.rejected(lambda v: v["bars"]["rows"][0].update(scale=None))
        self.rejected(lambda v: v["bars"]["rows"][0].update(chord=None))
        self.rejected(lambda v: v["bars"]["rows"][1].pop("degree"))
        self.rejected(lambda v: v["bars"]["rows"][1].pop("split"))
        self.rejected(lambda v: v["bars"]["rows"][0].update(split=["Am"]))
        self.rejected(lambda v: v["bars"]["rows"][0].update(split=["Am", "Am"]))
        self.rejected(lambda v: v["bars"]["rows"][0].update(split=["Am", 1]))
        self.rejected(lambda v: v["bars"]["rows"][0].update(split="Am"))
        self.rejected(lambda v: v["bars"]["rows"][0].update(chord=None, scale=None, degree=None, split=["Am", "F"]))

    def test_results_stored_by_0_14_0_stay_valid(self):
        v = stored()
        for r in v["bars"]["rows"]:
            del r["scale"], r["degree"], r["split"]
        self.assertIsNotNone(server.check_rhythm(v))

    def test_results_stored_by_0_15_0_stay_valid(self):
        v = stored()
        for r in v["bars"]["rows"]:
            del r["split"]
        self.assertIsNotNone(server.check_rhythm(v))

    def test_a_split_is_accepted(self):
        v = stored()
        v["bars"]["rows"][0]["split"] = ["Am", "F"]
        self.assertIsNotNone(server.check_rhythm(v))

    def test_swing(self):
        good = rw.swing_field(GRID, DONE, hats([0.0, 0.5]))
        self.assertIsNotNone(server.check_rhythm(stored(swing=good)))
        self.assertIsNotNone(server.check_rhythm(stored(swing=rw.swing_field(GRID, DONE, []))))
        self.assertEqual(server.rhythm_summary(stored(swing=good))["swing"]["feel"], "straight")
        self.assertIsNone(server.rhythm_summary(stored())["swing"])
        for change in (dict(feel="odd"), dict(feel=None), dict(off_beat_phase=0.9), dict(off_beat_phase=None), dict(hits=-1), dict(extra=1),
                       dict(status="failed")):
            self.assertIsNone(server.check_rhythm(stored(swing=dict(good, **change))), change)
        v = stored(swing=good)
        del v["bars"]
        v["downbeats"] = {"status": "not_provided", "reason": "x"}
        self.assertIsNone(server.check_rhythm(v))

    def test_rejects_units_without_downbeats(self):
        self.rejected(lambda v: v.update(downbeats={"status": "not_provided"}))
        self.rejected(lambda v: (v.pop("bars"), v.update(downbeats={"status": "not_provided", "count": 3})))

    def test_summary_leaves_the_rows_out(self):
        s = server.rhythm_summary(stored())
        self.assertEqual(s["bars"]["bar_count"], 2)
        self.assertNotIn("rows", s["bars"])
        self.assertEqual(s["downbeats"]["count"], 3)

    def test_summary_without_units(self):
        s = server.rhythm_summary(dict(RHYTHM_OUT_V40))
        self.assertIsNone(s["bars"])

    def test_page(self):
        p = server.rhythm_page("ab" * 32, stored(), "bars", 2.5, 3.0, 0, 10)
        self.assertEqual((p["total"], p["bars"][0]["chord"], p["reliability"], p["layer_status"]),
                         (1, "F", "reference_candidate", "done"))


WALTZ = [i * 1.5 for i in range(5)]  # 4 bars of 3 beats of 0.5 s
WALTZ_BEATS = [i * 0.5 for i in range(12)]


class MeterTest(unittest.TestCase):
    def test_beats_per_bar(self):
        self.assertEqual(rw.beats_per_bar(WALTZ_BEATS, WALTZ), 3)
        self.assertEqual(rw.beats_per_bar([i * 0.5 for i in range(16)], [0.0, 2.0, 4.0, 6.0, 8.0]), 4)
        self.assertIsNone(rw.beats_per_bar([0.0, 0.5, 1.0, 1.5, 2.0], [0.0, 2.5]))  # 5
        self.assertIsNone(rw.beats_per_bar([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0], [0.0, 1.5, 3.5]))  # median 3.5
        self.assertIsNone(rw.beats_per_bar([], WALTZ))
        self.assertIsNone(rw.beats_per_bar(None, WALTZ))

    def test_clean_beats(self):
        self.assertEqual(rw.clean_beats([0.5, 1.0004, 9.0], 2.0), [0.5, 1.0])
        for times in (None, "x", [1.0, 0.5], [0.5, float("nan")], [0.0] * (rw.MAX_BEATS + 1)):
            self.assertEqual(rw.clean_beats(times, 1e6), [])

    def test_three_beat_bars_get_12_steps(self):
        hits = [{"time": 0.0, "class": "kick"}, {"time": 0.5, "class": "snare"}, {"time": 1.0, "class": "snare"}, {"time": 1.49, "class": "kick"}]
        b = rw.bars_field(WALTZ, [], DONE, hits, False, 6.0, WALTZ_BEATS)
        self.assertEqual((b["beats_per_bar"], b["steps"]), (3, 12))
        self.assertEqual((b["rows"][0]["kick"], b["rows"][0]["snare"], b["rows"][1]["kick"]), ([0], [4, 8], [0]))

    def test_unknown_keeps_16(self):
        b = rw.bars_field(BEATS, CHORDS, DONE, HITS, True, 4.0)
        self.assertEqual((b["beats_per_bar"], b["steps"]), (None, 16))

    def test_swing_reads_beats_of_a_3_beat_bar(self):
        hits = [{"time": round(i * 0.5 + 0.25, 3), "class": "hihat"} for i in range(30)]  # half a beat after each beat
        grid = [i * 1.5 for i in range(11)]
        self.assertEqual(rw.swing_field(grid, DONE, hits, 3)["off_beat_phase"], 0.5)
        self.assertIsNone(rw.swing_field(grid, DONE, hits)["off_beat_phase"])  # read as 4 beats the hits spread out

    def test_server_checks_the_grid(self):
        hits = [{"time": 1.375, "class": "kick"}]  # step 11 of 12
        bars = rw.bars_field(WALTZ, [], DONE, hits, False, 6.0, WALTZ_BEATS)
        v = stored(bars=bars, downbeats=dict(stored()["downbeats"], count=5))
        self.assertEqual(v["bars"]["rows"][0]["kick"], [11])
        self.assertIsNotNone(server.check_rhythm(v))
        self.assertEqual(server.rhythm_summary(v)["bars"]["beats_per_bar"], 3)
        for change in (dict(steps=16), dict(beats_per_bar=4), dict(beats_per_bar=5, steps=20), dict(steps=None)):
            self.assertIsNone(server.check_rhythm(dict(v, bars=dict(bars, **change))), change)
        w = json.loads(json.dumps(v))
        w["bars"]["rows"][0]["kick"] = [12]
        self.assertIsNone(server.check_rhythm(w))
        old = json.loads(json.dumps(stored()))
        del old["bars"]["beats_per_bar"], old["bars"]["steps"]  # stored before worker 0.22.0
        self.assertIsNotNone(server.check_rhythm(old))


if __name__ == "__main__":
    unittest.main()
