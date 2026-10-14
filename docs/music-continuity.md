# Song-clock evidence composition

`POST /mcp` -> `compose_music_timeline` -> `lib/music-continuity.ts` ->
`analysis/music-continuity.mjs`. This read-only tool assembles existing observations;
it does not rerun a model, read audio, save state or change the pitch/beat detector.
It complements `analyze_music_structure` (1s texture/transition evidence) and
`get_music_timeline` (saved report lookup).

## Use

1. Call `analyze_music_timing` for 1–30s clips, with their real `offsetSeconds`
   within the same song. Keep the results in caller storage, not chat text.
2. Supply each result's `source`, `timing` and `pitch` subobjects unchanged in
   `segments`. Extra fields inside those projections are ignored. Do not send PCM
   or the entire audio-analysis response again.
3. Request a half-open song interval `[start,end)` no longer than 30s. `duration`
   is the full song duration, at most600s.
4. If `window.nextStart` is not null, repeat the same segments and duration with
   `start=nextStart` and the **original end**. All layers use the same page cut.

```js
const segments = results.map(({ source, timing, pitch }) => ({ source, timing, pitch }));
const args = { duration: 180, start: 25, end: 45, segments };
// tools/call name="compose_music_timeline", arguments=args
// Next page: {...args, start: result.window.nextStart}; end stays45.
```

At most60segments,33000pitch frames and6000combined beat/downbeat observations
are accepted before deduplication, within the existing MCP request-byte limit.
For longer inputs, pass the original clips intersecting the requested window;
do not cut or renumber their arrays. Keep that same segment set for continuation.
Supported timing provenance is the existing50Hz/250frame/6border/keep_first/
official-minimal recipe; pitch is `monophonic-yin-style-central-check-v2` or an
explicit unavailable result. Unsupported clocks/recipes, partial contours and
inconsistent observations under the same identity are rejected.

## Time, identity and overlap

Each beat/downbeat row retains `sourceIndex`, original `index`, `localTime`, and
song `time`. Pitch slices retain `sourceIndex`, `firstFrameIndex`, original
contour `start`, `stepSeconds`, and parallel Hz/MIDI/periodicity arrays. For slice
element j, use this exact expression, not a newly rebased grid:

```js
sources[slice.sourceIndex].source.offsetSeconds
  + (slice.start + (slice.firstFrameIndex + j) * slice.stepSeconds)
```

Public Hz/MIDI/periodicity values and nulls are unchanged. Fractional clip offsets
can produce out-of-phase20ms grids. No resampling, interpolation, rounding of
times, note-boundary inference or global BPM computation occurs.

Canonical source order is independent of input order. The source table is stable
across continuation calls with identical inputs. Exact source+placement+recipe
duplicates collapse only if all observations agree; conflicting duplicates fail.
Different source identities remain separate views, even where their times agree.
Do **not** add these views together as independent musical hits or choose the
highest-periodicity view as verified truth. No calibrated cross-crop selection
criterion is available. Different overlapping observations are not automatically
evidence of an actual musical contradiction.

Each page has <=1000pitch frames and <=256combined beat/downbeat rows. Coincident
observations are kept together. The earliest overflow time cuts every layer;
it appears on the next page, never skipped with an epsilon. `totals` counts the
entire requested interval; `returned` counts this page. `coverage.intervals`,
`gaps` and `overlaps` cover only `[window.start,window.end)`, the returned page.

Coverage means supplied audio, not voice, silence, complete beat detection or
accurate pitch. Null means unknown; unavailable pitch remains explicit per source.
Crop-edge uncertainty stays intact. PCM hashes and recipe labels are caller-held
provenance, not proof of common-song origin, authentic estimates or authorization.

## Verification scope

Frozen confirmation (2026-10-14):8groups/17cases,13valid cases across72pages;
48432original source/index/time/value observations preserved exactly once after
authorized duplicate collapse, plus4rejection cases. Independent Python ledger
includes30s/60s boundaries, fractional offsets, missing context, contradictory
overlap and recorded native MCP output. The duplicate fixture removes206repeated
copies from412inputs; it never removes distinct overlap evidence.

One fresh Node maximum-catalog chain (60sources/33000pitch frames/6000beat rows),
no warmup or selected reruns:56pages, first/max59.1088ms per composition call
including validation, total2333.6087ms; loading excluded. Largest text+structured
MCP envelope158276bytes. These are bounded local cost observations, not a speedup
versus an existing composer or hosted latency. Actual built MCP connects three
overlapping audio excerpts to two pages:1126pitch frames+28beat/downbeat rows,
no loss;13.4471/8.3784ms,46547/10513wire bytes. Original audio analysis remains
identical except two timing diagnostics. Tests116/116, typecheck/build pass;
lint0errors/3existing warnings.

Original failures are retained: a catalog count test expected5rather than6tools;
the oracle assumed internal.status400 rather than ZodError; the first MCP negative
fixture accidentally shared a timing object between duplicates. Each received
one scoped correction, no benchmark/model rerun. Final evidence combines original
successes with repaired negative checks; it does not relabel failed original logs.

Local `evidence/continuity-18/` contains the frozen contract/fixture hashes,
independent exact-identity ledger, source-derived checks, bounded-page cost/wire
measurements and actual compiled MCP results. This stage tests composition
correctness and usability, not a gain in musical-detection accuracy. Real-world
cross-crop disagreement resolution and hosted Sites deployment are unverified.

The256KiB wire-size target is a measurement target for the frozen maximum catalog,
not a server-enforced output byte cap. Output is bounded by source and observation
counts; long supplied reasons, Unicode/JSON escaping or numeric precision can
increase wire bytes. Review probes with60unavailable sources and512character
reasons produced278477bytes (Korean) or464781bytes (control-character escaping).
Those target misses remain recorded; text is not silently clipped to claim a pass.
