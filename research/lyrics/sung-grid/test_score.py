import unittest

from score import phon, prepare, score


def grid(kana, step=0.5, start=1.0):
    return [{'kana': k, 'start_s': str(start + i * step)} for i, k in enumerate(kana)]


class ScoreTest(unittest.TestCase):
    def test_exact_match(self):
        notes = grid(['さ', 'く', 'ら'])
        r = score(notes, [{'text': 'さくら', 'start': 0.0}], offset=1.0)
        self.assertEqual(r['kana_cer_pct'], 0)
        self.assertEqual(r['timed_words'], 1)
        self.assertEqual(r['start_err_median_s'], 0)

    def test_timing_uses_word_first_matched_kana(self):
        notes = grid(['き', 'み', 'の', 'こ', 'え'])
        words = [{'text': 'きみの', 'start': 0.1}, {'text': 'こえ', 'start': 1.4}]
        r = score(notes, words, offset=1.0)
        self.assertEqual(r['timed_words'], 2)
        self.assertEqual(r['start_err_median_s'], 0)  # errors +0.1 (き at 0.0) and -0.1 (こ at 1.5)
        self.assertEqual(r['abs_err_median_s'], 0.1)

    def test_kanji_read_in_context_across_split_words(self):
        notes = grid(['ば', 'しょ'])
        r = score(notes, [{'text': '場', 'start': 0.0}, {'text': '所', 'start': 0.2}], offset=1.0)
        self.assertEqual(r['kana_cer_pct'], 0)

    def test_sokuon_dropped_both_sides(self):
        self.assertEqual(prepare(list('ずっと'), [0, 0, 1])[0], list('ずと'))

    def test_particle_and_long_vowel(self):
        notes = grid(['き', 'み', 'わ'])
        r = score(notes, [{'text': '君', 'start': 0.0}, {'text': 'は', 'start': 1.0}], offset=1.0)
        self.assertEqual(r['kana_cer_pct'], 0)
        self.assertEqual(phon('そーら'), 'そおら')
        self.assertEqual(phon('こうえい'), 'こおええ')

    def test_errors_counted(self):
        notes = grid(['あ', 'い', 'う'])
        r = score(notes, [{'text': 'あお', 'start': 0.0}], offset=1.0)
        self.assertEqual(r['ops'], {'sub': 1, 'ins': 0, 'del': 1})


if __name__ == '__main__':
    unittest.main()
