# Music Lens LAN API (Mac server)

This is the Mac side of `MUSIC-LENS-LAN-API-1026`, spec v0.6 (SHA256 `6282bd25…f9e524`). It is a small HTTPS
listener for the home LAN that lets Windows teammates call the team wrapper `~/tools/music-lens/ml`
(status / analyze / report / timeline). The server uses only the standard library and runs on `/usr/bin/python3`
(Python 3.9). The listener never loads a model: models load only inside each `ml analyze` job's inference
subprocesses.

Nothing here is installed. No LaunchAgent, firewall rule, certificate or key is created by this repo.

## Files

| File | Purpose |
| --- | --- |
| `server.py` | The server: config, en0/en1 address discovery, TLS 1.2+, strict framing, HMAC auth, durable nonce and receipt logs, pipeline gate, staging, afconvert, strict WAV check, hard-link promotion, `ml` calls by argv, error mapping, cleanup, logs |
| `rhythm_worker.py` | Optional rhythm layer worker (Essentia DSP, no models): BPM (+ half/double), beats, key (+ 20 s sections), beat-grid chords. Runs under the Essentia python, never in the listener |
| `limit_exec.py` | Sets RLIMIT_CPU 60 s / FSIZE 24 MiB / NOFILE 32, then `execv /usr/bin/afconvert -f WAVE -d LEI16@16000 -c 1 src dst` |
| `sign.py` | Shared canonical-string and HMAC helper (server, tests, Python clients) |
| `sign_vector.mjs` | Reference Node signer; prints or checks the fixed vector (`tests/vectors.json`) |
| `examples/api.example.json` | Example production config (`~/.music-lens/api.json`, mode 0600) |
| `examples/com.music-lens.lan-api.plist.template` | LaunchAgent **template** |
| `tests/` | unittest suite (fake `ml`, fake rhythm worker, throwaway cert, 127.0.0.1 test mode) plus one opt-in live test; `tests/synth_audio.py` makes synthetic WAVs for the real-worker tests |

## Tests

```sh
cd ~/dev/music-lens-lan
/usr/bin/python3 -m unittest discover -s tests -v            # offline; uses a fake ml and a temp root
MUSIC_LENS_LIVE=1 /usr/bin/python3 -m unittest discover -s tests -p test_live.py -v   # real wrapper, once
```

The real-worker tests in `tests/test_rhythm.py` run `rhythm_worker.py` under the Essentia python of the emotion pilot
runtime and skip cleanly when that path is missing.

Tests bind 127.0.0.1 only through the test-only `"test_mode": true` config flag. A production config (no
`test_mode`) refuses loopback, `0.0.0.0`, literal bind IPs, port 0, `limits` overrides and `test_ready_file`.

## Signing (summary)

The canonical string is the UTF-8 bytes of 8 fields joined by LF, with no trailing LF: `CLIENT`, `METHOD`, `TARGET`
(exactly as sent, non-ASCII percent-encoded), `CONTENT_TYPE` (empty for GET), `CONTENT_LENGTH` (`0` for GET),
`TIME`, `NONCE`, `BODY_SHA256`. The signature is `X-ML-Sig = hex(HMAC-SHA256(key, canonical))`, where the key is the
**32 raw bytes** decoded from the 64-hex key file. Percent-encode labels as `encodeURIComponent` does (a space becomes
`%20`, and `+` is a literal plus sign).

## Windows client note (name resolution)

On Windows, Node 22 `getaddrinfo` may return `ENOTFOUND` for `songseunghun-ui-MacStudio.local`. The client may then
resolve the name through the Windows DnsClient (`Resolve-DnsName -Type A`) or an optional configured address list.
It keeps only A records inside the `allow` /24, connects by IP with TLS `servername` set to the `.local` name, and
keeps the CA, the hostname check and the certificate pin unchanged.

## User-only install steps (not done; commands written, never run)

Do these only after approving an always-on listener (RunAtLoad + KeepAlive, en0/en1, port 8765, 172.30.1.0/24).

1. Copy the code:
   ```sh
   mkdir -p ~/tools/music-lens/api && cp ~/dev/music-lens-lan/{server.py,limit_exec.py,sign.py} ~/tools/music-lens/api/
   chmod 700 ~/tools/music-lens/api
   ```
2. Create the TLS certificate and key by hand. They must have a DNS SAN and **named-curve** P-256 parameters.
   The system LibreSSL writes explicit curve parameters by default, and Python 3.9's TLS then fails with
   "no shared cipher". Run:
   ```sh
   mkdir -p ~/.music-lens/tls ~/.music-lens/keys && chmod 700 ~/.music-lens ~/.music-lens/tls ~/.music-lens/keys
   /usr/bin/openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -pkeyopt ec_param_enc:named_curve \
     -days 825 -nodes -subj "/CN=songseunghun-ui-MacStudio.local" \
     -addext "subjectAltName=DNS:songseunghun-ui-MacStudio.local" \
     -keyout ~/.music-lens/tls/server.key -out ~/.music-lens/tls/server.pem
   chmod 600 ~/.music-lens/tls/server.key
   /usr/bin/openssl x509 -in ~/.music-lens/tls/server.pem -noout -fingerprint -sha256   # the pin for Windows
   ```
3. Create one key per client machine (64 lowercase hex plus one LF, mode 0600):
   ```sh
   (umask 077; /usr/bin/openssl rand -hex 32 > ~/.music-lens/keys/claude-window.key)
   (umask 077; /usr/bin/openssl rand -hex 32 > ~/.music-lens/keys/codex-window.key)
   ```
   Keep these separate from the snap-annotate keys. Never print them in a shared log or chat.
4. Write the config: `cp ~/dev/music-lens-lan/examples/api.example.json ~/.music-lens/api.json && chmod 600 ~/.music-lens/api.json`.
5. Firewall (needs an admin password; the user does this): allow incoming connections for `/usr/bin/python3` in
   System Settings > Network > Firewall > Options, or run
   `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /usr/bin/python3`.
   Note that `/usr/bin/python3` is a shim. If the prompt names the CommandLineTools `Python3.framework` binary,
   allow that one.
6. Copy these to Windows by hand: `server.pem` (public cert only), the SHA-256 fingerprint, and that machine's own
   key file, into `%USERPROFILE%\.music-lens\` (`mac-cert.pem`, `client.json`, `keys\mac.key`). Then restrict it with
   `icacls "%USERPROFILE%\.music-lens" /inheritance:r /grant:r "%USERNAME%:F"`.
7. Load the LaunchAgent:
   ```sh
   cp ~/dev/music-lens-lan/examples/com.music-lens.lan-api.plist.template ~/Library/LaunchAgents/com.music-lens.lan-api.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.music-lens.lan-api.plist
   ```
   To unload it: `launchctl bootout gui/$(id -u)/com.music-lens.lan-api`. Unloading sends SIGTERM; see
   "Stopping" below. A clean exit does not mean every analysis finished or was cancelled.

## Optional rhythm layer (beats, key, chords)

Off unless the config has a `rhythm` key. Without it the server loads and behaves exactly as before.

```json
"rhythm": {"python": "/Users/songseunghun/Documents/Codex/2026-09-27-window-pc-pc/outputs/music-lens-emotion-pilot-20261025/runtime/bin/python",
           "worker": "/Users/songseunghun/tools/music-lens/api/rhythm_worker.py"}
```

Both paths must be absolute regular files, not group- or world-writable, and `python` must be executable; any other
key or value stops the server (config error). `examples/api.example.json` leaves the key out on purpose: turning the
layer on is an owner decision (copy `rhythm_worker.py` next to `server.py` first).

API:
- `POST /analyze?...&rhythm=1` opts in (`rhythm=0` or no flag = unchanged behaviour, byte for byte). The flag is part
  of the signed TARGET like every other query field. With no `rhythm` config the request gets 400
  `rhythm_unavailable` before anything is staged (no receipt). Any other value is 400 `bad_request`.
- After a successful `ml analyze` (receipt already `done`), the server runs `<python> -I rhythm_worker.py
  <inbox>/api-<op>.wav` by argv on the same promoted WAV, with the same child env (PATH `/usr/bin:/bin:/usr/sbin:/sbin`),
  its own session, and a wall timeout of `rhythm_timeout` (300 s, capped by the time left before the handler's 800 s
  deadline) that kills the process group. It takes the team flock `<root>/.analyze.lock` non-blocking (the same
  file the `ml` wrapper locks) and passes the locked descriptor to the worker, so the lock lives as long as the worker.
  The worker also bounds itself (SIGALRM 330 s, RLIMIT_CPU 600 s) in case the server dies first.
- Rhythm never fails the analyze: the reply stays 200 with the ASR fields unchanged plus `rhythm`:
  - success: the same compact summary as `/report` plus `cache_hit` and `stored` (no beat or chord rows; page
    them with `/timeline` `layer=beats|chords`);
  - `{"status":"busy","code":"busy"}` when the team lock is held (never waits);
  - `{"status":"timeout","code":"timeout"|"no_time_left"}`;
  - `{"status":"failed","code":...}` with the worker's error code (`invalid_audio`, `too_short`, `analysis_failed`,
    `dependency_missing`, `timeout`), or `invalid_output`, `internal_error`, `server_misconfigured`,
    `lock_unavailable`, `no_result_id`, `shutting_down`.
- The worker JSON is stored durably (tmp 0600 + F_FULLFSYNC, replace, directory fsync) as
  `<root>/results-rhythm/<result_id>.json` (directory 0700). A later analyze with the same `result_id` (an `ml` cache
  hit, same audio and settings) reuses it (`cache_hit: true`) and runs nothing. A store failure still returns the
  data with `stored: false`. The files are not pruned automatically (about 30 KB per 10 minutes of audio).
- `GET /report/<id>` adds a `rhythm` summary (`status`, `bpm`, `bpm_confidence`, `bpm_confidence_band`,
  `bpm_estimates`, `bpm_alternatives`, `bpm_alternatives_note`, `key`, `key_sections`, `key_change_count`,
  `beat_count`, `chord_segment_count`, `chords_grid`, `downbeats`, `duration`, `tool`, `warnings`) when a stored file
  exists, re-serializing the native JSON (number formatting may change). Files stored by worker 0.1.0 lack the
  0.2.0 fields and still load; the summary then has `bpm_alternatives`, `bpm_alternatives_note`, `key_sections`,
  `key_change_count` and `chords_grid` set to `null` (they are not recomputed; old chord rows stay frame-level). A damaged file gives `rhythm: {"status":"failed","code":"invalid_result"}`.
  No file: native bytes unchanged.
- `GET /timeline/<id>?layer=beats|chords` is served by the server from the stored file with the native rules:
  `start`/`end` 0..600 (default 0 and 600, `end >= start`), rows overlapping the range, `limit` 1..200 (default 100),
  `cursor` <= total, a ~43 KB page byte cap, `truncated` and `continuation`. Beats rows are `{"time"}`, chords rows
  `{start, end, label, strength}` (with `reliability: "low_reference"`). No stored file: 404 `rhythm_not_found`.
  `words`/`emotion` still go to `ml timeline` unchanged.
- `GET /status`: when the layer is configured, adds `rhythm: {configured, python_present, worker_present}` (file
  checks only, Essentia is not run). Not configured: native bytes unchanged (no `rhythm` key means unavailable).

`bpm_confidence_band` maps the native confidence to Essentia's documented BeatTrackerMultiFeature bands:
`very_low` [0, 1), `low` [1, 1.5], `good` (1.5, 3.5] (Essentia: about 80% AMLt), `excellent` above 3.5. It is
Essentia's guide, not a calibration on the team's music.

Worker output (worker 0.2.0): `bpm`, `bpm_confidence` (RhythmExtractor2013 multifeature, native 0..~5.32,
uncalibrated), `bpm_estimates` (top 5 shares), `bpm_alternatives` (`[bpm/2, bpm*2]`, 2 decimals, only values within
40..300; empty when bpm is 0) with `bpm_alternatives_note`, `beats` (s, ms rounding),
`downbeats: {"status":"not_provided"}`, `key` (`tonic`, `scale`, `strength`, `profile: "bgate"`), `key_sections`
(rows `{start, end, tonic, scale, strength}`, see below), `key_change_count` (sections - 1), `key_section_window`
(20), `chords` (see below), `chords_grid` (`beat` or `frame`), `chord_frame_segment_count` (frame-level segment
count before the beat vote, stored file only), `chords_reliability: "low_reference"`, `sample_rate` 44100 (input
resampled from 16 kHz because RhythmExtractor2013 assumes 44.1 kHz), `duration`, `tool` (name, version,
`essentia_version`), `warnings` (`short_audio` < 10 s, `near_silent`, `no_beats`, `low_bpm_confidence` < 1.5,
`low_key_strength` < 0.5, `key_changes` when there is more than one key section, `no_chords`). Audio under 1 s is
refused (`too_short`).

- Key sections: the same KeyExtractor (`bgate`, frame 4096, hop 2048) on fixed, non-overlapping 20 s windows (a last
  window under 10 s joins the previous one), then adjacent windows with the same tonic and scale merged; `strength`
  is the plain mean of the merged windows (null if any window's native strength was non-finite, with warning
  `key_section_strength_unavailable`). At most 30 sections for 600 s. 20 s holds about 8-10 bars at common tempos,
  enough chroma for a key profile; shorter windows follow single chords. Weak sections (low `strength`) keep their
  row: read `strength` before trusting one.
- Chords: HPCP 12 bins + ChordsDetection (hop 2048, 2 s window) give frame labels. When beats exist, each beat
  interval (plus the head before the first beat and the tail after the last) takes the label with the largest summed
  native strength among its frames (a null strength counts 0 in the vote; ties: more frames, then the earliest
  frame; an interval holding no frame takes the frame nearest its midpoint), then identical neighbours merge. Every
  segment therefore spans at least one beat, except the head and tail. A segment's `strength` is the mean of the
  winning label's frame strengths (null if any is null, with warning `chord_strength_unavailable`; never a measured
  0.0). Without beats, identical consecutive frame labels merge as before (`chords_grid: "frame"`). Key profile `bgate` is Essentia's default; how well it fits the team's
material is not measured. A non-finite native chord strength becomes `strength: null` plus warning
`chord_strength_unavailable`. Worker output and stored files are refused if any number is non-finite or too large.

Accuracy (honest notes): every value is a DSP estimate. On synthetic tests a 120 BPM click track gives 120.0 BPM
(confidence ~3.9) and a C-Am-F-G triad loop gives C major with the four chords at the right 2 s boundaries; real
music has not been measured. Confidence and strengths are native and uncalibrated. Chords are major/minor triads
only, a low-reliability reference, not a transcription. There are no downbeats, bars or time signature. A 600 s
synthetic file took about 6.3 s and ~0.97 GB peak RSS on this Mac (worker 0.2.0).

- Tempo octave: the metrical level is ambiguous. On one real song (216 s, published tempo 220) the tracker said
  109.94 BPM and every `bpm_estimates` entry was ~110, so 220 never appeared there. `bpm_alternatives` lists half and
  double without choosing; compare them with a published tempo or with your own listening. No heuristic warns.
- Key sections are a DSP estimate with 20 s resolution: a change inside a window moves the boundary to a window edge
  (up to 20 s off), and a window straddling the change can report a third key. Synthetic test: C major 0-30 s then
  D major 30-60 s gave C 0-20, G 20-40 (the straddling window; G major is the union of the two scales), D 40-60;
  the single global `key` said G major.
  Relative keys (C major / A minor) share notes and can alternate. One real song with a known modulation (E major,
  then another key area ~160-180 s, F major from 180 s) motivated the field; it was not re-measured here.
- Beat-grid chords remove sub-beat flicker (that real song had 324 frame-level segments, median 0.33 s, shorter
  than one beat) but also any real chord change shorter than a beat; head/tail can be shorter than a beat. On the
  synthetic C-Am-F-G loop beat-grid and frame-level segments agree (2 s each); on the 30 s click track (no harmony)
  frame labels flicker and the beat vote cuts 32 segments to 11. Synthetic music did not make the frame labels
  flicker (the 2 s ChordsDetection window smooths them), so the reduction on real music is untested here.

## Runtime layout (production)

- The `~/AI-Listen/api/` directory (0700) holds `nonces.log` and `receipts.log` (0600, fsynced with F_FULLFSYNC).
- `~/AI-Listen/uploads/<op>/` (0700) is per-request staging; `<op>` is a random 128-bit hex id.
- `~/AI-Listen/inbox/api-<op>.wav` is a hard link of the checked WAV. Only its basename goes to `ml`.
- The server writes `~/AI-Listen/logs/api.log` (5 MiB x 3 files). The launchd stdout and stderr files rotate at
  start with the same limits.
- When a run crashes, its receipts left `running` become `unknown` at the next start. Their paths are logged
  (`receipt_unknown`) and **kept**. Delete them only after you verify that the exact job and its descendants ended.
- Journals fail closed. Every append is written completely and fsynced; the server tracks the last durable size.
  After any write or fsync error the journal is poisoned: no later nonce or receipt is acknowledged (503
  `storage_error`) until a repair truncates the file back to that size and fsyncs it. Compaction (tmp + fsync,
  replace, directory fsync, reopen onto the new file) is one step; a failure after the replace also poisons the
  journal, and repair rewrites it from memory before any new work.
- At start, a torn last line (crash mid-append) is truncated away and fsynced, and `journal_tail_truncated` is
  logged. An unparsable line anywhere else stops the server (exit 2, "journal needs owner recovery"), because it
  may hide a later acknowledged record. Inspect the file by hand; do not delete it to get the server up.

## Stopping

SIGTERM (launchd unload) or SIGINT: the listeners close first, so no new connection is accepted, and no new job is
launched (an upload that finishes now gets 503 `shutting_down`, nothing launched). The server then waits up to
`stop_grace` (630 s: the 600 s analyze bound plus margin) for the running job and its waiting client. An ASR job still
running after that is not killed: its receipt becomes `unknown` with `error_code` `interrupted_by_stop`, its paths
are logged (`receipt_unknown`, `reason: stop`) and kept, as after a crash. The LaunchAgent template sets
`ExitTimeOut` 660, but on this Mac (macOS 27, 2026-10-26) `launchctl print` shows `exit timeout = 60` for the
installed agent, while values under 60 in Apple's own jobs are kept: launchd appears to cap it at 60 s (inferred,
not proven). Treat the reported 60 s as a possible launchd stop budget; a 630 s drain is not guaranteed. Kill
timing and descendant termination have not been tested. The API may exit while `ml` or its inference children
remain. Retained `running` receipts (ASR phase only; a rhythm-phase receipt is already `done`, see deviation 12) become
`unknown` on restart and never authorize an automatic retry. The flock
releases when its holding process exits; server exit or lock release alone does not prove all analysis processes
ended. Before cleanup, follow the existing owner-verification rule for the job and its descendants. Restart only
when no analyze is running if its result matters (`analyze_exit` in `api.log` precedes the final receipt and
cleanup, so it alone does not prove the pipeline is idle).

## Interpretations and deviations from v0.6 (safer reading)

1. Conversion writes to `uploads/<op>/converted.wav` and not straight to the inbox. That file then gets the
   same strict check and the same no-overwrite hard link as an uploaded WAV. The 88 MiB staging quota is
   64 MiB of upload plus 24 MiB of converted output.
2. The strict WAV check also accepts WAVE_FORMAT_EXTENSIBLE, but only as plain 16-bit integer PCM (mono, mask 0 or 4,
   PCM GUID). `afconvert -f WAVE -d LEI16@16000 -c 1` writes this format for mono 16 kHz sources such as the
   fixture's M4A. The native analyzer (Python 3.12 `wave`) accepts it, and native validation stays authoritative.
3. Every header may appear at most once (not only `X-ML-*` and `Content-Length`). A GET with a `Content-Type`, any
   bytes after the head, or `Expect` gets 400 or 417. A malformed `X-ML-*` value is a 400 grammar error. An unknown
   client gets 401.
4. Status codes the spec leaves open: target > 2048 B gives 414, headers > 16 KiB or > 40 give 431, an upload
   timeout gives 408 `request_timeout`, an early EOF gives 400 `truncated_body`, a conversion wall timeout gives
   504 `timeout`, and a conversion failure gives 400 `invalid_audio`. A body-hash mismatch gives 401.
5. A reused operation id gives 409 `operation_exists` with `request_id` (use `/receipt`). An id rejected before
   reservation (`busy`, storage, size) gets no receipt (404).
6. An address on a bind interface that falls outside `allow` is skipped and logged. The server exits (code 1) only
   when no address remains. A production `allow` must be a private /24 or narrower.
7. Bind addresses are read once at start. An address change needs a restart (KeepAlive does not detect it).
8. `server.py` adds its own directory to `sys.path` because `-I` drops it. It needs this to import `sign.py`.
9. After an early error reply, the server reads at most 1 MiB or 0.5 s of unread body before it closes, so the
   reply is not lost to a TCP reset. A large body that keeps streaming after a `busy` reply can still reset the
   connection, and the client then reports outcome unknown and reads `/receipt`. A 404 there means only that no
   retained receipt exists for this sender and id: it may never have been reserved, it may have expired or been
   pruned (30 days / 2000), or it may belong to another client. It does not show that nothing ran, and it never
   authorizes an automatic POST retry.
10. If the final receipt write fails after `ml analyze` was launched, the reply is 503 `outcome_unknown` with
   `request_id` ("may have run ... do not retry"), and the receipt reads `unknown` (`error_code`
   `outcome_unknown`). The client treats it like a lost response: exit 4, poll `/receipt` later, never retry the
   POST. `storage_error` ("nothing launched") is used only when nothing was launched.
11. A RIFF/WAVE upload that fails the strict check (for example 22.05 or 44.1 kHz, stereo, 24-bit, float, an 18-byte
   `fmt ` chunk with `cbSize`, or extra chunks) is converted like MP3/M4A: `limit_exec.py` + `afconvert -f WAVE -d
   LEI16@16000 -c 1` (same rlimits, 120 s wall timeout) into `uploads/<op>/converted.wav`, then the same strict
   check (including 0 < duration <= 600 s) and no-overwrite hard link. A canonical WAV (PCM mono 16 kHz s16) skips
   afconvert. A conversion failure gives 400 `invalid_audio`; size limits, magic-byte detection and framing are
   unchanged.
12. Optional rhythm layer (not in v0.6): `rhythm=1` on `/analyze`, a `rhythm` summary in `/report`, `layer=beats|chords`
   on `/timeline`, and a `rhythm` object in `/status` (see "Optional rhythm layer"). New rhythm work and the `/status`
   field need the `rhythm` config key; `/report` and `/timeline` read stored files whenever they exist. The receipt
   records the ASR outcome only; the rhythm result lives in `results-rhythm/`. The worker runs while the API gate is
   held (another API analyze meanwhile gets 409 `busy`), and it takes the team flock after
   `ml analyze` released it, so a Mac-side analyze can win the race (rhythm `busy`). A stop during the rhythm step
   does not mark the receipt `unknown` (it is already `done`), but the worker may still be running.
