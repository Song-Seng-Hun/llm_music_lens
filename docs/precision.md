# Detailed music evidence for LLMs

Approved continuation: local file analysis with no personal provider key; browser-first so the same UI can later run on Sites. Additive report `detail` with its own schema identifier preserves music-lens/1 records. The Site server stores bounded result JSON and serves owner-scoped tools; it never executes a heavyweight ML model.

## Data path
File -> browser decode -> 22,050 Hz mono -> rhythm/harmony worker -> Basic Pitch note events -> optional 16,000 Hz Whisper transcription -> validated detail -> report UI/storage/MCP. Workers are terminated on cancellation or file replacement. Model/runtime identifiers and stage status travel with each result. Downloads are public static model files, not audio uploads.

The rhythm layer holds beats and half/double candidates, frequency-band onsets, energy envelopes and section-change candidates. No guaranteed kick/snare identity or automatic meter claim. Harmony is a chroma-template estimate, not an authoritative score. Basic Pitch works best on a single instrument; full-song mixtures require careful review. Whisper is speech recognition: sung/overlapped lyrics and rap are error-prone; word timing is estimated, not forced alignment. Unavailable stages say failed/skipped, never silence or completion.

## LLM evidence path
Latest user clarification: general music evidence for LLMs. Existing get_music_timeline returns synchronized lyrics/word timestamps, beat candidates and offsets, notes/chord candidates, band attacks and energy. Additional features: accent/offbeat candidates, spectral-band impacts, energy rise/drop-return/fall candidates, transcript-derived density and pitch classes. Simultaneous notes are NOT assigned as the pitch of the word/voice. Composition reference, musical interpretation and animation are downstream uses. Keep short, queryable evidence with source hash, method, timestamp bounds, status and limits.

## Scope and verification
12 MiB/600-second source bound retained. Process note models in short overlapping chunks; cap exported events with visible truncation. Dense-input fixture measured 1,085,973 bytes: raise storage/import/request cap from 1 MB to 1.9 MB, under D1's 2,000,000-byte row limit including metadata headroom. Oversize stays refused. Model failure retains its error and completed evidence; no silent fallback. No automatic download on page load. No added API keys, media downloader, startup service, global skill or app setting.

Functional pilot: synthetic click/chord fixtures; public speech fixture for actual Whisper execution (origin/hash in evidence/precision/speech-source.json). Check timing tolerances, silence abstention, actual model inference, UI controls, stale-result cancellation, round-trip storage and bounded tool output. Speech is not a song/rap benchmark. Compare no performance score without a fixed reference set. Close real-song accuracy as inconclusive under current data, with the actual implementation and checks recorded separately.

## Primary references
- Spotify Basic Pitch TS: https://github.com/spotify/basic-pitch-ts (browser audio-to-note implementation, Apache-2.0).
- Spotify Basic Pitch: https://github.com/spotify/basic-pitch (mixture/single-instrument limitations).
- Transformers.js ASR: https://huggingface.co/docs/transformers.js/v3.8.1/en/api/pipelines (word timestamps and chunked ASR).
- Whisper: https://github.com/openai/whisper (speech model, not a guarantee of correct sung lyrics).
- D1 limits: https://developers.cloudflare.com/d1/platform/limits/ (2,000,000-byte maximum row; 1.9 MB application cap leaves metadata headroom).

Versions, model revisions and actual downloaded bytes are recorded after dependency inspection. Exact-word/phoneme alignment, stem separation, large-model accuracy and automatic genre/taste judgments are not inferred from these mechanisms.
