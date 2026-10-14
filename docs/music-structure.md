# Song-wide repetition and transition evidence

`analyze_music_timing` returns `structurePacket` beside its unchanged analysis layers. Retain packets from consecutive excerpts with their actual `offsetSeconds`, then call:

```json
{"name":"analyze_music_structure","arguments":{"duration":120,"packets":["replace with actual packet objects"]}}
```

The strings above are placeholders, not valid packets. Default output is coverage and transition evidence. Recurrence did not pass the frozen full-phrase quality gate: `repetitionEvidence.status` is `withheld`, and repetition arrays/totals are omitted, never empty-as-absence. Explicit `includeExperimentalRepetitions: true` returns experimental candidates with the failed-gate reason adjacent to them. This is a post-result product restriction, not a changed experiment or a claim that the original gate passed.

The tool takes 1–60 packet objects, <=600 seconds total, each from 1–30 seconds of mono22050Hz PCM. No browser, new model/API, audio storage or persistent server job. The existing authenticated MCP route and request-byte limit apply. Packet order is immaterial; overlapping intervals, unsupported recipes, invalid vectors, missing rows and inconsistent clocks are rejected. Hashes identify claimed chunk bytes, not authenticated features or common song origin. Keep returned packets unchanged; do not combine different songs.

Packet rows use excerpt-relative time. Results use song-relative time. `coverage` lists known intervals and gaps; complete coverage only means packets cover the declared duration. Full active1s rows support comparison. Partial last rows still count coverage but are not compared; no repetition occurrence or transition support bridges missing time. Known occurrences on opposite sides of a gap may match. Missing results do not establish absence of repetition.

`rmsDbfs: null` encodes measured digital zero and requires `activeFraction: 0`; comparison uses a documented -160dBFS floor. It is not missing RMS. Missing analysis is represented by a gap, not a fabricated silent packet. `mel` and `lowShare` null mean unavailable spectral evidence.

## Measurements

Recipe `logmel16-1s-sequence-v1` reuses the existing128-band50Hz log-mel frontend. Groups of8 adjacent bands are averaged over active frame centers, then normalized to16 components. No second FFT or change to model inputs. At least half the frame centers must have RMS>=1e-4, with aggregate RMS>=1e-4; unavailable spectrum yields null. RMS is a centered-frame mean-square proxy, not sample-exact1s RMS or LUFS. Low share is power below180Hz over non-DC/non-Nyquist spectral power. The last centered frame at the exact excerpt end is excluded.

Experimental sequence comparison uses aligned full1s rows; 4–60s occurrences, spectral cosine distance, centered energy-envelope agreement and within-sequence variation. Aligned distance must be <=half the off-diagonal unaligned distance; maximal evidence suppresses strongly overlapping and contained subpatterns. Standalone short motifs remain possible. It does not compensate for tempo changes, transposition or arbitrary alignment. Repeated static tones alone are uninformative. Repetition contrasts describe mean RMS and low-band-share differences between the observed occurrences.

Transitions compare contiguous3s windows before/after a candidate. `support` names those intervals; spectral distance, RMS change and low-share change explain the candidate. Timing resolution is about1s, not measured boundary accuracy. Similarity and novelty are heuristics, not probabilities. No verified verse/chorus, instrument, emotion, style or artistic-intent labels.

At most16 repeat pairs and24 transition peaks are returned; `Total` and `Truncated` fields disclose omitted detail. Comparisons use at most600 full rows. Default requests skip recurrence computation entirely and report `diagnostics.comparisonCells: 0`; the method string identifies transition-only execution. Only explicit experimental recurrence allocates the bounded pair matrix (up to1,440,000bytes of Float32 storage). Withheld arrays/totals remain omitted, never reported as no repetition. This does not establish whole-isolate peak memory or hosted throughput.

## Evaluation

Local evidence: `evidence/structure-15/`. Baseline7e0491e has no song-wide comparison tool. Three actual old MCP outputs and frontend features were preserved. Separate synthesized musical phrases provide known recurrence and transition truth; steady harmonic tone, gradual drift and silence test false positives. Existing `waveform.changes` start/end cues are an energy-cue proxy, not a competing old structure model.

Pilot A recovered the large returns but produced many short pairs; full-phrase macroF1 .149733. The sole B added ordered specificity and maximal-evidence suppression: .583333, still below .80. No more method tuning. B was frozen before six distinct Confirm clips (376s total, three musical layouts and three negatives).

| Frozen Confirm question | Result | Decision |
| --- | --- | --- |
| Substantial phrase-pair retrieval, macroF1>=.80 / recall>=.75 | F1 .555556, recall .833333; 3TP,4FP,1FN under fixed one-to-one section oracle | F1 gate failed; no default recurrence adoption |
| Transitions, macroF1>=.80 within±2s | .885714; 7TP,2FP,0FN | Limited synthetic capability improvement |
| Equal-energy transition recall gain>=.50 over energy cues | 5/5 vs0/5, +1.00 | Passed |
| Each steady/drift/silence negative<=1 pair/transition | All three0/0 | Passed |

The recurrence failure is retained, not rescored after inspection. One32s return covers two16s reference pairs, but one-to-one scoring credits only one. Four extra5s motifs are outside the full-section labels; this does not prove they are musically false. Clean full-section retrieval remains inconclusive under this data/budget. An annotation/granularity investigation would be a separate follow-up, not an excuse to change this result. The combined original `criteriaPass` remains false.

One actual600s comparator run:54.9471ms (guard500ms); no linear extrapolation of quadratic work. Fixed real-input packet extraction: normalized18.689/9.407/12.110ms per30s (guard25ms). Every original frontend Float32 byte and spectral frame matched on those three inputs. CPU isolation, whole-request speedup, whole-isolate peak and hosted Sites operation are not established by these local checks.

Three actual old/new compiled-MCP calls preserved every old analysis field except two diagnostic timers; added packet bytes5278/13536/13528. A constructed90s ABA exercised actual timing calls with source offsets, then default and explicit experimental structure calls. Local acceptance:17 original functional successes plus one corrected auth-only check on the identical compiled artifact. The first harness wrongly expected public tools/list to require identity; its failed receipt is retained, and unauthenticated tools/call correctly returns401. No model/cost replay was used to fix that evaluator error. Final600s result serialization:19,287bytes experimental /14,029bytes default; dense legal30s packet13,930bytes, all counting both MCP copies. Repository106tests plus7 affected rechecks, typecheck and build passed; lint0errors/3existingwarnings.

Concept references: [temporal exclusion and cosine recurrence](https://librosa.org/doc/0.11.0/generated/librosa.segment.recurrence_matrix.html) and [local novelty for music segmentation](https://www.audiolabs-erlangen.de/resources/MIR/FMP/C4/C4S4_NoveltySegmentation.html). This bounded implementation is not a numerical port or benchmark of those libraries.
