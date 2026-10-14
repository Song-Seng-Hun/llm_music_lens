# Server vocal pitch

`POST /mcp` -> `analyze_music_timing` -> `lib/server/music-timing.ts` ->
`analysis/vocal-pitch.mjs` -> `lib/vocal-pitch-context.ts`.
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

## Frozen comparison, 2026-10-14

Question: is this complete server recipe useful compared with the unchanged
cheap `public/audio-engine.mjs` quick pitch intervals at bounded local cost?
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
