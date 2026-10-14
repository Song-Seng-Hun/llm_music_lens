# Measured research status

As of 2026-10-14. This is a summary of retained local experiments, not a claim of general music-transcription accuracy. The private repository contains the program, tests and preparation scripts. Downloaded recordings, annotations, model weights, browser traces and experimental outputs remain in the original local working directory and are excluded from Git. They were not deleted by repository setup.

## Adopted program

- Default mixed/polyphonic notes: Spotify Basic Pitch1.0.1. Fractional pitches, duration evidence, bounded results and uncertainty remain explicit. A ChoraleBricks AN1 four-part wind performance pilot scored onset/pitch F1 50.47%, precision39.56%, recall69.70% (50ms/50cents,231reference events); one piece does not estimate general accuracy.
- Optional solo voice: SOME `some-0.0.1`, model SHA256 `9d55f9d239e9d70d1bf039506cf1c428b657df7bbb8b94104ca882af6b721e06`. On two locally heldout vocadito human solo clips, onset/pitch F1 increased48.56→71.04% versus Basic Pitch. Native fractional MIDI and null confidence are preserved. This does not establish Vocaloid, mixed-song, rap or lyric accuracy. Upstream training overlap is unknown.
- The program also estimates tempo, beat support, tonal/chord candidates, onsets, acoustic features and optional Whisper word timings. The vocal-note experiments do not validate those separate outputs.

## Closed follow-ups, not adopted

On four already-seen vocadito clips1/10/11/12, current SOME macro onset/pitch F1 is67.208036%, pitch-frame F1 84.465891%, onset/pitch/offset F1 37.147974%. Offset matching additionally requires error<=max50ms/20%reference duration. These three metrics answer different questions.

| Local experiment | Result | Product decision |
|---|---|---|
| Larger GAME model, vocal-accuracy-03 | Onset/pitch and frame F1 declined on development clips | Retain SOME |
| Waveform edge/gap refinement, vocal-boundaries-04 | Neither fixed candidate changed real predictions | No adoption |
| SOME internal frame-rest edges, vocal-internals-05 | 1/155notes changed; frame F1 84.465891→84.533524%, offset-aware F1 unchanged37.147974%; required offset gain3pp not met | No adoption; confirmation13–16 unopened |

The internal-output experiment restored original model bytes exactly after removing diagnostic descriptors; actual-browser original output and input hashes matched on all four clips. Independent review recomputed all scores and agreed with closure. Internal extraction alone is not accuracy improvement.

Local audit records: `evidence/vocal-specialist-02/REPORT.md`, `evidence/vocal-accuracy-03/`, `evidence/vocal-boundaries-04/REPORT.md`, `evidence/vocal-internals-05/REPORT.md`. These paths are absent from a clean clone. Do not manufacture a pass from their absence or recreate heldout results from this summary. Standard source tests are available through `npm test`; dataset evaluations require separately acquired, versioned inputs and frozen criteria.

## Sources and assets

- [vocadito v3](https://zenodo.org/records/5578807): human unaccompanied singing and two musician annotations.
- [ChoraleBricks1.0.1](https://zenodo.org/records/15463260): wind quartet performance/reference data, CC BY4.0. Local mixtures preserve source timing; score MIDI is not assumed to be aligned performance truth.
- [SOME preparation and license](vocal-specialist.md): code and weights have separate licenses; released weights are CC BY-NC-SA4.0. Weights are downloaded explicitly, never in this Git history.
- PinocchioP/Yamaha excerpts were local investigation inputs. No creator-aligned PinocchioP note ground truth was verified. No sample audio or proprietary annotation is redistributed with this repository.

Future comparison follows Pilot→Confirm→Close with frozen splits, metrics, candidates, budget and a meaningful-gain threshold. Existing failures remain failures; deployment and real team MCP authentication still require separate verification.
