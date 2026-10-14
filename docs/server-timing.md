# Server music timing

`POST /mcp` → `analyze_music_timing` → `lib/server/music-timing.ts` →
`lib/server/beat-analysis.mjs`. The actual tool executes the model on the server;
it does not launch a browser, call a paid API or depend on a separate local
inference service. The same built Worker is the Sites deployment target.
Local workerd/MCP execution is verified; hosted Sites publication, whole-isolate
128MiB peak and team connection are not yet verified.

## Input and result

Send standard base64 of **signed16 little-endian, mono,22050Hz PCM**,1–30seconds:

```json
{
  "audio": { "encoding": "pcm_s16le", "sampleRate": 22050, "channels": 1, "data": "<base64 PCM bytes>" },
  "offsetSeconds": 0
}
```

An existing audio converter can prepare this input, for example:
`ffmpeg -i song.wav -t 30 -ac 1 -ar 22050 -f s16le excerpt.pcm`.
Pass file bytes through a tool/HTTP client rather than pasting audio into an LLM
conversation. This program does not download YouTube or arbitrary URLs.

`source.sha256` identifies the actual decoded PCM16 bytes. All returned times
are excerpt-relative; add `source.offsetSeconds` for song time. Offset plus
excerpt length cannot exceed600seconds. Independent excerpts can disagree at
their edges: they are not a verified whole-song meter track.

Results include detected beats, downbeat candidates, median-interval BPM,
counts of detected beats between downbeat candidates, model/recipe hashes and
warnings. `meter` remains null: a missed downbeat can combine several bars.
Neither peak strength nor model agreement is a calibrated confidence.

The same call returns `waveform`: short-time RMS, energy-region boundaries,
peaks and sustained rises/falls, with beat and supplied-unit/note references.
`sourceKind` defaults to `mixture`; `isolated-vocal` is caller attribution, not
automatic separation or identification. `waveformDetail` chooses `summary`
(100ms output curve) or `fine` (10ms); internal20ms/10ms analysis stays the same.
Clipped edges are not true vocal starts/ends. See [waveform dynamics](waveform-dynamics.md)
for thresholds, clocks, bounded output and interpretation.

`acoustics` adds bounded1second spectral texture from the same FFT: low/mid/high
power shares, magnitude centroid,85%power rolloff and power flatness. Below the
-80dBFS RMS gate values are unknown. No extra inference, instrument label or
noise probability is implied. See [spectral texture](spectral-texture.md) for
frequency boundaries, averaging, padding and verification limits.

Optional `timedVocal` accepts `{unit,source,items:[{start,end,text}]}` with
unit `phone`, `mora`, `syllable` or `word`, at most256items. Optional `noteEvents`
accepts `{source,mode,items:[{start,end,midi}]}`, mode `solo-vocal` or `polyphonic`,
at most512items. Both use the current excerpt clock. The output preserves
supplied boundaries, adds beat/downbeat offsets, beat phase and bounded note
overlap, and counts supplied units per second. It never invents syllable times
by dividing words. Caller source labels are attribution, not verification;
overlap does not establish that a mixture note belongs to a sung syllable.

No audio/results are saved by this tool. Sites identity is required for calls;
saved-report tools retain their existing owner checks. Concurrent heavy calls
within an isolate receive a retryable busy error instead of an unbounded queue.
No automatic fallback to old estimates after a model error.

## Measured practical comparison (2026-10-14)

One fixed candidate: small0, float32 ONNX,5second windows,6frame borders,
238frame stride, official minimal decoder, current JS waveform frontend.
Twelve previously inspected GTZAN tracks: first three each of blues,classical,
hiphop,rock; same native22050Hz PCM16 first30seconds for both methods. Annotated
events in that interval retained. Frozen inclusive70ms one-to-one F1 scorer;
no tuning, failed-case removal or metric substitution after predictions.

| Metric | Previous spectral grid | Actual new MCP recipe |
|---|---:|---:|
| Beat macro F1 |18.3250%|79.4279%|
| Beat pooled F1 |21.7335%|80.2941%|
| Beat precision |95.4545%|80.8889%|
| Beat recall |12.2628%|79.7080%|
| Beat TP/FP/FN |84/4/601|546/129/139|
| Downbeat macro F1 |not produced|58.3322%|
| Downbeat TP/FP/FN |not produced|114/126/60|

11tracks improved; blues00001 worsened86.57→45.36%. The previous estimator
abstained on9/12: much of the gain is recovered coverage, with more false
positives. Downbeat precision47.5% makes certified-bar claims inappropriate.
Close: **meaningful improvement for this fixed practical retrieval question**,
not improvement on every dimension/song or general transcription accuracy.

All12 actual MCP responses matched every frozen candidate beat/downbeat and
PCM identity. Recorded per-case runner median3.157s, total39.071s for12×30s
audio; this includes local request preparation, not pure CPU timing. Standalone
30s workerd test5.01s; WASM capacity69.1875MiB, not whole-isolate peak.

The strict JS-vs-official feature-equivalence gate remains **failed**:
2/2,305,536values exceeded its unchanged tolerance after one fixed-coefficient
repair. Thus this evaluates the complete JS recipe against annotations and does
not claim numerical equivalence to the official frontend. Small0 excludes
GTZAN training per upstream, but this inspected subset is not a fresh unbiased
generalization study. The earlier30s-context pilot is a different recipe.

Local frozen inputs, predictions, failures, scores and independent review:
`evidence/timing-integration-10/`. Not included in a clean clone.
Reusable data acquisition/comparison scripts: `research/timing/comparison/`.

## Alignment limit

TIFA float alignment runs through `research/timing/alignment/align.py` on an
explicit local file with known phones; it does not require an always-on server.
Its output can supply `timedVocal`. Oracle pronunciation and training overlap
remain limitations; it is not an end-to-end ASR improvement claim.

Actual Sites-style resource trials failed: int8 required146.9375MiB WASM at
session creation; int4 ran inference but grew to160.375MiB. Both exceed128MiB
before other isolate overhead. Therefore **automatic acoustic syllable
alignment is not installed in the Sites server**. No pretend equal-division
replacement was installed. An actual MCP call can still link supplied alignment
to audio-derived beats, while reporting this missing acoustic stage.

## Packaging

The MIT small0-derived9,426,750byte ONNX is tracked under `models/beat-this/`.
`prepareServerBeat()` checks all model/runtime hashes and generates the
project-local loader. Vite packages ONNX as Data and WASM as CompiledWasm;
no Python or runtime WASM compilation is required on Sites.

The450MiB optional browser-only SOME model remains on local disk and works in
local Vite preview. `public/.assetsignore` excludes it from Workers static
deployment because it exceeds the25MiB per-asset limit. It is not a server note
model. Existing browser analysis and saved-report reads remain supported
consumers; their full migration is not claimed.
