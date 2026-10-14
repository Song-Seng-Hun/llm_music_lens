# Sites server inference feasibility

This experimental server path exports Beat This small0 to ONNX and executes a fixed [1,250,128] feature input in local Cloudflare workerd with ONNX Runtime WASM. It is evidence toward a Sites-hosted analysis MCP, not a deployed Sites endpoint. There is no frontend change or background service.

Only authored scripts live here. Checkpoints, audio, generated ONNX/WASM/loader copies and logs stay in ignored `evidence/`. The adapter uses the already-installed pinned ORT; it does not install dependencies or alter its original files.

## Reproduce from a clone

Install the project's existing locked JavaScript dependencies using its normal installer. Python packages below are optional research dependencies, installed in an isolated environment. Example PowerShell commands from the repository root:

```powershell
python -m venv evidence/sites-env
& evidence/sites-env/Scripts/python.exe -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
& evidence/sites-env/Scripts/python.exe -m pip install beat-this==1.1.0 onnx==1.16.1
& evidence/sites-env/Scripts/python.exe -B research/timing/sites/export-beat.py --help
```

Supply an existing Beat This `small0.ckpt` and a permitted audio file at least five seconds long. The exporter accepts only checkpoint SHA256 `6074be2c4d490c5f6101fcc374a1ec72ae93456e23bb6019783b849f5dc7d47b`, then loads it with `weights_only=True`. No automatic checkpoint or audio download occurs. Both export and work directories must be new subdirectories of this checkout's ignored `evidence/`; existing directories are refused to preserve earlier experiments. Replace the two input paths below:

```powershell
& evidence/sites-env/Scripts/python.exe -B research/timing/sites/export-beat.py --checkpoint evidence/inputs/small0.ckpt --audio evidence/inputs/audio.wav --output-dir evidence/sites-export
node research/timing/sites/compare-node.mjs --artifact-dir evidence/sites-export
node research/timing/sites/prepare-workerd.mjs --artifact-dir evidence/sites-export --work-dir evidence/sites-workerd --run
```

The Node comparison is optional and uses the existing `onnxruntime-node` transitive dependency when installed. It refuses an existing `beat-node-result.json` before inference and writes results exclusively. The workerd comparison independently checks both outputs against the Torch reference. No npm dependency needs adding for this path.

The exporter uses official `Audio2Frames.signal2spect` on the first five seconds, takes its first 250 frames, freezes opset 17 and fixed tensor shapes, and saves input/reference/model hashes. Trace warnings do not establish support for another shape. The raw-audio log-mel preprocessing currently runs in Python, not in the Worker.

`prepare-workerd.mjs` verifies the tested ORT package version plus factory/WASM hashes and workerd version. It creates an evidence-only adapter which suppresses Node/browser environment branches and instantiates a precompiled WASM module. It writes the config and authored caller into the selected ignored work directory, referencing model/input files in place. Without `--run`, it prints the generated workerd command only. With `--run`, it uses a bounded 60-second local test and preserves `prepared.json`, `workerd.log`, and `workerd-result.json`, including all 500 output logits for independent parity checks; a runtime/parity failure returns a nonzero exit status.

## Measured pilot, 2026-10-14

The original fixture was `vocadito_20.wav`, first five seconds, audio SHA256 `feb3c31e77b7779bdfb302c7819644535804f22a90a8e21f63c393d667ec5181`. It produced input SHA256 `292f90eca16ccc4f0d6c5b663035d43d34a51fb65dcb7e47312eecb18dc0ffeb` and ONNX SHA256 `2c61c460654723857fc8dface211d133e6c983efec4c5e91b576bfca746182f1` (9,426,750 bytes).

Torch 2.8.0+cpu reference, Node ORT 1.21.0 and workerd 1.20260515.1 with ORT WASM 1.22.0-dev.20250409-89f8206ba4 all completed. For 250 beat and 250 downbeat logits, workerd max absolute errors were 9.537e-6 and 1.1444e-5, within the predeclared atol/rtol 1e-4. Node errors were 8.106e-6 and 8.583e-6. Single-run inference times were Torch 294 ms, Node 188 ms, workerd 972 ms; the full workerd test took 2.095 seconds. These are portability measurements, not benchmarks or downbeat-quality scores.

WASM capacity rose from 16 MiB to 40 MiB after session creation, then 57.625 MiB after inference. This is **WASM capacity**, not whole-isolate peak: JavaScript, serialized model, compiled code and temporary allocations also count. It does not verify the Sites 128MB isolate limit. Checkpoint size and Torch RSS likewise cannot establish that limit.

Preserved failures: dynamic WASM byte compilation was rejected by workerd; the unadapted ORT factory assumed unavailable Node file-URL/browser-location APIs. Python ONNX 1.17.0 failed Windows DLL initialization before export; replacing only that isolated dependency with 1.16.1 resolved it. No model architecture, input or acceptance threshold was changed after evaluation. Original raw logs remain in the local evidence directory rather than Git.

## Still unverified

- Hosted Sites registration of static `CompiledWasm` imports and its deployment bundle policy.
- Whole-isolate peak, hosted CPU/request lifecycle limits, concurrency and durable long-job support.
- Server-side audio decoding/log-mel preprocessing and MCP analysis invocation.
- Full-song chunking, other shapes and musical beat/downbeat accuracy.

Close this pilot as successful fixed-input runtime portability, with hosted execution still inconclusive. A five-second successful output is not a finished Sites analysis server.
