# Sung lyrics vs note grid

Scores Music Lens Japanese word output (text and word start times) against a note grid whose lyrics and timing are exact. It is a research scorer, not part of the product.

## Ground truth

The grid comes from a published singing-synth score (UST) rendered by a sample-concatenating singer, not an AI audio model. Two renders of the same project are byte-identical, so every note's kana and start time is known exactly. The vocal is rendered alone, with vibrato off and portamento 0.

The current set is a few Vocaloid songs ("set A"). Song names, scores, grids, renders and results stay outside this repository; only the scorer is published here.

Grid CSV columns used: `kana` (one kana unit per note) and `start_s` (project time, seconds). The render's WAV time 0 is a later project time; pass it as `--offset` (grid WAV time = `start_s - offset`).

## What it measures

- **Kana CER**: ASR text read to hiragana, then edit distance to the grid kana, divided by the grid length.
  - Kanji are read over the whole ASR text, because Whisper often splits a compound across words.
  - 君 is read きみ (pykakasi always gives くん).
  - Both sides use the sung pronunciation: を→お, づ/ぢ→ず/じ, ou→oo, ei→ee, ー→previous vowel, a lone は/へ word→わ/え.
  - っ is dropped on both sides, because score files omit it.
- **Word start**: each ASR word's first exactly matched kana gives that note's start. The scorer reports the signed median error, the share within 0.1/0.2/0.3/0.5 s, and the share within 0.05/0.1/0.2 s after removing the median bias.
  - The rendered voice can start up to about 0.1 s before the note (median about 0.02 s).
  - Words with no exact kana match are counted as untimed.

## Run

```text
python research/lyrics/sung-grid/score.py --grid /local/set-a-01/note-grid.csv --result /local/results/<id>.json --offset 1.24
python -B -m unittest discover -s research/lyrics/sung-grid -p "test_*.py" -v
```

The tests use synthetic notes and words only.
