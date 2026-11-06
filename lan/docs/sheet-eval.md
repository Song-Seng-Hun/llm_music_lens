# LLM sheet question test

The question: does an LLM answer questions about a song more accurately from `/sheet/<id>` (`sheet.py`) than from the
`/report` JSON it gets today, without inventing values for layers that were not measured?

## Setup

- 45 questions over four stored results (workers 0.4.3, 0.10.0, 0.19.0, 0.21.0; one with a 0.29 s section, two with
  no bar grid, parts or drums). Answers come from the raw stored result by code, never from a sheet.
- Question types: tempo, form counts, section at a time, short section, key at a time, chord at a time, no-chord span,
  bar chord, bar snare steps, highest melody note, melody direction, note count in a range, loudest span, first vocal
  section, and four questions whose right answer is "not measured".
- Conditions, same questions and the same lookup access (up to 5 `/timeline`-style row requests, then answer):
  A = the `/report` rhythm summary JSON, B = an earlier sheet draft, C = `sheet.py`.
- Test takers: fresh Claude Sonnet subagents that saw only their prompt and lookup files (tool logs checked).
- Bars frozen before the first run: C accuracy >= A; C false claims = 0; C accuracy >= B; C input <= 50% of A.

## Results (run 2, 2026-11-06)

| Condition | Correct (of 45) | False claims (of 4) | Data size vs A |
| --- | --- | --- | --- |
| A `/report` JSON | 40 | 2 | 100% |
| B earlier draft | 39 | 0 | 25% |
| C `sheet.py` | 44 | 0 | 34% |

- The accuracy and false-claim bars passed. The input-size bar, measured as whole-prompt tokens, failed (93%) because
  each taker carries a fixed overhead of about 73k tokens; it is measured on the data part from the next run.
- A invented values for both unmeasured layers of the 0.10.0 result (parts share 0, empty snare list) and wrote keys in
  other spellings. B lost precision (times shown as mm:ss) and had no no-chord marks. C's one miss came from printing
  the section label and its role together; the label and role are now separate.
- Run 1 (same questions, a different taker setup, an earlier `sheet.py`): A 34, B 39, C 41 under the stricter first
  grader. The grader now accepts flat spellings as their sharp equivalents and a label-only answer where only the label
  was asked.

Data and scripts stay local (no songs in this repository).
