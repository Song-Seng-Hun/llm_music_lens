# Server attack candidates and beat relations

`analyze_music_timing` adds `attacks`. It reuses the existing beat frontend FFT
to observe positive changes in frequency magnitudes. These changes can expose
a new sound over an already-active sustained background. No new model, FFT,
API, audio storage or browser is required.

The shared operation follows the positive spectral-difference idea documented
by [librosa onset_strength_multi](https://librosa.org/doc/0.11.0/generated/librosa.onset.onset_strength_multi.html).
Our JavaScript recipe uses its own existing raw-magnitude frontend, bands,
smoothing, gates and peak selection; it is not a numerical port or validation
of librosa's default log-power/mel recipe.

## What an agent receives

- `events`: the first 64 chronological candidates. Each has an excerpt-relative
  `time`, uncalibrated `strength`, dominant change `band`, and `fluxShares`.
- `nearestBeat`: index, actual detected beat time and signed `offsetSeconds`.
  Negative means candidate before that beat; positive means after.
- `beatPhase`: actual preceding beat index and fraction of the next observed
  interval. Fraction 0.5 is halfway between those beats, not proof of syncopation.
- `total` and `truncated`: whether candidate detail was omitted. Request a
  shorter excerpt to recover detail; discarded candidates are not guessed.
- `density`: at most 30 one-second windows, using **all** candidates before the
  event cap. Each has count, candidates/second and dominant-band counts. The
  final window uses actual PCM duration, even for a sub-millisecond tail.

Offsets use actual consecutive beat estimates, not a constant-BPM grid.
Midpoint ties choose the earlier beat. Fewer than 2 beats or a candidate outside
the observed first/last beat span gives null relations; no grid is extrapolated.
At the final observed beat, the preceding interval ends at phase 1.
`source.offsetSeconds` converts excerpt time to song time without moving the
provided lyric/phone/note boundaries.

## Interpretation

Low/mid/high refer to frequency bands, not kick/snare/hi-hat or singer identity.
`fluxShares` use positive magnitude differences, not the `acoustics` layer's
power shares. `strength` is relative to the excerpt's maximum raw flux; it is
not a probability and cannot be compared as absolute intensity across crops.
`sourceKind: isolated-vocal` remains an unverified caller declaration.

Candidate count is not actual performed-hit count, rap syllable count or breath
count. A pitch/timbre change can produce a candidate; a legato or overlapping
attack can be missed. Timing and beat-estimation errors combine, so offsets do
not certify a musician's intentional ahead/behind placement or swing ratio.
The 20ms frame spacing is measurement resolution, not a 20ms accuracy claim.
Centered windows, smoothing and excerpt boundaries affect candidate times.

`method` returns the selected peak recipe and frontend definition. The first
frame only establishes a reference because no preceding audio is available.
The selected recipe also excludes reflected FFT edge context and its smoothing
support: no candidates in the first 63.22ms or last 43.22ms (the first eligible
50Hz frame is at 80ms). These margins are unobserved for attacks, not silence.
Quiet frames are gated by actual RMS. Existing waveform rises/regions,
spectral texture, beats and supplied vocal timing remain separate evidence.
The browser uses its exact pre-existing gate/recipe through the same helper;
different FFT/hop/windowing means browser/server candidates need not match.

## Evaluation scope

Pilot → frozen confirmation → close is recorded in `TASKS.md`; local raw
evidence stays under `evidence/attack-beat-13/`. Known synthetic starts test
candidate timing and false detections. Existing waveform region/rise starts
are a capability comparator, not a competing onset model. Real recordings
check old-layer preservation, cost and actual MCP execution; they do not have
new hand-annotated onset truth here. Hosted Sites execution and whole-isolate
peak memory remain unverified.
