# Waveform dynamics through MCP

`analyze_music_timing` also returns `waveform`: recorded signal energy over
time, energetic-region boundaries and sustained rises/falls. It reuses decoded
PCM on the server; no new model, API or browser is required. The existing
beat/downbeat estimator and supplied phonetic/note times remain unchanged.

## Signal identity and clocks

`sourceKind` defaults to `mixture`. With this input, a louder section could be
voice, drums, accompaniment or several sources. `isolated-vocal` declares that
the caller supplied a vocal track; the server does not verify or separate it.
Room noise, reverb, breath sounds and separation artifacts can still affect it.

Every time is relative to the excerpt; add `source.offsetSeconds` for song time.
These are energy observations, not phoneme/voicing/note boundaries. A consonant
can precede periodic voicing; an energy region can contain many syllables.
Never silently move supplied lyrics to a detected energy boundary.

## Measurements

- Analyze centered20ms windows every10ms. Sample bounds use the actual sample
  rate, nearest-sample rounding and available samples at cropped edges; no
  artificial zero padding. These are analysis resolutions, not accuracy claims.
- Raw RMS includes DC; dBFS is20log10(RMS), rounded0.01dB. Exact zero becomes
  null. Whole-clip sample peak is not true peak; RMS is not LUFS, perceived
  loudness, physical vocal effort or emotion.
- Entry threshold=max(-60dBFS, strongest frame minus40dB); release threshold
  is6dB lower. Two consecutive frames establish activity; three below release
  finish it, with the edge backdated to the first qualifying frame. Quiet
  content below this criterion is not proved silent or voiceless. Different
  crops can change the clip-relative threshold.
- Rise/fall observations use100ms bins of frame power. Same-direction changes
  must sustain at least200ms and total at least3dB; zero bins do not connect
  unrelated regions. They are sustained changes, not a label for every attack.

`waveformDetail: "summary"` returns a100ms sampled curve by default. `"fine"`
returns every10ms point (up to3000). Both modes use the identical internal
analysis and produce identical event candidates; summaries can miss peaks
between displayed samples. Curve time=`startSeconds + index * stepSeconds`.

`activity.regions` gives start/end, peak time/level and `startClipped` /
`endClipped`. A clipped edge cannot establish the real beginning/end of the
sound. Region and rise/fall arrays each retain at most64 entries with true
total/truncated counts. A truncated list is not complete event coverage.

`context.items` links the first16 retained regions to nearest detected beats
and overlapping supplied vocal-unit/note indices; `context.total` counts the
retained regions and `context.truncated` marks omitted links. All64 retained
energy regions remain available. Clipped region edges have no start/end beat
link. Overlap index lists retain at most8 items with total/truncated; look up
the original supplied arrays. For later detailed links, analyze a smaller
excerpt with an explicit offset. These links do not establish lyric-to-pitch
identity or introduce new phonetic alignment.

## Interpretation

For a declared vocal track, examine region entry, recorded level growth,
observed peak and decay together with existing beat/pitch/lyric evidence. For
a mixture, describe whole-signal energy changes. Do not turn absent energetic
regions into a claim of no singing, assign breaths from amplitude alone, or
claim a singer became more forceful because mastering made a segment louder.

## Validation and practical cost

Three frozen recordings (one10.25s vocal excerpt, two30s mixtures) retained
exact PCM provenance, beat/downbeat arrays and supplied phonetic links through
actual compiled-Worker MCP requests. All2,432 exported-curve comparisons match
independent direct RMS arithmetic (703MCP summary,1,026MCP fine,703Node summary;
these are repeated representations of three recordings, not independent songs).
Synthetic gates meet the40ms criterion; amplitude doubling yields6.02dB.

Waveform-only Node measurements were28.82ms,46.05ms and47.91ms, one call per
recording with no warm-up. They exclude decode, context, serialization and
model work and do not establish hosted latency. Paired whole-request timings
are noisy single runs; no speedup claim. No new model or inference pass.

The original dense default result added81,750bytes after MCP text/structured
serialization, failing the50KB criterion. Capping only optional context links
at16 reduced it to41,980bytes while preserving all64 retained energy regions
and changes. Original failure is retained; thresholds and numeric events were
not tuned. Fine curves and total responses containing supplied text can be
larger;50KB is the default added waveform guard on the fixed dense fixtures.

Final80tests, typecheck/build and6-case actual MCP acceptance pass. Local
receipts: `evidence/waveform-dynamics-11/`. Source acquisition is unchanged.
Functional fixtures and exact RMS arithmetic do not establish real-song
phoneme or vocal-activity accuracy. Hosted Sites deployment/team connection
and whole-isolate memory remain separately unverified.
