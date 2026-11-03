"""Score Music Lens Japanese word output against a sung note grid (one kana unit per note).

The grid comes from a deterministic singing-synth render of a published score, so each note's
lyric and start time are exact. Text: kana CER after reading ASR kanji to hiragana. Timing: each
ASR word's first exactly matched kana -> that note's start.
"""
import argparse
import csv
import json
import statistics
import unicodedata

import pykakasi

_kks = pykakasi.kakasi()

VOW = {}
for _v, _row in zip('aiueo', ['あかさたなはまやらわがざだばぱぁゃ', 'いきしちにひみりぎじぢびぴぃ', 'うくすつぬふむゆるぐずづぶぷぅゅ',
                              'えけせてねへめれげぜでべぺぇ', 'おこそとのほもよろをごぞどぼぽぉょ']):
    for _c in _row:
        VOW[_c] = _v
LONG = {'a': 'あ', 'i': 'い', 'u': 'う', 'e': 'え', 'o': 'お'}
PARTICLES = {'は': 'わ', 'へ': 'え'}  # an ASR word that is only the particle is sung as わ/え


def hira(s):
    """Hiragana and ー only; katakana folded, everything else dropped."""
    s = ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in unicodedata.normalize('NFKC', s))
    return ''.join(c for c in s if 'ぁ' <= c <= 'ゟ' or c == 'ー')


def phon(s):
    """Sung-pronunciation form, length-preserving: を->お, づ/ぢ->ず/じ, ou->oo, ei->ee, ー->previous vowel."""
    s = s.replace('を', 'お').replace('づ', 'ず').replace('ぢ', 'じ')
    out = []
    for c in s:
        v = VOW.get(out[-1]) if out else None
        if c == 'ー' and v:
            c = LONG[v]
        elif c == 'う' and v == 'o':
            c = 'お'
        elif c == 'い' and v == 'e':
            c = 'え'
        out.append(c)
    return ''.join(out)


def grid_kana(notes):
    """Kana sequence and the note index of each kana."""
    seq = [(c, i) for i, r in enumerate(notes) for c in hira(r['kana'])]
    return [c for c, _ in seq], [i for _, i in seq]


def asr_kana(words):
    """Kana sequence and the word index of each kana.

    Kanji are read over the whole text, not per word: Whisper often splits a kanji compound across
    words, and a per-word reading loses context (場所->ばところ). 君 is read きみ, its usual sung reading.
    """
    texts = [unicodedata.normalize('NFKC', PARTICLES.get(w['text'].strip(), w['text'])) for w in words]
    owner = [j for j, t in enumerate(texts) for _ in t]
    seq, pos = [], 0
    for item in _kks.convert(''.join(texts)):
        reading = item['hira'].replace('くん', 'きみ') if '君' in item['orig'] else item['hira']  # pykakasi: always くん
        span, kana = len(item['orig']), hira(reading)
        seq += [(c, owner[pos + min(q * span // len(kana), span - 1)]) for q, c in enumerate(kana)]
        pos += span
    return [c for c, _ in seq], [j for _, j in seq]


def prepare(chars, owners):
    """Drop っ (score files omit sokuon), then apply phon over the whole sequence."""
    kept = [(c, o) for c, o in zip(chars, owners) if c != 'っ']
    return list(phon(''.join(c for c, _ in kept))), [o for _, o in kept]


def align(A, G):
    """Levenshtein with backtrace: (distance, ops, match {a_index: g_index} for equal pairs)."""
    n, m = len(A), len(G)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        D[i][0] = i
    for k in range(m + 1):
        D[0][k] = k
    for i in range(1, n + 1):
        ai, row, prev = A[i - 1], D[i], D[i - 1]
        for k in range(1, m + 1):
            row[k] = min(prev[k] + 1, row[k - 1] + 1, prev[k - 1] + (ai != G[k - 1]))
    i, k, match, ops = n, m, {}, {'sub': 0, 'ins': 0, 'del': 0}
    while i or k:
        if i and k and D[i][k] == D[i - 1][k - 1] + (A[i - 1] != G[k - 1]):
            if A[i - 1] == G[k - 1]:
                match[i - 1] = k - 1
            else:
                ops['sub'] += 1
            i, k = i - 1, k - 1
        elif i and D[i][k] == D[i - 1][k] + 1:
            ops['ins'] += 1
            i -= 1
        else:
            ops['del'] += 1
            k -= 1
    return D[n][m], ops, match


def score(notes, words, offset):
    """offset: project time of WAV t=0 (grid WAV time = start_s - offset)."""
    G, g_note = prepare(*grid_kana(notes))
    A, a_word = prepare(*asr_kana(words))
    dist, ops, match = align(A, G)
    first = {}
    for p in sorted(match):
        first.setdefault(a_word[p], match[p])
    errs = [words[j]['start'] - (float(notes[g_note[k]]['start_s']) - offset) for j, k in sorted(first.items())]
    out = {'grid_notes': len(notes), 'grid_kana': len(G), 'asr_words': len(words), 'asr_kana': len(A),
           'kana_cer_pct': round(100 * dist / len(G), 2), 'ops': ops,
           'timed_words': len(errs), 'unmatched_words': len(words) - len(errs)}
    if not errs:
        return out
    ab = sorted(abs(e) for e in errs)
    med = statistics.median(errs)
    within = lambda es, t: round(100 * sum(1 for e in es if abs(e) <= t) / len(es), 1)
    out.update({
        'start_err_median_s': round(med, 3), 'abs_err_median_s': round(statistics.median(ab), 3),
        'within_pct': {t: within(errs, float(t)) for t in ('0.1', '0.2', '0.3', '0.5')},
        'worst_abs_s': round(ab[-1], 3),
        'within_pct_after_median_shift': {t: within([e - med for e in errs], float(t)) for t in ('0.05', '0.1', '0.2')},
    })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--grid', required=True, help='note-grid CSV (columns include start_s, kana)')
    ap.add_argument('--result', required=True, help='Music Lens result JSON (words[] with text, start)')
    ap.add_argument('--offset', required=True, type=float, help='project time of WAV t=0, seconds')
    a = ap.parse_args()
    with open(a.grid, encoding='utf-8-sig') as f:
        notes = list(csv.DictReader(f))
    with open(a.result, encoding='utf-8') as f:
        words = json.load(f)['words']
    print(json.dumps(score(notes, words, a.offset), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
