import unittest

import music_lens_local as m


def seg(seek, *words, text=None):
    ws = [{"word": w, "start": t, "end": t + 0.4, "probability": 0.9} for w, t in words]
    return {"seek": seek, "text": text if text is not None else "".join(w for w, _ in words), "words": ws}


QUIET = lambda start, end: -40.0  # below the sparse check
LOUD = lambda start, end: -10.0


def many(t0, n, prefix=" w"):
    return [(f"{prefix}{i}", t0 + i * 0.25) for i in range(n)]


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
        out, rereads = m.reread_windows(segs, 60.0, fake, QUIET)
        self.assertEqual(out, segs)
        self.assertEqual((rereads, fake.calls), ([], []))

    def test_marker_window_replaced_by_first_clean_retry(self):
        segs = [seg(0, (" a", 1.0)), seg(3000, (" ご視聴ありがとうございました", 31.0)), seg(6000, (" c", 61.0))]
        fake = FakeTranscribe({"32.0,60.0": [seg(3200, (" b", 40.0), (" late", 61.0))]})
        out, rereads = m.reread_windows(segs, 90.0, fake, QUIET)
        self.assertEqual(fake.calls, ["32.0,60.0"])
        self.assertEqual([w["word"] for s in out for w in s["words"]], [" a", " b", " c"])  # retry words past the window dropped
        self.assertEqual(rereads, [{"start": 30.0, "end": 60.0, "reason": "marker", "shift_seconds": 2}])

    def test_loop_retries_until_clean(self):
        segs = [seg(0, (" " + "오" * 223, 5.0))]
        loop = [seg(200, (" " + "오" * 50, 6.0))]
        fake = FakeTranscribe({"2.0,30.0": loop, "4.0,30.0": loop, "6.0,30.0": [seg(600, (" 압생트", 7.0))]})
        out, rereads = m.reread_windows(segs, 30.0, fake, QUIET)
        self.assertEqual(fake.calls, ["2.0,30.0", "4.0,30.0", "6.0,30.0"])
        self.assertEqual([w["word"] for s in out for w in s["words"]], [" 압생트"])
        self.assertEqual(rereads[0]["reason"], "loop")
        self.assertEqual(rereads[0]["shift_seconds"], 6)

    def test_unfixable_window_drops_only_marker_runs(self):
        segs = [seg(0, (" I", 1.0), (" love", 2.0), (" you", 3.0), (" Thank", 20.0), (" you", 20.5), (" for", 21.0), (" watching", 21.5))]
        out, rereads = m.reread_windows(segs, 30.0, FakeTranscribe({}), QUIET)
        self.assertEqual([w["word"] for s in out for w in s["words"]], [" I", " love", " you"])
        self.assertEqual(rereads, [{"start": 0.0, "end": 30.0, "reason": "marker", "shift_seconds": None}])

    def test_wordless_window_left_alone_and_short_tail(self):
        segs = [{"seek": 0, "text": "", "words": []}, seg(3000, ("ご", 31.0), ("視聴", 31.3))]
        fake = FakeTranscribe({})
        out, rereads = m.reread_windows(segs, 34.5, fake, QUIET)
        self.assertEqual(fake.calls, ["32.0,34.5"])  # no retry for the wordless window; 34.0,34.5 is too short to try
        self.assertEqual(rereads, [{"start": 30.0, "end": 34.5, "reason": "marker", "shift_seconds": None}])
        self.assertEqual([w["word"] for s in out for w in s["words"]], [])

    def test_sparse_window_takes_retry_with_most_words(self):
        segs = [seg(0, *many(0.0, 60)), seg(3000, *many(30.0, 4)), seg(6000, *many(60.0, 60))]
        fake = FakeTranscribe({"32.0,60.0": [seg(3200, *many(32.0, 20))], "34.0,60.0": [seg(3400, *many(34.0, 40, " r"))]})
        out, rereads = m.reread_windows(segs, 90.0, fake, LOUD)
        self.assertEqual(fake.calls, ["32.0,60.0", "34.0,60.0", "36.0,60.0", "38.0,60.0", "40.0,60.0"])
        self.assertEqual(sum(1 for s in out for w in s["words"] if w["word"].startswith(" r")), 40)
        self.assertEqual(rereads, [{"start": 30.0, "end": 60.0, "reason": "sparse", "shift_seconds": 4}])

    def test_sparse_needs_ten_more_words_and_no_repeat_run(self):
        segs = [seg(0, *many(0.0, 60)), seg(3000, *many(30.0, 4)), seg(6000, *many(60.0, 60))]
        fake = FakeTranscribe({"32.0,60.0": [seg(3200, *many(32.0, 12))],
                               "34.0,60.0": [seg(3400, *[(" おやすみ", 34.0 + i) for i in range(20)])]})
        out, rereads = m.reread_windows(segs, 90.0, fake, LOUD)
        self.assertEqual(len([w for s in out for w in s["words"]]), 124)  # window kept as it was
        self.assertEqual(rereads[0]["shift_seconds"], None)
        self.assertEqual(m.reread_windows(segs, 90.0, fake, QUIET)[1], [])  # quiet audio: not sparse

    def test_long_repeated_sentence_retry_rejected(self):
        segs = [seg(0, *many(0.0, 60)), seg(3000, *many(30.0, 4)), seg(6000, *many(60.0, 60))]
        loop = [seg(3200, *[(w, 32.0 + i * 0.2) for i, w in enumerate(" We are now at the University of South Carolina".split(" ")[1:] * 5)])]
        out, rereads = m.reread_windows(segs, 90.0, FakeTranscribe({"32.0,60.0": loop}), LOUD)
        self.assertEqual(rereads[0]["shift_seconds"], None)

    def test_credit_text_dropped_when_no_retry_is_better(self):
        segs = [seg(0, ("作詞", 1.0), ("・", 1.4), ("作曲", 1.8), (" ああ", 10.0))]
        out, rereads = m.reread_windows(segs, 30.0, FakeTranscribe({}), QUIET)
        self.assertEqual([w["word"] for s in out for w in s["words"]], ["・", " ああ"])  # only the credit words go
        self.assertEqual(rereads, [{"start": 0.0, "end": 30.0, "reason": "credit", "shift_seconds": None}])

    def test_first_window_never_sparse(self):
        segs = [seg(0, *many(0.0, 4)), seg(3000, *many(30.0, 60)), seg(6000, *many(60.0, 60))]
        self.assertEqual(m.reread_windows(segs, 90.0, FakeTranscribe({}), LOUD)[1], [])

    def test_short_word_repeat_takes_first_clean_retry(self):
        segs = [seg(0, *[(" oh,", 1.0 + i * 0.2) for i in range(40)])]
        fake = FakeTranscribe({"2.0,30.0": [seg(200, (" a", 3.0), (" b", 4.0))]})
        out, rereads = m.reread_windows(segs, 30.0, fake, QUIET)
        self.assertEqual([w["word"] for s in out for w in s["words"]], [" a", " b"])
        self.assertEqual(rereads, [{"start": 0.0, "end": 30.0, "reason": "loop", "shift_seconds": 2}])

    def test_repeat_stays_when_every_retry_repeats(self):
        words = [(" hello,", 1.0 + i * 0.2) for i in range(10)]
        loop = [seg(200, *words)]
        out, rereads = m.reread_windows([seg(0, *words)], 30.0, FakeTranscribe({f"{k}.0,30.0": loop for k in (2, 4, 6, 8, 10)}), QUIET)
        self.assertEqual(len([w for s in out for w in s["words"]]), 10)  # it may be sung, so nothing is dropped
        self.assertEqual(rereads[0]["shift_seconds"], None)

    def test_korean_subtitle_credit_is_a_marker(self):
        segs = [seg(0, (" a", 1.0), (" 한국어", 9.0), (" 자막을", 9.5), (" 사용하였습니다.", 10.0), (" b", 12.0))]
        out, rereads = m.reread_windows(segs, 30.0, FakeTranscribe({}), QUIET)
        self.assertEqual([w["word"] for s in out for w in s["words"]], [" a", " 한국어"])
        self.assertEqual(rereads[0]["reason"], "marker")

    def test_checked_rereads_rejects_bad_entries(self):
        ok = [{"start": 30.0, "end": 60.0, "reason": "loop", "shift_seconds": 2}, {"start": 60.0, "end": 90.0, "reason": "sparse", "shift_seconds": None}]
        self.assertEqual(m.checked_rereads(ok, 90.0), ok)
        for bad in ([{"start": 30.0, "end": 60.0, "reason": "no_words", "shift_seconds": 2}],
                    [{"start": 30.0, "end": 60.0, "reason": "loop", "shift_seconds": 3}], "x"):
            with self.assertRaises(m.Failure):
                m.checked_rereads(bad, 90.0)


if __name__ == "__main__":
    unittest.main()
