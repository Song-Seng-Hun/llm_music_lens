# Server vocal pitch

`POST /mcp` -> `analyze_music_timing` -> `web/lib/server/music-timing.ts` ->
`web/analysis/vocal-pitch.mjs` -> `web/lib/vocal-pitch-context.ts`.
Set `sourceKind: "isolated-vocal"` with the usual mono22050Hz PCM16 input,
1–30seconds. This caller declaration enables the tracker; the server does
not verify isolation or separate a singer. Default mixture returns
`pitch.status: "unavailable"` and skips the tracker.

## Returned evidence

- `contour`: parallel `hz`, fractional `midi` and `periodicity` arrays; at most
  1500frames, no truncation. Frame time=`start + index * stepSeconds`,
  step=.02seconds. Add `source.offsetSeconds` for song time. Crop support and
  unsupported periodicity retain null entries, not deleted/compressed time.
- `summary`: estimated fraction, observed range and at most12most-observed
  nearest-note names with frame counts. MIDI69=A4=440Hz; C4=MIDI60.
  Hz rounds to2decimals; MIDI and periodicity to4. Fractional MIDI preserves
  estimated movement, without inventing note onsets/offsets.
- `method` and `warnings`: exact recipe, clock and limitations. Periodicity
  is an uncalibrated normalized-difference measure, not probability. Null is
  insufficient evidence, not verified silence or absence of a voice.

This is not a score, note transcription, chord/key estimator, lyric alignment,
source separator, automatic vibrato classifier or replacement for browser
SOME. Supplied note/lyric events remain unchanged. Breathy/unvoiced/noisy
passages and overlapping sources can generate false or octave-shifted pitch.

## Recipe

Custom CPU-only YIN-style normalized-difference method, no new dependency,
model download or external API. It is not a numerical implementation of
[librosa YIN](https://librosa.org/doc/0.11.0/generated/librosa.yin.html) or
[pYIN](https://librosa.org/doc/0.11.0/generated/librosa.pyin.html).
No probabilistic/Viterbi voicing decoder or temporal smoothing is applied.

95-tap symmetric Blackman sinc lowpass1800Hz, DC-normalized; samples spaced
4native samples (5512.5Hz),512sample frames centered exactly at k*441native
samples. Symmetric support is ±1069native samples (~48.48ms); missing context
is not reflected/padded. Mean squared differences use all512-lag pairs with
a common mean midpoint, then cumulative mean normalization. Select the first
local trough with parabolically interpolated depth<.15, refine the lag and
reject outside70–1000Hz. AC RMS<.001 abstains. No global-minimum fallback.
Periodicity=clamped1-minus-interpolated-depth.20ms spacing is resolution,
not measured timing accuracy; ~97ms support can blur rapid transitions.

Current method `monophonic-yin-style-central-check-v2` adds one bounded check:
when long-frame periodicity<.95, use a centered256sample frame (~46.26ms)
from the same filtered signal and the same YIN thresholds/range. Retain the
original long-frame Hz/periodicity only if the short estimate is within50c;
otherwise return null for both. Strong estimates and the original crop guard
are unchanged. No averaging, octave folding, temporal interpolation or unknown filling.
The windows are correlated evidence, not independent proof of a voiced source.

## Current refinement, 2026-10-14

Question: reduce false voicing relative to the existing server tracker while
retaining pitch accuracy, genuine leaps/vibrato and bounded local cost.
Pilot used previously seen vocadito10/11/21/24. A single shorter-window recipe
improved development F1 and false voicing; the second allowed recipe was unused.
Candidate, inputs, scorer, projection and gates were then frozen before fresh
vocadito25–28 predictions/annotation parsing. Same archive and resampling as
the initial comparison below. No post-Confirm tuning or cost-run selection.

All9378reference rows remain (6138voiced/3240unvoiced), including crop edges.
The same all-row nearest20ms/4decimalMIDI scoring applies. Primary gates:
macrofalsevoicing reduction>=3pp; macro50cF1 decline<=.5pp and each clip<=3pp.
All pass. Four recordings are a bounded confirmation, not general-song,
Vocaloid, rap or mixed-audio accuracy.

| Macro metric | Previous server | Current server |
|---|---:|---:|
| False voicing / reference unvoiced | 22.96% | 9.73% |
| 50cent frame F1 | 85.04% | 88.18% |
| 20cent frame F1 | 69.05% | 71.98% |
| Pitch precision50c | 83.09% | 90.99% |
| Pitch recall50c | 87.22% | 85.79% |

Every clip F1 improves (+3.18/+3.60/+3.27/+2.53pp). False-voiced counts
744->318; correctly pitched voiced rows5370->5284. This is a precision/recall
tradeoff, not recovery of more true notes. Gross/octave errors were0on both
sides of this fresh set, so it establishes no octave-error improvement.
Known leaps reach the correct plateau within120ms; supported plateau accuracy
100%. Synthetic vibrato range/phase/error remain unchanged; crop unknowns,
missing-fundamental, noise and silence guards pass. Synthetic cases do not
certify all real musical transitions.

One paired cost run: three fixed seen clips10/11/21, one warmup and four serial
ABBA blocks each (eight observations per method). Tracker plus unchanged
projection adds32.81/27.18/29.77ms normalized per30s; median ratios
1.212/1.170/1.183. All pass <=100ms/30s added and <=1.5x. This is a modest
extra computation for fewer false pitches, not a speed gain or a whole-MCP
latency measurement. The short-frame buffer adds2048bytes; whole-isolate
peak remains unmeasured.

Independent preflight found a baseline-qualified leap guard weaker than the
declared absolute requirement. Corrected before Confirm; original Pilot
output/scorer kept, same predictions rescored, verdict unchanged. No budget
reset. Local audit: `evidence/pitch-refinement-17/` (inputs, freeze, original
and repaired scorer, all predictions/counts/timings, integration and review).

Integrated through the existing `analyze_music_timing` route. Three fixed
actual built-MCP calls match the frozen direct candidate and preserve all
non-pitch fields except two diagnostic timers. Both mixture calls are exactly
unchanged; the isolated-vocal response adds816bytes including metadata.
Repository tests108/108, typecheck/build pass; lint0errors/3existing warnings.
The first sandbox runtime connection failed before comparisons; its receipt
is retained. The same built artifact/harness passes with scoped local runtime
access. Single whole-request timings are not a speed comparison. Hosted
deployment and whole-isolate memory peak remain unverified.

## Initial tracker comparison, 2026-10-14 (before the refinement)

Question: is this complete server recipe useful compared with the unchanged
cheap `web/public/audio-engine.mjs` quick pitch intervals at bounded local cost?
The old server had no pitch; absence was not scored as a competing model.
No superiority comparison against SOME, neural trackers or pYIN was run.

Pilot vocadito10/11 selected A/B, then Confirm21–24 were the next four unused
IDs after20, chosen before viewing their F0 difficulty. Local archive v3
SHA256 `e0d6b99d3f9c594afe5ae5c4d7bdacebe569e53b809e90b89d1c771c4f9990e3`;
[vocadito source](https://zenodo.org/records/5578807), recorded as CC BY4.0 in
the retained input manifest (source page refetch timed out this turn). Existing
44.1kHz audio resampled with fixed polyphase2:1 and PCM16quantization; recorded
input hashes. No recordings/annotations are committed.

All12600reference rows within PCM duration remain: voiced, unvoiced,
out-of-range and crop edges. Nearest20ms frame, half-tie upward;6rows outside
nearest-index bounds score null. No fitted shift/filtering. Candidate scoring
uses the actual exposed fractional MIDI rounded4decimals. Primary is the
unweighted four-clip mean of `2*correct50c/(referenceVoiced+predictedVoiced)`.
Frozen gates: F1>=.75, gain>=.01, pitchprecision>=.80; Node<=500ms normalized
per30s, whole-MCP added pitch bytes<=80000. Four clips are not12600independent
recordings; these data do not establish general-song or rap accuracy.

| Macro metric | Unchanged quick intervals | Server recipe B |
|---|---:|---:|
| 50cent frame F1 | 51.53% | 80.99% |
| 20cent frame F1 | 36.62% | 68.83% |
| Pitch precision50c | 76.69% | 82.12% |
| Pitch recall50c | 39.61% | 80.90% |
| False voicing / reference unvoiced | 15.95% | 23.96% |
| Gross error>200c / both voiced | 2.32% | 4.45% |
| Octave error within50c of1200c / both voiced | 1.04% | 1.97% |

Primary improvement is+29.47percentage points and all primary gates pass;
false-voicing and some gross/octave errors worsen. Clip21F1=71.87%; clip24
precision=73.87%. Do not turn aggregate success into a claim that every clip,
every metric or unvoiced detection improved. No post-Confirm tuning.

Pilot A's2000Hz subharmonic acceptance is retained as a failure. The one
allowed B correction thresholds interpolated trough depth before range
rejection. B fixes that regression; synthetic harmonic bank388/400frames
within50c (4octave errors), glide/vibrato395/400, negative fixtures0/400false
frames. These synthetic checks do not validate real-song accuracy.

Single Confirm Node calls normalize to161.77–206.56ms per30s; not an
end-to-end speed claim. Dense1500frame serialization with actual metadata
adds71834bytes across MCP text+structured copies. The FIR buffer at maximum
input alone uses5,292,000bytes, plus bounded frame/output arrays; whole-isolate
peak is unmeasured. Local Worker/MCP results are recorded separately;
Sites publication/team access remains unverified.

Four fixed before/after actual MCP requests preserve every prior field except
two diagnostic timers. Three default-mixture requests skip pitch and add
322bytes; isolated vocal0000 adds26390bytes (513frames,284estimated). All9pitch
fields/arrays/metadata equal the direct frozen recipe exactly in workerd.
Final immutable-build HTTP acceptance9/9, repository tests99/99, typecheck and
build pass; lint has0errors/3existing warnings. All four single whole-MCP pairs
were slower:2592.52->3285.68,2937.54->3552.64,2910.22->3271.24,
1203.41->1544.62ms. No repeated cost selection; total latency impact is
inconclusive, not a speed gain. Normalized standalone DSP cost and whole-MCP
time are separate measurements.

Local audit artifacts (absent from a clean clone): `evidence/server-pitch-14/`
contains immutable Pilot failures, Confirm protocol/freeze, all predictions,
scores and denominators, numerical/synthetic checks, actual MCP before/after
receipts, final acceptance and review. Source tests run through `npm test`.
