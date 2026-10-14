# Beat/downbeat research runner

This is the reusable source of a bounded local Beat This pilot. It is not a production API or a Sites deployment. Weights, benchmark arrays, audio, annotation files, predictions and local run state belong outside this source directory.

Use an isolated environment with the package versions in [SOURCES.json](SOURCES.json): Torch2.8.0+cpu, torchaudio2.8.0+cpu, beat-this1.1.0, NumPy2.2.6, soundfile0.13.1, psutil7.2.2 and soxr1.1.0. This runner does not install dependencies or download weights. Sources/hashes identify the actual measured implementation; upstream main links are reference locations, not a claim of a pinned current commit.

## Freeze and run

Before predictions, inventory original GTZAN keys and choose the lexicographic first3 each in blues, classical, hiphop and rock. The example preserves those12 selected keys; acquisition failures or model failures must remain recorded, not be replaced by easier tracks. Training/data overlap is unverified, so this selection is not called held out.

Copy [FREEZE.example.json](FREEZE.example.json) into your separate local evidence folder. Replace every asset placeholder with a verified local path and SHA256; paths resolve relative to the freeze. Check the literal selected spectrogram/annotation members against the acquired archive. Set `frozen_before_predictions` to true only once the selection, identities, CPU settings and metric are fixed. Keep that file unchanged during the run.

```text
python research/timing/beats/run_pilot.py --freeze /absolute/local-evidence/FREEZE.json --output /absolute/local-evidence/run1-results.json
```

Both paths are explicit. Existing result paths are refused. The output is persisted as cases finish, preserving native events, individual matching indexes/errors, failed cases and timing. A partial run must not be reported as complete. Do not write run results under this source directory.

The official `Spect2Frames` call returns beat logits before downbeat logits; `Postprocessor(type="minimal",fps=50)` also returns beats before downbeats. The code retains the upstream1500-frame chunks,6-frame borders and keep_first aggregation. Torch intra/inter-op compute uses1thread and float32. Official postprocessing and RSS sampling can use helper threads; this is not a claim that the whole process has one OS thread.

GTZAN NPZ is itself a ZIP of selected NPY members. Selected payloads use NumPy `allow_pickle=False` and a64MiB member bound; no whole-archive extraction occurs. Spectrograms must be floating[time,128]. A small individual NPZ member additionally needs `spectrogram_key`. Annotation rows contain seconds and beat position; position1 supplies downbeat truth. Beat-only annotations keep downbeat truth unavailable rather than invent it. No temporal shift or native-event clipping is performed.

One-to-one event F1 uses an inclusive70ms tolerance with all unmatched events retained. Empty truth+prediction F1 is undefined. Macro/micro results and failed-track counts are separate. Two optional real-waveform smokes establish execution only; no accuracy claim follows without beat/downbeat truth.

Model load/imports are timed separately. It loads once, so per-case runtimes are warm-model measurements including input read/inference/postprocessing/scoring; waveform-smoke time includes preprocessing. psutil process RSS is sampled every10ms and can miss brief peaks. None of these figures establishes Sites runtime, hosting memory feasibility or improvement over the existing product.

## Mechanical checks

```text
python -B -m unittest discover -s research/timing/beats -p "test_beat_*.py" -v
python -B research/timing/beats/run_pilot.py --help
```

The six checks use synthetic arrays only: duplicate/unmatched matching, chronological maximum-cardinality matching, fixed tolerance and empty/invalid inputs, aggregate denominators, numeric ZIP/NPZ members, pickle rejection and independent downbeat/beat-only labels. They do not load Torch/checkpoints or access benchmark audio/annotations. The measured pilot is documented separately by the project owner; source publication does not publish the research data.
