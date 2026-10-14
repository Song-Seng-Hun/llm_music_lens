# Beat/downbeat and singing alignment pilot

2026-10-14; baseline `501c6e45d4d024cd8da14452b6e2baaa2c8d83f7`.

The target is a Sites analysis server called through MCP. An open browser or a separate always-on local analysis server is not part of that target. Local model runs below are research measurements, not a substitute deployment. No paid API, frontend change or hosted deployment was used.

## What actually ran

| Track | Actual result | Practical limit |
|---|---|---|
| Beat This! small0, CPU | 12/12 labelled GTZAN clips; macro beat F1 87.9219%, downbeat F1 70.0713% | 70ms one-to-one tolerance; fixed convenience subset, not general-song accuracy or improvement over the old estimator |
| TIFA singing alignment, CPU | 2/2 Japanese clips, 55 lexical phones; 96/110 boundaries within 50ms, 105/110 within 100ms | GTSinger training overlap; supplied correct phone sequence; not automatic lyrics, syllable grouping, Korean or rap validation |
| Beat This! exported ONNX in local workerd | Actual inference on a fixed 5-second spectrogram; both outputs match Torch within frozen tolerances | Not a deployed Sites test; raw-waveform preprocessing, full-length inference and whole-isolate memory remain unverified |

No new analysis tool has been registered on the live MCP endpoint. Its existing three report-reading tools remain unchanged. These results establish working candidate components and a server-runtime path to continue, not an end-to-end music service.

## Beat/downbeat experiment

One published checkpoint, official `beat-this==1.1.0` preprocessing/inference and minimal postprocessing. CPU float32, Torch threads 1; official 1500-frame chunks, 6-frame borders and 50fps. No training, tuning, fitted timing offset or candidate sweep.

Before predictions, the selection was frozen as the first three original keys in each of blues, classical, hiphop and rock, in lexical order. The scorer uses maximum-cardinality one-to-one matches within inclusive 70ms; duplicate detections remain false positives. All 12 completed:

- Beats: 644 true positives, 129 false positives, 41 false negatives; pooled F1 88.3402%, macro F1 87.9219%.
- Downbeats: 135 true positives, 118 false positives, 39 false negatives; pooled F1 63.2319%, macro F1 70.0713%.
- Per-track downbeat F1 ranges 27.5862–100%. Minimal postprocessing does not establish meter; detections must remain estimates, not guaranteed bar starts.
- Warm-model execution/read/scoring was 3.53–4.32 seconds per approximately 30-second spectrogram. Load/import was 7.16 seconds; sampled process RSS across labelled cases and waveform smokes reached 351,166,464 bytes (334.90MiB). This is Python-process memory, not Workers memory.
- Separate 8.713-second vocal and 57.329-second quartet waveform runs completed. They have no beat/downbeat truth here and contribute no accuracy score.

The labelled run uses published precomputed spectrograms, so it does not measure waveform preprocessing accuracy. Checkpoint-specific training overlap was not independently audited; no held-out claim is made. The absence of an old-estimator run on the same audio prevents a relative-improvement claim.

Sources: [Beat This!](https://github.com/CPJKU/beat_this), [published spectrograms and annotations](https://zenodo.org/records/13922116). Both downloaded archive MD5 values match the publisher. Checkpoint SHA256: `6074be2c4d490c5f6101fcc374a1ec72ae93456e23bb6019783b849f5dc7d47b`.

## Singing phone alignment

[TIFA v1.0.0](https://github.com/openvpi/TIFA/releases/tag/v1.0.0), source `32a0a13aebce580844ad1dc281ea11ef1be94f14`; 39,604,224 parameters, CPU float32, four threads, canonical Viterbi decoder. Model archive SHA256 `6da6832cd2cb981aae1ccc2d9330f8f7c085ce7977d1eb13bff63531d038a88b` matches the release digest. Weights loaded with `weights_only=True`; configuration parsed safely.

Two [GTSinger](https://huggingface.co/datasets/AaronZ345/GTSinger) Japanese clips from the same singer/song total 20.595 seconds. Correct phone identities/order were supplied; reference times were excluded from inference. No word-duration splitting was used. All lexical onsets and offsets enter the denominator; AP tokens are scored separately.

Lexical boundary MAE 42.64ms, p95 70.00ms. Several errors remain large: about 630–640ms around one transition and 530ms on another vowel onset. Token agreement about 0.9997 did not reveal those errors and must not become timing confidence. These are conditional phone timings; mora/syllable boundaries and many-to-many note alignment still require separate work.

Load/import took 11.05 seconds; first clip 13.42 seconds including decoder compilation, second 0.615 seconds. These are single observations, not a deployment latency benchmark. The model lists GTSinger in training sources; the measurements are diagnostic, not independent validation. Model CC BY-NC-SA4.0 and dataset terms are retained with the local assets; neither weights nor recordings are redistributed here.

## Server-runtime result

Local workerd executes a statically registered WASM module. Dynamic WASM byte compilation fails; the installed ORT factory also needs an explicit environment/loading adapter. With that adapter, actual inference of the fixed `[1,250,128]` input succeeds:

- ONNX model: 9,426,750 bytes, opset 17. Input is the first 5 seconds of vocadito20 through the official frontend, produced offline for this runtime check.
- Torch/workerd maximum absolute difference: beat `9.54e-6`, downbeat `1.144e-5`; both satisfy `atol=1e-4`, `rtol=1e-4`, with 250 values each.
- Local inference clock: about 972ms; full workerd test about 2.095 seconds. These are not hosted CPU quotas.
- WASM capacity: 16MiB after initialization, 40MiB after session creation, 57.625MiB after inference. This excludes JS, other buffers and hosting overhead; it does not prove compliance with the Sites 128MB limit.

Actual Sites module registration, total isolate peak, hosted CPU/job limits, audio decoding/log-mel conversion and full-length chunking remain unverified. The successful five-second graph is not the official full-context accuracy path. An experiment-specific Windows ONNX DLL issue was resolved with ONNX1.16.1; no host/global package change was made.

## Closure and retained evidence

Phase: **Pilot**. Both methods produce real output; numerical server-runtime feasibility is demonstrated locally. Overall accuracy improvement and complete Sites operation are **inconclusive under this data/budget**. This does not discard the working components: preserve the callable research scripts and use the verified server path for subsequent implementation. Do not replace the current analysis or claim syllable accuracy from this pilot.

Original local records: `evidence/timing-extensions-09/PLAN.md`, `beats/FREEZE.json`, `beats/pilot-results.json`, `alignment/frozen/manifest.json`, `alignment/results.json`, `sites/RESULTS.md`, `REVIEW.md`. Downloads, predictions, every error, failed setup logs and source hashes remain there, excluded from Git. A fresh clone lacks those assets; their absence is not a passing check.

Next bounded work: full-context server memory/runtime and hosted module registration; independent labelled singing with a specified lyric/pronunciation input contract, then syllable/mora grouping and note/beat linkage. These are separate questions, not reasons to keep retuning this pilot.
