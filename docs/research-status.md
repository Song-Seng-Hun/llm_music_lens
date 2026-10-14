# Measured research status

As of 2026-10-14. This is a summary of retained local experiments, not a claim of general music-transcription accuracy. The private repository contains the program, tests and preparation scripts. Downloaded recordings, annotations, model weights, browser traces and experimental outputs remain in the original local working directory and are excluded from Git. They were not deleted by repository setup.

## Adopted program

- Default mixed/polyphonic notes: Spotify Basic Pitch1.0.1. Fractional pitches, duration evidence, bounded results and uncertainty remain explicit. A ChoraleBricks AN1 four-part wind performance pilot scored onset/pitch F1 50.47%, precision39.56%, recall69.70% (50ms/50cents,231reference events); one piece does not estimate general accuracy.
- Optional solo voice: SOME `some-0.0.1`, source SHA256 `9d55f9d239e9d70d1bf039506cf1c428b657df7bbb8b94104ca882af6b721e06`, current derived runtime SHA256 `7b5a4eb8e5b1486902e3918516020a8f0f78d0d22c02ebd4d5410b2dc60e8e75`. On two locally heldout vocadito human solo clips, onset/pitch F1 increased48.56→71.04% versus Basic Pitch. Native fractional MIDI and null confidence are preserved. This does not establish Vocaloid, mixed-song, rap or lyric accuracy. Upstream training overlap is unknown.
- The program also estimates tempo, beat support, tonal/chord candidates, onsets, acoustic features and optional Whisper word timings. The vocal-note experiments do not validate those separate outputs.

## Closed follow-ups, not adopted

On four already-seen vocadito clips1/10/11/12, current SOME macro onset/pitch F1 is67.208036%, pitch-frame F1 84.465891%, onset/pitch/offset F1 37.147974%. Offset matching additionally requires error<=max50ms/20%reference duration. These three metrics answer different questions.

| Local experiment | Result | Product decision |
|---|---|---|
| Larger GAME model, vocal-accuracy-03 | Onset/pitch and frame F1 declined on development clips | Retain SOME |
| Waveform edge/gap refinement, vocal-boundaries-04 | Neither fixed candidate changed real predictions | No adoption |
| SOME internal frame-rest edges, vocal-internals-05 | 1/155notes changed; frame F1 84.465891→84.533524%, offset-aware F1 unchanged37.147974%; required offset gain3pp not met | No adoption in that experiment; confirmation13–16 unopened at its closure |

The internal-output experiment restored original model bytes exactly after removing diagnostic descriptors; actual-browser original output and input hashes matched on all four clips. Independent review recomputed all scores and agreed with closure. Internal extraction alone is not accuracy improvement.

Local audit records: `evidence/vocal-specialist-02/REPORT.md`, `evidence/vocal-accuracy-03/`, `evidence/vocal-boundaries-04/REPORT.md`, `evidence/vocal-internals-05/REPORT.md`. These paths are absent from a clean clone. Do not manufacture a pass from their absence or recreate heldout results from this summary. Standard source tests are available through `npm test`; dataset evaluations require separately acquired, versioned inputs and frozen criteria.

## Small-gain practical adoption

On fresh vocadito13–16, the fixed exterior-rest trim improved A1 frame F1 **78.71763→80.02464%** and onset/pitch F1 **59.12985→59.65068%**; A2 mean frame F1 **79.0000→80.24531%**. Offset-aware F1 unchanged. The frozen value06 acceptance FAILED: clip14's A2 frame F1 declined0.12449pp. All original outputs, conditions and that failure remain recorded.

A separate, explicit post-result product decision accepted this secondary-annotation tradeoff for aggregate gains, conditional on bounded cost and preserved behavior. The unchanged eight-run cost plan passed: clip10 median14.6251→14.9066s (+1.92%), clip11 20.4199→20.4131s (-0.03%), including load/inference/decode/release. Original3outputs, both exposed tensors and final predictions matched the diagnostic reference exactly. Graph +87bytes, no extra model pass/dependency/API; total peak memory unmeasured. This justifies a small local product choice, not a universal accuracy or latency claim. Integration uses one updated solo-vocal decoder; prior code remains in Git.

Evidence: `evidence/vocal-value-06/`, `evidence/vocal-value-cost-07/`. The original internals05/value06 gate failures are not relabeled as passes. See [solo-vocal preparation and limits](vocal-specialist.md) for model migration and exact scope.

## Continuous vocal contour

Native frame pitch/rest, alongside unchanged note events, is now exposed through JSON, the notes UI and paged timeline queries. No additional model pass, dependency or API. Four fresh local clips17-20 vs F0: macro50-cent frameF1 **67.95537->71.09609%**,20-cent **40.44437->43.88666%**. All four improve both, but false-voiced frames increase2/1 on19/20. Baseline is the existing flat-per-note summary, not a competing pitch tracker. Training overlap unknown; no general-song or vibrato/slide-classification claim.

Eight fixed paired browser runs preserved native outputs/notes and passed overhead gates: **+1.50246%/+224.4ms**, **+0.60564%/+116.45ms**. Added model descriptors41bytes over prior runtime; output4bytes/frame; peak memory unmeasured. Keep native nulls, bounded52,000samples,200sample timeline pages and unchanged report byte cap. E1 evaluator tie repair before confirmation and C1 exact-EOF repair retain original evidence; C1 replay changes none of the four scores. Evidence: `evidence/vocal-contour-08/`; detailed schema/limits in [solo-vocal guide](vocal-specialist.md#continuous-pitch-evidence-2026-10-14).

## Sources and assets

- [vocadito v3](https://zenodo.org/records/5578807): human unaccompanied singing and two musician annotations.
- [ChoraleBricks1.0.1](https://zenodo.org/records/15463260): wind quartet performance/reference data, CC BY4.0. Local mixtures preserve source timing; score MIDI is not assumed to be aligned performance truth.
- [SOME preparation and license](vocal-specialist.md): code and weights have separate licenses; released weights are CC BY-NC-SA4.0. Weights are downloaded explicitly, never in this Git history.
- PinocchioP/Yamaha excerpts were local investigation inputs. No creator-aligned PinocchioP note ground truth was verified. No sample audio or proprietary annotation is redistributed with this repository.

Future comparison follows Pilot→Confirm→Close with frozen splits, metrics, candidates, budget and a meaningful-gain threshold. Existing failures remain failures; deployment and real team MCP authentication still require separate verification.
