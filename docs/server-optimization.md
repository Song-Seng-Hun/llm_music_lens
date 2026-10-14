# Server cost optimization

The optimization16 experiment preserves the existing music-analysis recipe,
sample rate, model, precision, context length and output evidence. It adds no
dependency, API or analysis mode. Baseline: `5fac172`.

Two changes use the existing execution paths:

- The FFT frontend reuses two private Float64 twiddle tables instead of
  recalculating identical trigonometric values for every butterfly/frame.
  Arithmetic order and model inputs stay unchanged. The tables retain
  16,368 bytes per loaded frontend module.
- Default `analyze_music_structure` requests skip recurrence entirely, since
  recurrence evidence is already withheld. Explicit experimental opt-in still
  computes the original recurrence. At600 full rows, default execution avoids
  the1,440,000-byte Float32 pair matrix and179,700 pair comparisons.

These are named allocation bounds, not whole-process peak-memory savings.
The model and its WASM capacity are unchanged. The FFT tables and recurrence
matrix have different lifetimes and must not be subtracted into a universal
peak-memory claim.

## Frozen comparison

Fixed inputs: three existing real PCM excerpts, twelve existing structure
inputs including six previous synthetic confirmation cases,600s packets,
gaps, partial tails and malformed guards. Four serial ABBA blocks after one
warmup per method/case, eight measured observations per side. No model or
quality tuning; no outcome-selected reruns.

| Primary local stage metric | Result |
| --- | --- |
| FFT frontend, macro paired savings |54.959%,197.299ms normalized to30s |
| Default600s structure, paired savings |79.428%,46.577ms |
| Explicit experimental recurrence, paired added time |+3.754725ms; failed guard2.314985ms |

The combined original confirmation criteria failed, and remain failed.
Frontend and default-path savings passed their own practical thresholds.
The opt-in recurrence body is unchanged, but measured performance was worse
in3/4 blocks. Its prior JIT/cache history differs because default requests
execute recurrence only on the baseline; that limits causal interpretation
but does not erase the failed guard.

All449,920 frontend Float32 values and38,665 spectral fields remained exact.
Structure outputs and refusals matched for all twelve cases in both modes.
Default-only metadata changes: `comparisonCells` becomes0 and `method`
truthfully describes transition-only execution. Experimental outputs remain
fully identical, with the previous failed-quality evidence still attached.

Local raw evidence: `evidence/optimization-16/eval-*`. These private input and
measurement files are excluded from Git. Peak memory, hosted Sites operation
and general throughput are not established by this local experiment.

## Actual MCP and product decision

The preplanned immutable-built-Worker comparison made60 calls: first calls,
warmups, then two ABBA blocks per case. Every analytical field was identical,
including text/structured copies and isolated-vocal pitch. Only diagnostic
timers and the two approved default execution metadata fields differ.
Unauthenticated tool execution still returned401. Runtimes were reused;
first-by-case values are not independent cold-process measurements.

| Local MCP case | Baseline median | Candidate median |
| --- | ---: | ---: |
| Vocal excerpt,10.25s, isolated/fine |1992.2ms |1849.0ms |
| Blues excerpt,30s |4499.9ms |4131.6ms |
| Hip-hop excerpt,30s |4301.7ms |4694.2ms |
| Default600s structure |88.42ms |26.56ms |
| Experimental600s recurrence |71.68ms |89.85ms |

Default structure's paired block reduction was68.83%/60.46ms; experimental
recurrence added26.69%/19.77ms. Audio paired percentage changes were8.49%
faster,7.96% faster and0.74% slower. Ratios and absolute differences are
aggregated separately; neither should be reconstructed from unpaired medians.
Two blocks and host-load variation do not establish universal whole-request
speedup. The frontend component consistently improved. Response evidence and
sizes are essentially unchanged; no context-size optimization is claimed.
WASM capacity stayed72,548,352bytes; whole-isolate peak remains unmeasured.

Practical post-result decision: adopt both changes for this default-oriented
tool. FFT independently meets the frozen criteria. Default recurrence skipping
avoids work whose result is withheld and saves a1.44MB matrix at600 rows;
accept the measured optional experimental-path penalty (about20ms in these
MCP pairs). This is a disclosed product tradeoff, not a passed original
combined experiment. The original `criteriaPass:false` and all slower cases
remain in the evidence. No benchmark was repeated to obtain a favorable run.

Verification:106 repository tests, typecheck and build passed; lint had
zero errors and three unchanged warnings. Independent exact-diff/evidence
review is recorded locally. No model, dependency, auth, schema, hosted
deployment or quality-threshold change occurred. The two supported paths
remain intentional: default transitions; explicit experimental recurrence.
