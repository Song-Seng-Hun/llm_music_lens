import unittest

import music_lens_local as m


def seg(seek, *words, text=None):
    ws = [{"word": w, "start": t, "end": t + 0.4, "probability": 0.9} for w, t in words]
    return {"seek": seek, "text": text if text is not None else "".join(w for w, _ in words), "words": ws}


class FakeTranscribe:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, clip):
        self.calls.append(clip)
        return self.answers.get(clip, [])


class RereadTests(unittest.TestCase):
    def test_clean_windows_untouched(self):
        segs = [seg(0, (" a", 1.0)), seg(3000, (" b", 31.0))]
        fake = FakeTranscribe({})
        out, rereads = m.reread_windows(segs, 60.0, fake)
        self.assertEqual(out, segs)
        self.assertEqual((rereads, fake.calls), ([], []))

    def test_marker_window_replaced_by_first_clean_retry(self):
        segs = [seg(0, (" a", 1.0)), seg(3000, (" ご視聴ありがとうございました", 31.0)), seg(6000, (" c", 61.0))]
        fake = FakeTranscribe({"32.0,60.0": [seg(3200, (" b", 40.0), (" late", 61.0))]})
        out, rereads = m.reread_windows(segs, 90.0, fake)
        self.assertEqual(fake.calls, ["32.0,60.0"])
        self.assertEqual([w["word"] for s in out for w in s["words"]], [" a", " b", " c"])  # retry words past the window dropped
        self.assertEqual(rereads, [{"start": 30.0, "end": 60.0, "reason": "marker", "shift_seconds": 2}])

    def test_loop_retries_until_clean(self):
        segs = [seg(0, (" " + "오" * 223, 5.0))]
        loop = [seg(200, (" " + "오" * 50, 6.0))]
        fake = FakeTranscribe({"2.0,30.0": loop, "4.0,30.0": loop, "6.0,30.0": [seg(600, (" 압생트", 7.0))]})
        out, rereads = m.reread_windows(segs, 30.0, fake)
        self.assertEqual(fake.calls, ["2.0,30.0", "4.0,30.0", "6.0,30.0"])
        self.assertEqual([w["word"] for s in out for w in s["words"]], [" 압생트"])
        self.assertEqual(rereads[0]["reason"], "loop")
        self.assertEqual(rereads[0]["shift_seconds"], 6)

    def test_unfixable_window_drops_only_junk_words(self):
        segs = [seg(0, (" real", 1.0), (" Thank you for watching", 20.0), text=" real Thank you for watching")]
        out, rereads = m.reread_windows(segs, 30.0, FakeTranscribe({}))
        self.assertEqual([w["word"] for s in out for w in s["words"]], [" real"])
        self.assertEqual(rereads, [{"start": 0.0, "end": 30.0, "reason": "marker", "shift_seconds": None}])

    def test_wordless_window_and_short_tail(self):
        segs = [seg(0, (" a", 1.0)), {"seek": 3000, "text": "", "words": []}]
        fake = FakeTranscribe({})
        out, rereads = m.reread_windows(segs, 34.5, fake)
        self.assertEqual(fake.calls, ["32.0,34.5"])  # 34.0,34.5 is too short to try
        self.assertEqual(rereads[0]["reason"], "no_words")
        self.assertEqual([w["word"] for s in out for w in s["words"]], [" a"])

    def test_checked_rereads_rejects_bad_entries(self):
        ok = [{"start": 30.0, "end": 60.0, "reason": "loop", "shift_seconds": 2}]
        self.assertEqual(m.checked_rereads(ok, 90.0), ok)
        for bad in ([{"start": 30.0, "end": 60.0, "reason": "other", "shift_seconds": 2}],
                    [{"start": 30.0, "end": 60.0, "reason": "loop", "shift_seconds": 3}], "x"):
            with self.assertRaises(m.Failure):
                m.checked_rereads(bad, 90.0)


if __name__ == "__main__":
    unittest.main()
