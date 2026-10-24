# Local Music Lens (macOS MLX)

Task MUSIC-LENS-LOCAL-001 r2, tool 0.1.1: one-shot local ASR with retained native word timings.
Scope: this directory; no Sites integration, automatic downloads or singing-quality claim.

Implementation checklist (T2 CLI/API; path/process negatives required):
- [complete] Implement confined inputs, reproducible cache and bounded result reads.
- [complete] Implement fixed child inference lifecycle and stdio MCP entrypoint.
- [complete] Verify fake orchestration, rejection/publication/paging and actual wire entrypoint.
- [waiting] Root/Claude real inference and independent review (outside worker acceptance).

Tests use isolated fake inference only. They do not establish MLX compatibility or ASR quality.

## Run

Use an isolated Python 3.12 environment with `mlx-whisper` and its dependencies
installed by the operator from the binary/hash-locked `requirements-macos.lock`
supplied by root integration. This worker does not create that lock. Dependency
and model installation remain operator-controlled; this runner does not install
packages or download models. Supply a
predownloaded MLX Whisper directory with `config.json` and `.npz` or `.safetensors`
weights. Materialize Hugging Face snapshot symlinks first: model symlinks are rejected.
Model files are hashed before cache lookup and again after inference; this adds disk
I/O but does not load MLX or model tensors into the parent.

From this directory (replace the three absolute example paths):

```sh
python3 music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/model status
python3 music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/model --timeout 300 analyze fixture.wav --language en
python3 music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/model report RESULT_ID
python3 music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/model timeline RESULT_ID --start 0 --end 30 --limit 100 --cursor 0
python3 music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/model serve
```

All startup flags precede the subcommand. `analyze` requires `en`, `ko`, `ja` or
`auto`; `auto` allows Whisper language detection. Inputs must already be mono
16000 Hz signed16 PCM WAV, nonempty, at most 600 seconds and 64 MiB. There is no
transcoding or arbitrary shell invocation. Relative paths resolve inside the audio
root; absolute paths must also stay inside that root. Traversal and escaping
symlinks are rejected. Use private roots writable only by the intended local user;
this process is not a multi-user security sandbox.

Each success returns a 64-character lowercase hex `result_id`. The immutable full
local report is `RESULT_ROOT/RESULT_ID.json`; `report` and `analyze` return only its
summary/provenance, excluding all words. `timeline` returns words overlapping the
inclusive `[start,end]` range, native unrounded seconds, and native uncalibrated
probability when present. Use its `continuation` as the next `cursor` with the same
range. `total` counts all matching words; `truncated` means more remain. Limits are
1–200 words/page with an additional byte cap; JSON wire output is capped at 128 KiB.

The native local report format is explicitly `schema_version: music-lens-local/1`;
missing/unknown schema versions are rejected on load without modifying the file.
Native zero-length DTW words are retained. Native end times up to 0.05 seconds
beyond audio duration are retained unchanged and counted in the separate top-level
`timing_notes` list, including maximum overshoot. These small native overshoots
alone do not make a report partial. Ends beyond this tolerance are omitted.
Report loading revalidates the same timing bounds and notes; no timestamp is clamped.

Invalid intervals/text/probabilities are omitted with explicit warnings and
`state: partial`. No valid words is an error, including silence/unrecognized audio;
the runner does not invent a transcript. CLI analysis exits 0 for both complete
and partial reports; MCP returns `isError: false` for both. Consumers must inspect
`state` and `warnings` before treating a result as complete, and may also inspect
`timing_notes`. This applies to cached results too. Cache identity includes source WAV digest,
model file names/hashes, installed engine version, requested language/settings,
runner digest and tool version. Failed/cancelled/timed-out inference does not
publish a success. Publication uses an atomic no-overwrite hard link in the same
result filesystem. The named per-result `.lock` yields `busy` under contention.
An abnormal parent termination may leave a lock. Stale-lock or corrupt-result
recovery requires operator inspection and authorization before moving/removing
anything: inspect the lock PID/owner or affected report and preserve needed evidence.
The tool never deletes reports or stale locks automatically.

Only the fixed `sys.executable -I SCRIPT _infer` child imports NumPy/MLX; isolated
Python mode ignores Python environment/path injection. It receives a validated WAV
snapshot in a private temporary directory and offline Hugging Face environment
flags. The child exits after each job; timeout/cancellation kills its process group
and reaps it. Read/status/cache paths never import MLX. Do not expose the private
`_infer` entrypoint as an agent tool. Model/hash reads occur before the child and
are not governed by its inference timeout. The parent may persist for stdio MCP.

## MCP

`serve` uses one JSON-RPC message per line on stdin/stdout. It supports initialize,
ping, tools/list, tools/call, notifications/initialized and
notifications/cancelled. Exposed tools are `status`, `analyze`, `report`, `timeline`;
schemas are returned by tools/list. Only one tool call runs at a time; ping and
cancel notifications remain readable during inference. Cancellation identifies
the active JSON-RPC request ID. EOF cancels active inference and waits for the
worker. Notifications produce no reply. Logs/errors use stderr; stdout contains
only JSON-RPC. No network listener, shell, install, upload or delete tool exists.
No global client configuration is changed by these files.

## Verification and remaining limits

```sh
python3 -m unittest -v test_music_lens_local.py
```

Seven checks cover input/result confinement, configuration-sensitive immutable
cache reuse and schema rejection, failed/empty/malformed output and lock publication,
invalid native timings plus zero-length/end-tolerance retention and reload,
bounded paging, isolated-child timeout/reaping using fake Popen, and a real CLI `serve`
process handling MCP initialize/list/status/ping/notifications. The fake timeout
check proves orchestration calls; real MLX subprocess lifecycle, quality, memory,
runtime dependency compatibility and independent client use remain untested here.
Tests contain no inference or network requests. No mutation tool was installed.
The stdlib suite passed after implementation; root/Claude own real fixture evidence
and independent path/process review. Windows and singing accuracy are unsupported
claims in this slice. No Sites import/schema compatibility is claimed.
