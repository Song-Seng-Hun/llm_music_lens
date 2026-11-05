"""Musical units (worker 0.14.0): downbeats and bars."""
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
                bars=rw.bars_field(BEATS, CHORDS, DONE, HITS, True))
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
        rows = rw.bars_field(BEATS, CHORDS, DONE, HITS, True)["rows"]
        self.assertEqual(rows, [{"start": 0.0, "end": 2.0, "chord": "Am", "kick": [0], "snare": [4]},
                                {"start": 2.0, "end": 4.0, "chord": "F", "kick": [0], "snare": []}])

    def test_missing_layers_are_none(self):
        row = rw.bars_field(BEATS, CHORDS, {"status": "skipped", "reason": "x"}, [], False)["rows"][0]
        self.assertEqual((row["chord"], row["kick"], row["snare"]), (None, None, None))

    def test_no_downbeats_no_field(self):
        self.assertIsNone(rw.bars_field([], CHORDS, DONE, HITS, True))
        self.assertIsNone(rw.bars_field(None, CHORDS, DONE, HITS, True))


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


if __name__ == "__main__":
    unittest.main()
