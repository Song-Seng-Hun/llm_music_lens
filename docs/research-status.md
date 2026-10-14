# Measured research status

As of 2026-10-14. This summarizes retained experiments, not general music-transcription accuracy. The repository contains the program, tests, preparation scripts and the MIT small0-derived model needed by server inference. Downloaded recordings, annotations, other research model weights, traces and experimental outputs remain local and excluded from Git.

## Adopted program

- Server cost optimization: exact FFT twiddle reuse reduced paired frontend time54.96% (197.30ms/30s); default600s structure skips withheld recurrence, avoiding a1.44MB matrix. Actual local MCP median88.42->26.56ms for that default path, all60 analytical responses unchanged. Explicit experimental recurrence was slower (+3.75ms microbenchmark; about+19.77ms paired MCP), so the original combined guard FAILED. Adopted as a separate practical default-path tradeoff; no universal end-to-end speed or whole-memory claim. [Methods, failed guard and decision](server-optimization.md); local evidence `evidence/optimization-16/`.

- Server isolated-vocal pitch: CPU-only custom YIN-style contour,20ms Hz/fractional MIDI/nulls,70–1000Hz; no new API/model. Frozen vocadito21–24 confirmation versus unchanged quick pitch intervals: macro50c frameF1 **51.5280->80.9930%**, precision76.6938->82.1155%, recall39.6133->80.8953%. All12600duration rows retained, no alignment fitting. False voicing15.9535->23.9634%, gross error2.3232->4.4517% and octave error1.0354->1.9730% worsen; no per-metric universal win. The old server had no automatic pitch; this does not compare against SOME. Single normalized Node cost161.77–206.56ms/30s, dense whole-MCP added bytes71834. [Methods, failure history and limits](server-vocal-pitch.md); local evidence `evidence/server-pitch-14/`. Song-wide transition evidence is now implemented; recurrence stays explicit experimental opt-in after a failed full-phrase gate (see below).

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

## Timing extensions and server execution

The initial [timing pilot](timing-pilot.md) retained working Beat This and TIFA components without changing production. The follow-up now connects server Beat This to the actual `analyze_music_timing` MCP tool. Same12 waveform/annotation comparison: beat macroF1 18.3250→79.4279%;11 wins/1 loss, predominantly recovered coverage. Precision95.45→80.89%, recall12.26→79.71%. New downbeat macroF1 58.3322%. Actual MCP12/12 produces exactly the frozen candidate events and PCM identities. Median per-case runner3.157s/30s excerpt; WASM capacity69.1875MiB is not whole-isolate peak. TIFA int8/int4 both exceed128MiB in actual workerd; server acoustic syllable alignment stays unavailable. Supplied timed units can be linked to detected beats/notes. [Full methods, failures and limits](server-timing.md); local evidence `evidence/timing-integration-10/`. Hosted Sites deployment is not claimed.

## Server spectral attacks and beat relations

The actual `analyze_music_timing` path now returns positive spectral-change
candidates, signed offsets to observed beats, phase within actual beat
intervals, and one-second candidate density. It reuses the existing FFT;
there is no additional model or external inference. Candidate detail is
capped at 64 with total/truncated; density includes every candidate.

A bounded three-fixture pilot selected recipe B after removing one reflected
crop-edge false event. Before confirmation, six distinct 12-second synthetic
fixtures and a 60ms one-to-one matching tolerance were frozen. The three
positive fixtures scored **22/22 starts, zero false positives or misses**;
steady tone, gradual crescendo and silence produced zero detections. On the
14 sustained-background starts, recall rose from **0/14 energy cues to 14/14
spectral candidates**. The energy cues are a capability comparator, not a
competing onset model. This is not a real-song onset accuracy estimate.

Ten complete browser outputs/progress traces remain exact. On the fixed
three real excerpts, 449,920 model-input floats and 28,120 pre-existing
spectral scalars remain exact. Single paired Node frontend measurements
added 13.46ms for 10.25s and 42.47/8.83ms for 30s, within the frozen 100ms
local guard; peak selection and relation calculations were measured separately.
No speedup or general hosted-latency claim follows from these single pairs.
Three actual old/new compiled MCP calls preserve every prior result except
two diagnostic timers; added response bytes are 15,454 / 42,006 / 41,706,
including text and structured copies. A 290-candidate serialization fixture
with complete metadata adds 39,814 bytes, under the frozen 50,000-byte guard.
Final compiled MCP acceptance passes 8/8 checks; repository tests pass 93/93.

The first approximately 63ms and final 43ms lack sufficient unreflected
context and are not evaluated for attacks. Candidate counts are not drum or
rap-syllable counts; beat and attack estimation errors both affect offsets.
Bounded server vocal pitch and song-wide transition evidence are now implemented;
recurrence remains experimental after its failed confirmation gate.
Local evidence: `evidence/attack-beat-13/`; definitions: [attack/beat guide](attack-beat.md).

## Song-wide transitions; experimental recurrence (2026-10-14)

The new stateless MCP tool compares compact1s packets from actual timing calls across <=600s. Default returns transition candidates, observed contrasts and explicit coverage/gaps. Recurrence is withheld unless explicitly requested as experimental; withheld is not zero detections. No new model/API/dependency or browser requirement. [Contract, metrics and failure interpretation](music-structure.md).

Frozen B Confirm: six distinct synthetic clips,376s. Transitions macroF1 .885714,7TP/2FP/0FN; equal-energy recall5/5 vs0/5 unchanged waveform-change cues. Steady/drift/silence controls all0pairs/0transitions. Substantial phrase-pair retrieval fails the original F1>=.80 gate: .555556,3TP/4FP/1FN, macrorecall .833333. One long return merges two reference sections; four short motifs fall outside section labels. Original score/criteriaPass=false retained, no post-result rescoring. Default transition adoption is a restricted practical choice supported by its separately frozen gate; the original combined experiment did not pass. Clean full-section recurrence stays inconclusive under this data/budget.

One actual600s comparator call54.9471ms (guard500ms), fixed real-input extraction<=18.689ms/30s (guard25ms); all old frontend features/spectral frames exact on three inputs. Local evidence: `evidence/structure-15/`. Synthetic/constructed examples are not ground truth for the form of real songs. Whole-request speed, whole-isolate peak and hosted deployment remain unverified.

## Sources and assets

- [vocadito v3](https://zenodo.org/records/5578807): human unaccompanied singing and two musician annotations.
- [ChoraleBricks1.0.1](https://zenodo.org/records/15463260): wind quartet performance/reference data, CC BY4.0. Local mixtures preserve source timing; score MIDI is not assumed to be aligned performance truth.
- [SOME preparation and license](vocal-specialist.md): code and weights have separate licenses; released weights are CC BY-NC-SA4.0. Weights are downloaded explicitly, never in this Git history.
- PinocchioP/Yamaha excerpts were local investigation inputs. No creator-aligned PinocchioP note ground truth was verified. No sample audio or proprietary annotation is redistributed with this repository.

Future comparison follows Pilot→Confirm→Close with frozen splits, metrics, candidates, budget and a meaningful-gain threshold. Existing failures remain failures; deployment and real team MCP authentication still require separate verification.
