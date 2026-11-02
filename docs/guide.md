# Music Lens: full guide

The [README](README.en.md) gives the overview and a quick start. This page keeps the full reference that used to be the README: every server tool, the browser analysis, connecting over MCP, the scope of input and accuracy, and development notes.

A program that pulls time, beat, harmony, note, word and energy cues out of audio and hands them to an **LLM as evidence for music analysis**. The same material can be reused for composition references, style reading and video direction. The goal is **a tool where the Sites server does the analysis and the AI team receives the results over MCP**. It is being built so that it needs neither a personal model API key nor an open browser.

The MCP tool `analyze_music_timing` now analyzes the audio on the server and returns beat and downbeat candidates and the BPM. It also links supplied pronunciation/syllable times to beats and notes. A real local Worker and MCP call were verified; publishing on Sites and connecting the team are not yet verified. Automatic forced alignment of syllables is not supported, because it currently exceeds the server's memory limit. See [how to call it, comparison results and limits](server-timing.md).

**Waveform dynamics:** the same server tool analyzes 20 ms windows every 10 ms and returns RMS, onset/offset candidates for sounding regions, peaks and sustained rises and falls. The default curve is every 100 ms; `waveformDetail: "fine"` returns the 10 ms curve. The default is a mixed signal; `sourceKind: "isolated-vocal"` is a setting in which the user states that the input is an isolated vocal. It is not automatic vocal separation or syllable detection. [Measurement and interpretation](waveform-dynamics.md).

**Spectral texture:** `acoustics` reuses the existing beat FFT and returns, every second, the power share of low/mid/high bands, the spectral centroid, the 85% power boundary and spectral flatness. Quiet regions are marked "unknown". It works without any new model or API and is kept apart from instrument or emotion identification. [Definitions and verification scope](spectral-texture.md).

**Attacks against the beat:** `attacks` finds attack candidates from spectral change and returns whether each lands ahead of or behind the detected beat, where it falls between beats, and how many candidates there are per second. It shows up to 64 detailed events plus the total count and whether the list was cut. It does not decide drums or rap syllables, nor confirm swing. [Measurement and interpretation](attack-beat.md).

**Server vocal pitch:** given isolated-vocal PCM with `sourceKind: "isolated-vocal"`, `pitch` returns Hz, fractional MIDI and periodicity every 20 ms, plus a note-name summary. Unknown values and both ends of the file are null, and it is off for mixed signals. It uses no new model or API; on four separate vocals, the 50-cent pitch frame F1 went from **51.53% → 80.99%** against the earlier fast analysis. False detections in unvoiced regions and octave errors increased, and it is neither automatic note segmentation nor score transcription. [Comparison conditions and limits](server-vocal-pitch.md).

**Whole-song transitions:** gathering each 30-second analysis's `structurePacket` into `analyze_music_structure` compares transition candidates, spectral/loudness changes and unanalyzed regions across up to 10 minutes. On a separate synthetic check it found all 7 transitions and returned 2 extra candidates. Transitions at equal loudness went from 0/5 with the earlier loudness cue to 5/5. Repeated sections fell short of the evaluation bar, so they are withheld from the default output; experimental candidates come only with `includeExperimentalRepetitions: true`. It does not label chorus/verse or guarantee accuracy across real songs. [Comparison results including failures, and how to call it](music-structure.md).

**A timeline across segments:** `compose_music_timeline` ties the beats, downbeats and vocal pitch of existing analyses to whole-song time and returns only the range you ask for. Duplicates of the same material are removed, while differing estimates and unknown values in overlapping segments are kept per source. It runs no new model, does not change pitch accuracy and does not fill gaps. [How to call it, page boundaries and limits](music-continuity.md).

Project repository: [Song-Seng-Hun/llm_music_lens](https://github.com/Song-Seng-Hun/llm_music_lens). It keeps the source, tests, install scripts, licenses and the MIT Beat This model (about 9.4 MB) that the server needs to run. Other research models, audio, ground-truth annotations, run logs and raw experiment data are not committed, and verification records stay local. A summary of measurements is in [research status and limits](research-status.md).

**Mac analysis server on the local network (`lan/`):** a server that lets AI teammates on the same router use, through signed requests, the Mac's Music Lens local (0.2.2, Whisper large-v3-turbo) and the rhythm analysis worker (0.5.0: beat, BPM, key, chords, energy, sections, melody summary, a song-structure overview based on lyric repetition, and a Discogs genre estimate). It uses only the Python 3.9 standard library and runs on the Mac, separately from this web app. Setup, API and limits are in [lan/README.md](../lan/README.md), and the skill for the Windows team is in [lan/docs/skill/music-lens-remote/SKILL.md](../lan/docs/skill/music-lens-remote/SKILL.md). It was checked with 8 real J-pop songs; English and Korean songs are not yet verified.

## Run it now

Run `Start-MusicLens.cmd` in this folder and open `http://127.0.0.1:5173/`. Stop it with Ctrl+C in the terminal it started. There is no auto-start, no always-on service and no firewall change.

```powershell
& .\Start-MusicLens.ps1
```

Node.js 22.13 or later is required. In a new working folder, prepare it in this order, using a GitHub account that has access to the repository.

```sh
git clone https://github.com/Song-Seng-Hun/llm_music_lens.git
cd llm_music_lens/web
npm run install:ci
npm run build
npm run db:local
npm run dev -- --host 127.0.0.1
```

The build prepares the default note model, WASM and analysis worker from the installed dependencies. The optional SOME solo-voice model is separate; when you need it, run the [preparation steps](vocal-specialist.md#prepare) and build again. `.openai/hosting.json` is a Sites binding declaration with no secrets, so it is in the repository; run profiles, the local DB and environment files are not. If the Windows npm shim fails, you can call the same scripts through the installed Node's actual `npm-cli.js`.

## What works now

**Better solo-voice note accuracy:** in detailed analysis you can choose **Note target → Solo · vocal only (SOME)**. It keeps fractional pitch and start/end for unaccompanied vocals and records null where no confidence is given. On two unused solo songs, note F1 went from 48.56% to 71.04% (onset 50 ms, pitch 50 cents; not a generalization to all singing). The model is prepared on the current PC; installation, license and accuracy scope on other PCs are in the [solo-voice analysis guide](vocal-specialist.md). The existing polyphonic analysis stays the default.

**Continuous pitch:** solo mode also keeps a curve at about 11.61 ms intervals. Look at the current 20 seconds in the **Pitch · Harmony** tab, or query the original frames through `pitchContour` in the JSON or in MCP `get_music_timeline`. If a query was cut, continue from `nextStart` to the same end time. A null is the model's unvoiced estimate, not a real silence judgement. It is evidence for looking at vibrato and slides, but automatic classification accuracy is not yet verified. Existing installs should rerun the model preparation and the build.

1. **Detailed analysis:** choose a file → start detailed analysis. It computes BPM, the beat grid and half/double-tempo candidates, low/mid/high attacks, chord and key candidates, the start/end/MIDI pitch of polyphonic notes, and energy changes. With no file, you can run a 16-second synthetic demo.
2. **Optional word transcription:** choose Whisper Base/Small and a language to estimate start and end times per word. It is a draft for lyrics and rap analysis and can be wrong depending on the accompaniment and pronunciation.
3. **Analysis cues:** accents and off-beats on the beat, per-band attack candidates, build-ups, drop returns and energy falls, words per 5 seconds and Hangul character syllables, the observed range and pitch-class distribution. It also looks up the offset from each word to the nearest beat, and the notes/chords at the same time.
4. **Check against the original:** piano roll, clicking words and change regions, playing and seeking the original audio. When you reattach the same audio to a saved result, the file's SHA-256 is compared. Cancelling keeps the existing result.
5. **Hand-off to the AI team:** JSON, a Markdown summary, an "AI team hand-off" note, or the MCP below. Evidence per time range, models and methods, truncation, and failures/not-run steps are sent together. The on-screen summary and the hand-off note do not replace the full JSON.
6. **Save/import:** a per-user library for signed-in users, up to 1.9 MB of UTF-8 JSON. Audio is not stored. Saved or imported material is client-supplied data and is not promoted to independent verification.
7. **Fast mode / YouTube reference:** fast mode is the earlier model-free signal analysis. For YouTube, only checking a reference address and linking to the original are supported; an address alone does not download or analyze audio.

**Better tempo and acoustic cues:** tempo candidates are compared by how many real attacks they catch and how well the grid is supported; for ambiguous or changing tempo, a fixed whole-song beat is withheld. Long silences are not filled with beats. In "Analysis cues" you can see per-second spectral centroid/flatness, band energy shares, attack density and RMS, and they are also included in MCP and the hand-off note. Structural change is judged from 2-second energy, separately from the decay of single attacks.

For browser analysis, the note model and WASM runtime are downloaded from the site, and a chosen lyrics model from Hugging Face. Audio analyzed in the browser never leaves the browser, while server MCP analysis processes PCM sent by the caller on the server. The first lyrics run can need a download of several hundred MB and several minutes or more of computation. Just opening the page does not download models.

## Connecting with ChatGPT without a key

The results of the Sites server's analysis are **interpreted by a connected ChatGPT inside the conversation**. The MCP below supports server rhythm analysis of new audio and lookup of saved results. Sites' official MCP connection is separate from model API keys. The site does not call ChatGPT's internal models directly.

| MCP tool | Read-only behavior |
|---|---|
| analyze_music_timing | Server beats, downbeats and BPM for 1–30 s of PCM, waveform dynamics, spectral texture, attack candidates with their time against the beat and density; continuous pitch for isolated-vocal input; links supplied pronunciations/syllables and notes. Stores no audio or results |
| analyze_music_structure | Compares 1-second feature packets of a song up to 10 minutes: transition candidates, loudness/spectral change, unanalyzed regions |
| compose_music_timeline | Combines beats and pitch from analyzed segments in song time, removing duplicates, keeping per-source overlaps and unconfirmed values, readable in pages with shared boundaries |
| list_music_reports | Up to 30 recent analyses of the authenticated user |
| get_music_report | Summary, sources, range/pitch classes, tempo evidence, acoustic summary, model and stage status, and limits |
| get_music_timeline | Over a range of up to 120 s: words, beat offsets, simultaneous notes/chords, attacks, energy, changes, density, per-second timbre/band shares, per-section tempo; 200 per layer, with total/truncated shown |

After publishing on Sites, install/connect it as the personal plugin the platform provides and confirm real lookups. No arbitrary local OAuth or separate model key is added to the platform-managed connection. Whether each AI can see the same user's reports must be confirmed through a real authenticated connection. Service access without a user is not per-user report access.

There is no shared URL yet. Before publishing, results can be handed over as files. `skills/music-lens/SKILL.md` is a draft usage guide and is not installed globally.

"Local login" on the local screen is Seedy, the Sites starter's virtual development account, not a real ChatGPT account login. Do not expose the development server to other PCs or deploy local development cookies as team authentication.

## Scope of input and accuracy

- Files: up to 12 MiB / 10 minutes, in whichever of MP3/WAV/FLAC/OGG/M4A/AAC/WEBM the browser decodes. Rhythm and polyphony use 22.05 kHz mono, solo SOME uses 44.1 kHz mono, transcription and fast mode use 16 kHz. Playback uses the original.
- Rhythm: beat candidates compared by how well spectral flux agrees with real attack times. "supported" means a repeating period that explains the observed attacks, not a probability of being musically correct. ambiguous/variable/insufficient withhold the BPM and the whole-song grid and leave candidate/segment evidence. Half and double tempo remain possible, and accents/off-beats are not a time signature or downbeat judgement.
- Timbre: spectral centroid, 85% power rolloff, power flatness and low/mid/high power shares of the mixed signal, per 1-second segment. Silence is null. The RMS/active-region amplitude range is not LUFS, and the sample peak is not a true peak. Because the analysis is mono, stereo width, phase and frequencies above 11.025 kHz are not covered. Instrument, genre and emotion are not decided automatically.
- Attacks: per-band onsets. A low attack is not confirmed as a kick, nor a high attack as a hi-hat. Build-ups/drops are candidates from energy and attack change and do not guarantee song structure or intent.
- Notes: Spotify Basic Pitch, 12-second windows with 1-second overlap, up to 4,000 notes. The last input gets 1 second of silent context appended, and results are limited to the original analysis length (`tail-context-v1`). In mixed signals, accompaniment, overtones and false detections mix in. It is not a real score or vocal melody, and activation is not a calibrated probability.
- Chords: in polyphonic mode, major/minor triads and 7/maj7/m7 are told apart from note combinations that actually overlapped. Examples: C = C·E·G, Cm = C·D#·G, D7 = D·F#·A·C. Time per segment and the observed pitch classes are exported together. Broken chords that did not sound at the same time are not confirmed as chords at this stage. If the polyphonic note model fails, only spectral triad candidates are marked `spectral-only`, and no chord tones are made up. Solo mode does not guess accompaniment chords, and the key is estimated only from the duration of melody notes.
- Key: shown as `C# minor`, kept apart from a note name like C# or a chord like C#m. A 12-tone distribution weighted by note duration and model activation is compared with 24 major/minor profiles. It follows [music21's Krumhansl-Kessler profiles/correlation approach](https://music21.org/music21docs/_modules/music21/analysis/discrete.html); the extra withholding rules are unverified product heuristics. A single chord, too few notes, close candidates or an incomplete transcription withhold the representative key. Candidate correlations are not accuracy probabilities. Modulation, modes, complex extended chords and the score spelling of enharmonics are unconfirmed.
- Lyrics: Whisper is a speech transcription model. It can hallucinate or miss words in singing, fast rap and multiple vocals. 25-second windows, 256 generated tokens per window, up to 2,000 words; words at boundaries can be lost. Word timestamps are not phoneme forced alignment. Word density and Hangul character syllable counts are proxies for actual pronunciation density.
- Notes at the same time as a word may be accompaniment. Confirmed lyric-to-vocal-note matching, source separation, instrument classification and automatic rap classification are not provided. An empty result is not a judgement of silence or no vocals.
- Style and strengths are for the LLM to interpret, citing sources and time ranges. If it has not heard the actual audio, it must not say it has. Every result is `accuracy: unvalidated`; runs on synthetic audio and public speech are not real-song accuracy checks.

Model/runtime versions: Basic Pitch 1.0.1, TFJS/WASM 3.21.0, Transformers.js 3.8.1, ONNX Runtime Web 1.22.0-dev.20250409-89f8206ba4. Whisper's pinned revision is in `web/analysis/models.mjs`, the SHA-256 of static files in `web/public/models/manifest.json`, and licenses in `web/public/licenses/`. Basic Pitch and Whisper Base were run in a real browser; Small and the accuracy on Korean singing/rap are not yet verified.

## Development and verification

Run these in `web/`.

```powershell
npm run typecheck
npm test
npm run build
npm run db:local
npm run dev -- --host 127.0.0.1
```

`db:local` creates only the initial schema. The old `analysis_requests` table and initial migration remain to preserve data but are not used by running code. The model-call path, key input and `analyze_youtube` were removed. Dropping the table later is a separate data migration.

Records of the chord/key and rhythm/timbre checks, and of earlier versions, are kept locally and apart. `web/tests/browser-precision.cjs` checks the real model/UI/MCP, and `web/tests/browser-lyrics.cjs` checks a transcription run with public speech. `MUSIC_EVIDENCE_DIR` sets the results folder, and `MUSIC_REAL_FIXTURE` a local music sample whose terms of use you have checked. `PLAYWRIGHT_MODULE` and `BROWSER_EXECUTABLE` reuse an existing install. `web/tests/precision-storage.mjs` checks large JSON, size rejection and user isolation, and cleans up only the IDs it created.

Real server path: `web/app/mcp/route.ts` → `web/lib/mcp.ts` → `web/lib/server/music-timing.ts` → `web/lib/server/beat-analysis.mjs`. [Server analysis guide](server-timing.md). The existing browser path is `web/components/music-lens.tsx` → `web/lib/browser-detail.ts` → `web/public/precision-worker.mjs` (source `web/analysis/precision-worker.mjs`) and is kept. The optional `detail` of the shared report `web/lib/report.ts` preserves earlier JSON. Storage is `web/lib/server/reports.ts`.

Detailed tempo was replaced by a single `web/public/analysis/tempo-grid.mjs`. The acoustic summary aggregates measurements from the same FFT in `web/public/analysis/acoustics.mjs`. The earlier peak-autocorrelation selection function was removed, and old JSON can still be read even without the selection fields. Unconfirmed values in detailed results are not replaced with older signal values.

Chords and major/minor keys are computed by `web/public/analysis/harmony.mjs` after the note model, and `detail.harmony` is the source of truth. The compatibility fields `rhythm.key/chords` project the same result. The calculation that used the strongest triad as the whole-song key was removed. Check the evidence in the UI's **Pitch · Harmony** tab, JSON/Markdown, the AI team hand-off note and the MCP summary/timeline. This feature is produced by rerunning detailed analysis and does not quietly recompute past reports.

A real wind-quartet performance with ground-truth notes serves as the reference sample. It is public CC BY 4.0 material, and its audio, individual parts, score MIDI/MusicXML and note annotations stay local. Measured once with the current browser analyzer over 57 seconds and 231 reference notes, note onset/pitch F1 was 50.47% (50 ms / 50 cents), with 39.56% precision and 69.70% recall. Conditions, raw results and limits are kept in local records. This one song cannot support claims of general real-song accuracy or improvement, and lyrics, rap and drum accuracy were not evaluated. The next improvement keeps this baseline and fixes the comparison conditions and budget first.

Follow-up diagnosis on 2026-10-14: a candidate that suppresses weak onset signals of sustained notes was compared on the same model output. F1 went 50.47→55.17% and unmatched notes 246→189, but it fell short of the pre-set adoption bar (+5 points), so it was reverted. Candidates, error classes and results are kept in local records. The bar was not changed or retuned after the fact.

The same day, three candidates extended to two songs also failed the bars for improvement or for keeping note recall, so the default decoding was kept. Separately, a **truncation of the last note's length** that was found was fixed. On two synthetic sounds in a real browser, the missing tail went 24.8→1.5 ms and 97.8→0 ms, and rounding past a file end with a fractional length was also prevented. This is kept apart from any general real-song accuracy gain.

The first 60 seconds of vocal and harmony tracks that a creator officially released are also prepared for local performance tests. The audio is not in the repository; it is lossless with the same PCM as the original and fits within the existing 12 MiB input limit. This audio has no ground-truth notes or lyric alignment, so running the analysis is kept apart from scoring accuracy.
