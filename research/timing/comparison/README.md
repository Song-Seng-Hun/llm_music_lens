# Paired raw-waveform beat comparison

This is a bounded research harness for the fixed12 GTZAN cases selected in the earlier timing pilot. It compares the original `analyzeRhythm` with the actual shared server JavaScript frontend, fixed250-frame small0 model windows and decoder. `reference` mode uses Node ONNX Runtime as a runtime reference; `production` mode calls the real local MCP endpoint. The latter is required before attributing the comparison to the server execution path.

The same native22050Hz mono PCM16 samples feed both methods, decoded by division by32768. All files are cropped to the first661500 samples/30seconds to match the server input bound. Original audio/PCM hashes, annotation member hashes, excluded tail references and all errors remain in ignored evidence. No resampling, gain changes, phase shifting or predicted-output-based sample selection occurs.

## Sources and limits

- [Annotation maintainers' GTZAN mini distribution](https://github.com/TempoBeatDownbeat/gtzan_mini/tree/a61439e86c13037011fde8e0f0743ec55c50bce3): first three original files each from blues, classical, hiphop and rock; about16MB total. Downloaded WAV MD5 values must match the [mirdata immutable GTZAN index](https://zenodo.org/records/13993311/files/gtzan_genre_index_1.0.json?download=1). No audio is redistributed here.
- [Beat This annotations/spectrogram release](https://zenodo.org/records/13922116): the prior local annotation archive is verified against its frozen SHA256. Beat position1 supplies downbeat truth.
- [Official Beat This model documentation](https://github.com/CPJKU/beat_this): `small0` excludes GTZAN from training. This specific convenience subset has already been inspected, so this is not a fresh unbiased population estimate. The old1500-frame, precomputed-spectrogram pilot is context only, not the paired baseline.

Before predictions, the original numerical frontend check failed on4 of2,305,536 values. One fixed-coefficient correction reduced that to2 values but did not pass the unchanged absolute2e-4 plus relative1e-4 gate. Both records are retained. The parent explicitly selected direct complete-recipe product evaluation before predictions. The gate remains **failed**, and this experiment does not claim official-frontend numerical equivalence. No further frontend tuning or tolerance relaxation is permitted within this comparison.

## Sequence

Use the existing ignored Python3.10 timing environment (Torch/torchaudio2.8 CPU, NumPy2.2.6, beat-this1.1.0) and the existing local Node ONNX Runtime. No script installs packages, downloads model weights or executes downloaded installers. `fetch.py` alone downloads the fixed audio files and source index. Keep all assets/output under `evidence/`.

```powershell
$root = 'evidence/timing-integration-10/comparison'
$python = 'evidence/timing-extensions-09/env/Scripts/python.exe'
& $python research/timing/comparison/fetch.py --output $root --pilot-root evidence/timing-extensions-09/beats
& $python research/timing/comparison/features.py --root $root
node research/timing/comparison/run.mjs --root $root --mode parity
& $python research/timing/comparison/freeze.py --root $root --model evidence/timing-extensions-09/sites/beat-small-250.onnx
node research/timing/comparison/run.mjs --root $root --mode reference --model evidence/timing-extensions-09/sites/beat-small-250.onnx
node research/timing/comparison/run.mjs --root $root --mode production --endpoint http://127.0.0.1:PORT/mcp --headers LOCAL_HEADERS_JSON
& $python research/timing/comparison/score.py --root $root
```

These commands document the stages, not permission to repeat the closed experiment. The parity command intentionally exits nonzero for its retained numerical failure. `freeze.py` records that failure and the explicit product-selection decision; it refuses a refreeze after predictions. Both inference modes refuse to overwrite their result files. `--headers` accepts an ignored local JSON file with required local test identity headers; never commit credentials or identity fixtures. The production adapter permits only explicit loopback HTTP endpoints and checks the server-returned PCM SHA256, duration, sample rate and offset against the frozen input.

The frozen primary metric is equal-track macro beat F1 delta using the existing inclusive70ms maximum-cardinality one-to-one matcher in `research/timing/beats/beat_eval.py`. Micro F1, each track's loss/gain, exact runtime event parity and cost are secondary. Missing/failed cases produce empty predictions and retain their full reference denominator. Downbeat scores are absolute new capability measurements; old `strongBeats` are never treated as downbeats.

The budget is one old, one shared-JS NodeORT, and one actual MCP run per case, with no tuning or candidate selection. Cost reports distinguish old full spectral analysis, NodeORT model startup/analysis, and MCP transport plus server inference; single-run RSS samples are not exact peak memory or a general latency benchmark. An observed positive macro delta is reported with its size, per-track losses and cost, not a post-hoc threshold.

The original model export SHA256 is `2c61c460654723857fc8dface211d133e6c983efec4c5e91b576bfca746182f1`. Source/model/data/script hashes and actual execution evidence are in `evidence/timing-integration-10/comparison/FREEZE.json` and adjacent records. Asset licensing remains with the cited original sources; this directory contains only the harness.
