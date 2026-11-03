# Local Music Lens (macOS)

Version 0.2.6 combines native Whisper word times and optional voice emotion or
music-mood windows in one analysis. It runs per job; no daemon, automatic download,
upload, installation or global client configuration is added. Voice models are
unvalidated for singing; music-mood accuracy is unmeasured. Scores are model
estimates, not proven emotions, precise boundaries or highlight quality.
This module does not run in a Sites Worker. Its portable JSON supports a separately
implemented Sites import path; export alone does not deploy that path.

## Run

Use the existing isolated Python3.12 MLX environment and materialized Whisper
model. requirements-macos.lock records that environment. Emotion inference uses
a separate existing Python environment with Torch/FunASR/Transformers and/or
Essentia, recorded byte-for-byte in requirements-emotion-macos.lock from the
existing pilot environment. Preserve the venv Python path instead of resolving
its interpreter symlink. The runner never installs dependencies or downloads models.
All startup flags precede the command:

```sh
python3 -I music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/whisper-model status
python3 -I music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/whisper-model --emotion-python /absolute/emotion-runtime/bin/python --emotion-assets /absolute/emotion-assets --timeout 600 analyze fixture.wav --language en --emotion-mode voice
python3 -I music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/whisper-model report RESULT_ID
python3 -I music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/whisper-model timeline RESULT_ID --layer emotion --start 0 --end 30 --limit 100 --cursor 0
python3 -I music_lens_local.py --audio-root /absolute/audio --result-root /absolute/results --model-dir /absolute/whisper-model export RESULT_ID --title "Local analysis"
```

`--emotion-mode none` is the default for old callers. `voice` runs emotion2vec+
base plus audEERING arousal/dominance/valence; `music` runs MSD-MusiCNN DEAM/MIREX
music mood. Choose explicitly: music mood is not the singer's emotion.
Voice windows are4s/hop2s/minimum tail1s; music windows5s/hop2.5s/minimum tail3s.
Unsupported tails remain uncovered. No VAD, vocal separation or language-quality
claim is implied.

Expected assets below --emotion-assets:
- category/{model.pt,config.yaml,tokens.txt,configuration.json}: emotion2vec/
  emotion2vec_plus_base revision b318240bfe67db81a8c572ecb37ce9c3759b81c9.
- intensity/{model.safetensors,config.json,preprocessor_config.json}:
  audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim revision
  6eba34a2485ea31cb03600241787c3a5edab8626.
- music-model/{msd-musicnn-1.pb,deam-msd-musicnn-2.pb,moods_mirex-msd-musicnn-1.pb}:
  official versioned Essentia models; preserve source/license records. Pilot local
  hashes are provenance, not independently published upstream hashes.

Preserve license notices and existing personal/noncommercial usage restrictions.
Model/config hashes, engine versions and worker source are part of cache identity.
Category loading checks audited FunASR source hashes and only permits the exact ten
unused training-decoder omissions; missing inference state fails. Weights-only
loading and dimensional safetensors with strict loading checks are mandatory.

## Input, result and partial states

Input is nonempty mono16000Hz signed16 PCM WAV, at most600s/64MiB. Language is
explicit en/ko/ja/auto. No transcoding or shell execution. Paths must stay inside
the audio root; descriptor-relative O_NOFOLLOW opens reject escaping swaps and
symlinks. Use private roots; this is not a multi-user security sandbox.

Full immutable reports: RESULT_ROOT/RESULT_ID.json. New schema is
music-lens-local/2; v1 reports remain explicitly read-only loadable without
migration. Unknown schemas and invalid stored values fail.

V2 retains words, native uncalibrated probability, provenance, warnings, timing
notes and audio clock. Added: asr_status, and emotion mode/status/warnings,
recipe, coverage and windows. Complete requires all requested layers. Ordinary
stage failure retains successful independent layers as partial; empty ASR never
invents words or discards valid emotion. No successful requested layer means
error and no publication. Cancellation/timeout aborts without publishing.
CLI exit0 and MCP isError:false can include partial results: inspect state,
asr_status, emotion.status and warnings. Failed emotion is not neutral; missing
is not silence. Voice dimensions use native approximate0–1 scale; music uses
native approximate1–9. Never clamp or mix them. Highest observed arousal is a
window candidate, not an exact instant or validated clip recommendation.

Zero-length Whisper words remain. Native ends up to50ms beyond duration remain
unchanged with timing_notes; larger invalid intervals are omitted with warnings.
No timestamp is silently clamped.

report/analyze omit all words/full emotion windows, retaining counts and highest
observed arousal window. timeline --layer words|emotion pages one layer overlapping
inclusive [start,end]. Reuse same range/layer with continuation cursor. Limits:
1–200 rows and byte cap,128KiB ordinary CLI/MCP output.
Timeline includes layer_status, warnings and report_state: empty rows never imply
silence or neutral emotion by themselves.

export emits music-lens/1 with validated v2 localAnalysis, stable UUID, title,
source digest/duration and explicit unvalidated provenance. Only known relative
model/config/hash/version/settings metadata is portable; no absolute execution
paths or host identifiers. Complete UTF-8 JSON cap1,900,000 bytes; excess fails,
never truncates/uploads. CLI-only; importing/sending is a separate operation.

## Process, cache and recovery

ASR uses fixed sys.executable -I SCRIPT _infer. Emotion uses adjacent fixed
emotion_worker.py and startup-configured Python, never an MCP caller-selected
executable. Stages are serial/offline; the voice worker releases the category
predictor before loading dimensions. A shared wall-clock deadline covers preparation,
hashing, metadata probes and inference stages;
timeout/cancellation kills the current process group and reaps it. Status,
report and cache paths do not load models. Metadata probes use DEVNULL stdin and
at most30s or the remaining deadline. Cancellation is checked before/after a probe
and between1MiB hash chunks; a running synchronous probe may delay cancellation
until it exits. Full asset hashing on analyze cache hits remains a deliberate
integrity cost; no size/mtime-only digest cache is introduced.

Cache identity: source bytes, active model/config files, versions, runner/worker
source, mode/language and window recipe. Active assets are rehashed before
publication. Atomic no-overwrite hard links and per-result owner locks protect
results. Force-killed parents may leave a lock/temp directory; corrupt cache
fails closed. Recovery remains operator-controlled: inspect PID/owner and preserve
evidence before authorized cleanup. No automatic report/stale-lock deletion.

Transient stage failures retain useful partial data under a unique immutable
provenance.failure_attempt ID, leaving the deterministic cache slot free.
Summaries expose cacheable:false/retry_recommended:true. Only a later explicit
analyze retries; no automatic inference retry loop. A later success fills its
deterministic cache and never overwrites the partial. Deterministic empty ASR and
short-tail partials remain reusable. Version/runner hashes prevent0.2.0 failed
cache entries being selected by0.2.1; old files stay readable and untouched.

Heavy work (analyze, optional rhythm, eye look) shares one lease, `~/AI-Listen/.analyze.lock`
(heavy_job.py). A second job gets busy at once; there is no queue or retry. Voice
emotion runs on CPU in equal-length batches of 8. New version, runner and worker
hashes give 0.2.3 its own cache identity.

Window re-read (0.2.4): after one Whisper pass, a 30 s decoder window is decoded again
when its text has a known end-of-video phrase (ja/en/ko), a word longer than 40
characters (a decoder loop). The retry starts 2, 4, 6, 8 or 10 s later in the window;
the first clean retry replaces it. If none is clean, the loop words go, and so does
every word from the phrase to the window end (what followed one was invented too). Windows without words are not retried. `window_rereads` in the report lists each window, the reason
and the shift used. Cost: a few seconds per bad window. The phrase list comes from
observed failures, so a phrase not on it stays in the output.

0.2.5 adds two reasons. `credit`: the window's text has song-credit words (作詞, 作曲,
編曲) that Whisper writes over an intro. `sparse`: a loud window (other than the first)
with under half the song's median words per second, which is where Whisper skipped
sung lines, often repeated hooks. For both, every shift is tried and the retry with the
most words is used only if it has 1.5x and 10 more words than the window. No retry of any
reason is used if it has an end-of-video phrase, a credit word or a phrase repeated many
times in a row. If no retry helps, credit words are
dropped and a sparse window stays as it was. A loud instrumental break also counts as
sparse when it has a few words, so it costs five extra decodes but keeps its words.

0.2.6 widens `loop` to a short phrase repeated many times in a row, which Whisper writes
for dozens of words at a time (2-24 characters 6+ times, 25-80 characters 4+ times). The
first clean retry replaces the window; if every retry repeats too, the window stays, since
the repeat may be sung. A real repeated hook can still lose the seconds the retry skips.
`자막` joins the end-of-video phrases.

Whisper model: pass it with `--model-dir`. We use large-v3 (mlx-community/whisper-large-v3-mlx,
revision 49e6aa286ad60c14352c404340ded53710378a11, MIT): more accurate on songs than
large-v3-turbo in our local listening tests, at about twice the time and 3.9 GB of memory
against 2 GB. Turbo still works with the same flags.

## MCP and verification

serve exposes status/analyze/report/timeline as newline JSON-RPC on stdin/stdout.
Same startup flags. One active tool; ping/cancel remain readable. EOF cancels active
work and waits. Notifications get no reply; diagnostics use stderr. No network
listener or settings writes.

```sh
python3 -m unittest discover -v -s . -p 'test_*.py'
```

Eleven focused stdlib checks retain seven previous regressions and add layer
failure retention, emotion validation/export/paging, mode/model/worker cache
identity and second-stage cancellation. Fake inference is orchestration evidence;
only stdio negotiation/status uses a real CLI child. The schema assertion changes
because this change explicitly versions reports; no quality threshold or old
failed result is changed. Root owns actual inference, independent client checks
and Sites integration/deployment evidence. Windows, sung timing/emotion quality
and real GPU cancellation remain unverified by these tests.
