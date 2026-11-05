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
| `rhythm_worker.py` | Optional rhythm layer worker (Essentia DSP plus pinned local models: TempoCNN, Discogs-EffNet + genre head): BPM (+ half/double, + learned TempoCNN estimate), beats, key (+ 20 s sections), beat-grid chords, 1 s energy curve, repeated-section structure (A/B/...), melody feature summary (predominant pitch notes), genre/style estimate (song + 30 s windows). Runs under the Essentia python, never in the listener |
| `rmvpe_model.py` | Optional vocal melody: RMVPE inference on CPU, run by `rhythm_worker.py` as its own process (reduced from RVC-Project, MIT) |
| `btc_model.py` | Optional chords: BTC inference on CPU, run by `rhythm_worker.py` as its own process (reduced from BTC-ISMIR19, MIT) |
| `bass_onsets.py` | Optional bass layer: Basic Pitch note starts, run by `rhythm_worker.py` in its own environment |
| `other_notes.py` | Optional accompaniment layer: Basic Pitch notes of the other stem, run by `rhythm_worker.py` in its own environment |
| `adtof_model.py` | Optional drum layer: ADTOF Frame_RNN inference on CPU, run by `rhythm_worker.py` as its own process (separate implementation, reads the official checkpoint) |
| `limit_exec.py` | Sets RLIMIT_CPU 60 s / FSIZE 24 MiB / NOFILE 32, then `execv /usr/bin/afconvert -f WAVE -d LEI16@16000 -c 1 src dst` |
| `sign.py` | Shared canonical-string and HMAC helper (server, tests, Python clients) |
| `sign_vector.mjs` | Reference Node signer; prints or checks the fixed vector (`tests/vectors.json`) |
| `examples/api.example.json` | Example production config (`~/.music-lens/api.json`, mode 0600) |
| `examples/com.music-lens.lan-api.plist.template` | LaunchAgent **template** |
| `tests/` | unittest suite (fake `ml`, fake rhythm worker, throwaway cert, 127.0.0.1 test mode) plus one opt-in live test; `tests/synth_audio.py` makes synthetic WAVs for the real-worker tests |

## Tests

```sh
cd lan
/usr/bin/python3 -m unittest discover -s tests -v            # offline; uses a fake ml and a temp root
MUSIC_LENS_LIVE=1 MUSIC_LENS_FIXTURE_WAV=/abs/fixture-en.wav /usr/bin/python3 -m unittest discover -s tests -p test_live.py -v   # real wrapper, once
```

The real-worker tests in `tests/test_rhythm.py` run `rhythm_worker.py` under the Essentia python named by
`MUSIC_LENS_ESSENTIA_PYTHON` and skip cleanly without it. `MUSIC_LENS_FIXTURE_WAV` adds a real WAV to the strict
WAV check, and `NODE` (or `node` on PATH) runs the signer vector test.

Tests bind 127.0.0.1 only through the test-only `"test_mode": true` config flag. A production config (no
`test_mode`) refuses loopback, `0.0.0.0`, literal bind IPs, port 0, `limits` overrides and `test_ready_file`.

## Signing (summary)

The canonical string is the UTF-8 bytes of 8 fields joined by LF, with no trailing LF: `CLIENT`, `METHOD`, `TARGET`
(exactly as sent, non-ASCII percent-encoded), `CONTENT_TYPE` (empty for GET), `CONTENT_LENGTH` (`0` for GET),
`TIME`, `NONCE`, `BODY_SHA256`. The signature is `X-ML-Sig = hex(HMAC-SHA256(key, canonical))`, where the key is the
**32 raw bytes** decoded from the 64-hex key file. Percent-encode labels as `encodeURIComponent` does (a space becomes
`%20`, and `+` is a literal plus sign).

## Windows client note (name resolution)

On Windows, Node 22 `getaddrinfo` may return `ENOTFOUND` for `<mac-host>.local`. The client may then
resolve the name through the Windows DnsClient (`Resolve-DnsName -Type A`) or an optional configured address list.
It keeps only A records inside the `allow` /24, connects by IP with TLS `servername` set to the `.local` name, and
keeps the CA, the hostname check and the certificate pin unchanged.

## User-only install steps (not done; commands written, never run)

Do these only after approving an always-on listener (RunAtLoad + KeepAlive, en0/en1, port 8765, 192.0.2.0/24).

1. Copy the code:
   ```sh
   mkdir -p ~/tools/music-lens/api && cp ~/dev/music-lens-lan/{server.py,limit_exec.py,sign.py} ~/tools/music-lens/api/
   chmod 700 ~/tools/music-lens/api
   ```
2. Create the TLS certificate and key by hand. They must have a DNS SAN and **named-curve** P-256 parameters.
   The system LibreSSL writes explicit curve parameters by default, and Python 3.9's TLS then fails with
   "no shared cipher". Run (`<mac-host>` is `scutil --get LocalHostName`; addresses and LAN ranges in these docs are placeholders, the real ones live only in the local `api.json` and client config):
   ```sh
   mkdir -p ~/.music-lens/tls ~/.music-lens/keys && chmod 700 ~/.music-lens ~/.music-lens/tls ~/.music-lens/keys
   /usr/bin/openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -pkeyopt ec_param_enc:named_curve \
     -days 825 -nodes -subj "/CN=<mac-host>.local" \
     -addext "subjectAltName=DNS:<mac-host>.local" \
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
4. Write the config from this `lan/` folder (the example uses `/Users/YOU`; the server needs absolute paths):
   `(umask 077; sed "s|/Users/YOU|$HOME|g" examples/api.example.json > ~/.music-lens/api.json)`.
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
   sed "s|/Users/YOU|$HOME|g" examples/com.music-lens.lan-api.plist.template > ~/Library/LaunchAgents/com.music-lens.lan-api.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.music-lens.lan-api.plist
   ```
   To unload it: `launchctl bootout gui/$(id -u)/com.music-lens.lan-api`. Unloading sends SIGTERM; see
   "Stopping" below. A clean exit does not mean every analysis finished or was cancelled.

## Optional rhythm layer (beats, key, chords, energy, sections, melody)

Off unless the config has a `rhythm` key. Without it the server loads and behaves exactly as before.

```json
"rhythm": {"python": "/Users/YOU/path/to/essentia-runtime/bin/python",
           "worker": "/Users/YOU/tools/music-lens/api/rhythm_worker.py"}
```

Both paths must be absolute regular files, not group- or world-writable, and `python` must be executable; any other
key or value stops the server (config error). `examples/api.example.json` leaves the key out on purpose: turning the
layer on is an owner decision (copy `rhythm_worker.py` next to `server.py` first).

API:
- `POST /analyze?...&rhythm=1` opts in (`rhythm=0` or no flag = unchanged behaviour, byte for byte). The flag is part
  of the signed TARGET like every other query field. With no `rhythm` config the request gets 400
  `rhythm_unavailable` before anything is staged (no receipt). Any other value is 400 `bad_request`.
- After a successful `ml analyze` (receipt already `done`), the server runs `<python> -I rhythm_worker.py
  --words-stdin <inbox>/api-<op>.wav` by argv on the same promoted WAV (worker 0.4.3: stdin = the result's ASR words
  for `lyric_repeats`, see "Lyric repeats" below; before 0.4.3 the WAV was the only argument), with the same child env (PATH `/usr/bin:/bin:/usr/sbin:/sbin`),
  its own session, and a wall timeout of `rhythm_timeout` (300 s, capped by the time left before the handler's 800 s
  deadline) that kills the process group. It takes the team flock `<root>/.analyze.lock` non-blocking (the same
  file the `ml` wrapper locks) and passes the locked descriptor to the worker, so the lock lives as long as the worker.
  The worker also bounds itself (SIGALRM 330 s, RLIMIT_CPU 600 s) in case the server dies first.
- Rhythm never fails the analyze: the reply stays 200 with the ASR fields unchanged plus `rhythm`:
  - success: the same compact summary as `/report` plus `cache_hit` and `stored` (no beat or chord rows; page
    them with `/timeline` `layer=beats|chords|energy|melody|genre|bass|other|drums`);
  - `{"status":"busy","code":"busy"}` when the team lock is held (never waits);
  - `{"status":"timeout","code":"timeout"|"no_time_left"}`;
  - `{"status":"failed","code":...}` with the worker's error code (`invalid_audio`, `too_short`, `analysis_failed`,
    `dependency_missing`, `timeout`), or `invalid_output`, `internal_error`, `server_misconfigured`,
    `lock_unavailable`, `no_result_id`, `shutting_down`.
- The worker JSON is stored durably (tmp 0600 + F_FULLFSYNC, replace, directory fsync) as
  `<root>/results-rhythm/<result_id>.json` (directory 0700), plus a server-added `worker_sha256`: the SHA-256 of the
  configured worker file, hashed once when the server starts. A later analyze with the same `result_id` (an `ml` cache
  hit, same audio and settings) reuses the stored file (`cache_hit: true`, nothing runs) only when its
  `worker_sha256` equals the current one and (0.4.3) its server-added `words_sha256` equals the SHA-256 of the
  words payload the server would pass now (so an ASR re-run that changes the words of the same `result_id`
  recomputes; files without it are recomputed, which older files are anyway because the worker hash changed). Otherwise (another worker version, or a file from before this field, i.e.
  every 0.1.0/0.2.0 file) the worker runs again and the file is replaced atomically on success; a busy, timeout or
  failed re-run leaves the old file in place. A stored file with the current worker is also recomputed when its
  optional `tempo_cnn` is `unavailable` (any reason, e.g. model restored later) or `failed` with `inference_error` /
  `dependency_missing`, since those causes can change without a worker change; deterministic `too_short`,
  `non_finite`, `inconsistent_output` and (0.4.1) `no_audible_patches` stay cached. The same applies to a melody
  `failed` with `analysis_error` or `dependency_missing` (recomputed; `non_finite` cached). Likewise (0.4.3) a `lyric_repeats` `unavailable` with reason
  `result_unreadable` (the ml result file could not be read: may work next time); every other lyric outcome is
  deterministic for the same words and stays cached. And (0.5.0) a `genre` `unavailable` for any reason except
  `too_short` / `no_audible_patches` (a Discogs model file missing, unreadable or off its pin) or `failed` with
  `inference_error` / `dependency_missing`; `non_finite` and `class_count_mismatch` stay cached. While the model stays missing, every rhythm request therefore
  reruns the worker (about 15 s for a 4 min song). Only the worker file is tracked: a changed Essentia python with the same
  worker file is not detected (restart is needed after editing the worker in place). If the worker file cannot be
  read at start, its hash is null and nothing is reused (every rhythm request recomputes); that alone does not make
  the run fail, the run then succeeds or fails on its own (a worker that is really missing fails at spawn). `/report` and `/timeline` read whatever
  is stored and never recompute the worker (the summary's `overview` is derived from the stored file at read time,
  see "Overview"). A store failure still returns the
  data with `stored: false`. The files are not pruned automatically (about 35-45 KB per 10 minutes of audio with
  worker 0.3.0: 34,114 B for the 600 s loop, 41,434 B for a 600 s file with 40 sections; worker 0.4.0 adds the
  melody notes, about 54 B per note: 46,911 B for the 600 s loop (225 notes), 103,330 B for the 600 s melody track
  (1200 notes); at most 6000 notes, ~330 KB; worker 0.5.0 adds `genre`, about 1 KB plus ~200 B per 30 s window,
  1,189 B for 30 s, 4,864 B for the 600 s loop (20 windows); cap `rhythm_output` 4 MiB).
- `GET /report/<id>` adds a `rhythm` summary (`status`, `bpm`, `bpm_confidence`, `bpm_confidence_band`,
  `bpm_estimates`, `bpm_alternatives`, `bpm_alternatives_note`, `double_tempo_evidence`,
  `double_tempo_evidence_note`, `tempo_cnn`, `key`, `key_sections`, `key_change_count`,
  `beat_count`, `chord_segment_count`, `chords_grid`, `energy_summary`, `sections`, `sections_method`,
  `sections_reliability`, `melody`, `lyric_repeats`, `overview`, `genre`, `downbeats`, `duration`, `tool`, `warnings`) when a stored file
  exists, re-serializing the native JSON (number formatting may change). Files stored by worker 0.1.0 lack the
  0.2.0 fields and still load; the summary then has `bpm_alternatives`, `bpm_alternatives_note`, `key_sections`,
  `key_change_count` and `chords_grid` set to `null` (they are not recomputed; old chord rows stay frame-level).
  Files from 0.1.0 and 0.2.0 likewise give `double_tempo_evidence` and `double_tempo_evidence_note` as `null`, and
  files from before 0.2.2 give `tempo_cnn: null`, and files from before 0.3.0 give `energy_summary`, `sections`,
  `sections_method` and `sections_reliability` as `null`, and files from before 0.4.0 give `melody: null`, and files
  from before 0.4.3 give `lyric_repeats: null` and `overview: null`, and files from before 0.5.0 give `genre: null`
  (summary `genre` = the stored one without `windows`, plus `window_count`). `structure_debug` is never in the summary (stored file only). A damaged file gives `rhythm: {"status":"failed","code":"invalid_result"}`.
  No file: native bytes unchanged.
- `GET /timeline/<id>?layer=beats|chords|energy|melody|genre|bass|other|drums` is served by the server from the stored file with the native rules:
  `start`/`end` 0..600 (default 0 and 600, `end >= start`), rows overlapping the range, `limit` 1..200 (default 100),
  `cursor` <= total, a ~43 KB page byte cap, `truncated` and `continuation`. Beats rows are `{"time"}`, chords rows
  `{start, end, label, strength}`, with `display` from worker 0.10.0 and `one_beat: true` on some BTC rows (with `reliability`: `low_reference`, or
  `reference_candidate` for BTC chords), energy rows `{time, value}` (with `unit`
  and `step_s`; a row covers `[time, time + step_s)` and is selected when its `time` lies in `[start, end]`), melody
  rows `{start, end, midi, note}` (selected like chords: overlapping the range; with `reliability: "low_reference"`
  and `layer_status` = the stored melody `status`, so a `failed` melody gives `done`-shaped paging with no rows and
  `layer_status: "failed"`), genre rows `{start, end, patches, top}` (the 30 s windows overlapping the range, with
  `reliability: "model_estimate"` and `layer_status` = the stored genre `status`; not `done`: no rows).
  No stored file: 404 `rhythm_not_found`. `layer=energy` on a file stored before worker 0.3.0 (no `energy` field)
  gives the same 404 `rhythm_not_found`: the request is valid, that layer is just not stored for this result, and a
  new `rhythm=1` analyze recomputes it (the worker hash changed); `invalid_argument` would wrongly blame the
  request. `layer=melody` on a file stored before worker 0.4.0 and `layer=genre` before 0.5.0 give the same 404. Sections are in the summary only (at most 40 rows), not a timeline layer.
  `words`/`emotion` still go to `ml timeline` unchanged.
- `GET /status`: when the layer is configured, adds `rhythm: {configured, python_present, worker_present,
  worker_sha256}` (file checks only, Essentia is not run; `worker_sha256` is the start-time hash, null if unreadable). Not configured: native bytes unchanged (no `rhythm` key means unavailable).

`bpm_confidence_band` maps the native confidence to Essentia's documented BeatTrackerMultiFeature bands:
`very_low` [0, 1), `low` [1, 1.5], `good` (1.5, 3.5] (Essentia: about 80% AMLt), `excellent` above 3.5. It is
Essentia's guide, not a calibration on the team's music.

Worker output (worker 0.4.1): `bpm`, `bpm_confidence` (RhythmExtractor2013 multifeature, native 0..~5.32,
uncalibrated), `bpm_estimates` (top 5 shares), `bpm_alternatives` (`[bpm/2, bpm*2]`, 2 decimals, only values within
40..300; empty when bpm is 0) with `bpm_alternatives_note`, `double_tempo_evidence` with
`double_tempo_evidence_note` (diagnostic, see below), `tempo_cnn` (learned estimate, see below), `beats` (s, ms rounding),
`downbeats: {"status":"not_provided"}`, `key` (`tonic`, `scale`, `strength`, `profile: "bgate"`), `key_sections`
(rows `{start, end, tonic, scale, strength}`, see below), `key_change_count` (sections - 1), `key_section_window`
(20), `chords` (see below), `chords_grid` (`beat` or `frame`), `chord_frame_segment_count` (frame-level segment
count before the beat vote, stored file only), `chords_reliability` (`low_reference`, or `reference_candidate` for BTC
chords), `chord_vocabulary`, `sample_rate` 44100 (input
resampled from 16 kHz because RhythmExtractor2013 assumes 44.1 kHz), `duration`, `tool` (name, version,
`essentia_version`), `warnings` (`short_audio` < 10 s, `near_silent`, `no_beats`, `low_bpm_confidence` < 1.5,
`low_key_strength` < 0.5, `key_changes` when there is more than one key section, `no_chords`,
`double_tempo_evidence_unavailable`, `no_sections`, `sections_single`, `tempo_cnn_patch_count_mismatch` (0.3.1-0.4.0
only), `no_melody`, `melody_failed`, `melody_scale_degrees_unavailable`,
`melody_notes_capped`, `lyric_repeats_capped` (0.4.3), `lyric_repeats_approx_capped` (0.4.4)), `energy`, `energy_summary`, `sections`, `sections_method`, `sections_reliability`
(`low_reference`; `reference_candidate` with the section model, see "Energy and sections" and "Sections from All-In-One" below), `melody` and `melody_notes` (see "Melody features" below), and (worker 0.4.3) `structure_debug` (stored file only, see
"Energy and sections") and `lyric_repeats` (see "Lyric repeats"), and (worker 0.5.0) `genre` (see "Genre"). Audio under 1 s is refused (`too_short`).

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
synthetic file (C-Am-F-G loop) took about 7.65 s and ~1.10 GB peak RSS on this Mac with worker 0.2.2 and the
model present (3 runs; the same worker with the model unavailable: 6.8 s, 0.97-0.99 GB, equal to worker 0.2.1, so
TempoCNN adds about +0.85 s wall / +1.45 s CPU and +0.12 GB peak RSS). Earlier: worker 0.2.1 6.8 s; 0.2.0 on
the same file: 6.3 s, 0.99 GB).

- Tempo octave: the metrical level is ambiguous. On one real song (216 s, published tempo 220) the tracker said
  109.94 BPM and every `bpm_estimates` entry was ~110, so 220 never appeared there. `bpm_alternatives` lists half and
  double without choosing; compare them with a published tempo or with your own listening. No heuristic warns.
  Team check with worker 0.1.0 (8 songs, references from published charts): 87, 102, 145, 164 and 170 BPM came out
  right; 185, 200 and 220 came out at exactly half. The confidence band did not flag the halved ones.
- `double_tempo_evidence` (worker 0.2.1) is a diagnostic for the halving above, uncalibrated and untested on real
  songs. It makes no decision and changes neither `bpm` nor `bpm_alternatives`; do not pick a tempo from it alone.
  Idea: if the true tempo is double the tracked one, strong onsets also fall at the midpoints between tracked beats.
  Method: an onset-strength envelope (L1 half-rectified spectral flux, Essentia `Flux`, Hann 1024-sample frames,
  hop 512 = 11.6 ms at 44.1 kHz, divided by its maximum so values are 0..1) is max-pooled over +-30 ms (`window_ms`)
  at every tracked beat and at the midpoint of every consecutive beat pair whose gap is 0.12..1.5 s (`pairs`).
  +-30 ms absorbs tracker jitter and the one-frame flux lag and stays far below the shortest half-beat (144 ms at
  208 BPM), so the windows never overlap. Flux was chosen over HFC because it reacts to low-frequency attacks (kicks)
  as well as bright ones, and needs no phase. Fields: `beat_onset_median` (median over both flanking beats of every
  pair), `mid_onset_median`, `mid_to_beat_ratio` (mid median / beat median, 0..1000), `mid_strong_fraction` (share
  of pairs whose midpoint onset is >= `mid_strong_threshold` 0.5 x the mean onset of its two flanking beats, and
  > 0), `method`. Ratio and fraction are `null` with warning `double_tempo_evidence_unavailable` when there are fewer
  than 7 usable pairs (~8 beats) or the beat median is under 0.001.
  Synthetic numbers (30 s each): kicks on every beat at 100 BPM only gave ratio 0.0 / fraction 0.0; the same kicks
  plus equally strong kicks exactly halfway (an effective 200 BPM pulse) gave 1.011 / 1.0, and the tracker still said
  100.04 BPM for both. The 120 BPM click track gave 0.0 / 0.0; the C-Am-F-G loop with a soft click gave 0.383 / 0.276
  (60 s modulation track 0.37 / 0.169, 600 s loop 0.357 / 0.242): sustained partials leave a steady flux floor of
  about 0.2-0.3 of the maximum between onsets, so the baseline on real music may be nonzero (real-song baseline unmeasured). The team will evaluate it
  on the same 8 songs (87-220 BPM, published charts): run them with `--rhythm` and compare the values for the five
  right ones (87-170) with the three halved ones (185, 200, 220). Only a clear, consistent separation there would
  justify proposing a threshold, in a later version.
- `tempo_cnn` (worker 0.2.2) is a second, learned tempo estimate next to the DSP one; it changes neither `bpm` nor
  `bpm_alternatives`, and the rhythm `status` does not depend on it. Never pick a tempo from one source alone.
  Model: TempoCNN `deeptemp-k16-3` (Schreiber & Mueller, SMC 2019; Essentia model by Pablo Alonso, version 1,
  2020-08-04), source https://essentia.upf.edu/models/tempo/tempocnn/deeptemp-k16-3.pb, SHA-256
  `21c328332a221695dd6e8572728c617373064df882e8f81da6d88dc3a821e3b3`, expected at
  `~/tools/music-lens/models/tempocnn/deeptemp-k16-3.pb` (path and hash are constants in the
  worker; the model json sits beside it). Training data per the model json: Extended Ballroom, LMDTempo and
  MTGTempo (3826 30 s, 3611 30 s and 1158 2 min excerpts). License: the json has no license field; Essentia models
  are published under their own terms, so check them before any non-team use (no specific license is verified here).
  Before loading, the worker opens the file with O_NOFOLLOW (a symlink, directory or FIFO is refused), caps it at
  64 MiB and compares its SHA-256 with the pin; otherwise `{"status":"unavailable","reason":...}` (`model_missing`,
  `not_regular_file`, `model_unreadable`, `model_too_large`, `sha256_mismatch`) and everything else is computed as
  before. A load or inference error gives `{"status":"failed","code":...}` (`inference_error`,
  `dependency_missing`, `non_finite`, `inconsistent_output`, or `too_short` for audio under ~12 s, which holds no
  model patch); the worker never fails because of it. The file could still change between hashing and loading (the
  same trust as the worker file). Input: the 16 kHz source resampled to 11025 Hz (Essentia `Resample`). TempoCNN
  (`majority` aggregation) gives `bpm` and one BPM per ~11.9 s patch (hop ~5.9 s): `local_bpm_median`, `_min`,
  `_max`, `local_count`; `top` holds up to 3 `{bpm, prob}` classes of the mean softmax over all patches, from
  `TensorflowPredictTempoCNN` on the same audio and model. Verified here: the softmax has shape (patches, 256), rows
  sum to 1, and 30 + argmax equals TempoCNN's local BPM in every patch, so class i = 30 + i BPM (30..285; the json
  text says "the last one ... 286", which is one off for 256 classes); TempoCNN's own `localTempoProbabilities` is
  only each patch's maximum, not the distribution. The worker re-checks the argmax relation on every run
  (`inconsistent_output`). Worker 0.3.1 (after one real song, ~87 BPM, ~4 min, came back `inconsistent_output`
  with 0.2.2 while 7 others were done; that audio could not be re-run here): both calls now get `batchSize` 64,
  `patchHopSize` 128 and `lastPatchMode` "discard" explicitly. Measured on synthetic audio 12-600 s: the patch
  counts already matched at every length (39 patches for 240-241 s, 99 for 600 s), but the predictor's default
  `batchSize` 16 (TempoCNN's is 64) changed the softmax by up to 6e-7, while 64 reproduced TempoCNN's per-patch
  maxima exactly; a near-tie between two classes could therefore flip the argmax (the likely cause, unproven). A
  patch also counts as consistent when its local BPM's class probability is within 1e-6 of the row maximum (about
  2x the observed batch noise, far below the 3-decimal `top` probabilities). `inconsistent_output` now carries
  `detail`: `{code: "class_count_mismatch", row, classes}`, `{code: "patch_count_mismatch", local, predictions}`
  (difference above one), `{code: "argmax_mismatch", patch, local_bpm, argmax_bpm, prob_gap|null}` (first failing
  patch; `prob_gap` = row maximum minus the local BPM's class probability, null when that BPM has no class) or
  `{code: "global_out_of_range", bpm}`. Files without `detail` stay valid. Patch counts differing by exactly one
  (never observed, kept as a guard) give `done` over the shared leading patches (both start at frame 0 with the same
  hop) plus warning `tempo_cnn_patch_count_mismatch`; `local_count` and the local BPM fields still use all of
  TempoCNN's patches. Plus `model`, `model_sha256`, `note` ("learned estimate, uncalibrated on team songs;
  compare with bpm and bpm_alternatives"). Synthetic numbers (worker 0.2.2; `bpm` / `tempo_cnn.bpm` / top 3): click
  120 30 s 120.0 / 120 / 120 .817, 121 .171, 161 .003; C-Am-F-G loop 30 s 120.0 / 120 / 120 .742, 121 .244, 60 .003;
  modulation 60 s 120.02 / 120 / 120 .786, 121 .2, 116 .003; kick100 100.04 / 100 / 100 .813, 150 .125, 149 .018;
  kick100mid (effective 200 BPM pulse) 100.04 / 100 / 100 .863, 101 .025, 99 .014; 600 s loop 120.0 / 120 (local
  120-121, 99 patches). So on the midpoint-kick track the model also chose 100, not 200: it is not a fix for the
  halving above by itself. Real songs: not measured; the team will compare it on the same 8 songs.
- `tempo_cnn` in worker 0.4.1: one source, near-silent patches excluded. Evidence: a real song (274 s, about 21 s of
  near-silence at the end) came back `inconsistent_output` / `argmax_mismatch` on patch 44 (local 160, argmax 161,
  `prob_gap` 0.08), and the synthetic `silent_edges` track (10 s silence, 60 s music, 20 s silence) reproduced it
  with 0.4.0 (patch 13, the last one, inside the silent tail; local 161, argmax 155, gap 0.04). So TempoCNN and
  `TensorflowPredictTempoCNN` disagree for real on near-silent input; the 0.3.1 batch-size explanation was wrong.
  0.4.1 no longer calls `TempoCNN`. Everything comes from the predictor's per-patch softmax (same model, `batchSize`
  64, `patchHopSize` 128, `lastPatchMode` discard): local BPM = 30 + argmax per patch (ties: lower class); `bpm` =
  the most frequent local BPM (Essentia documents TempoCNN's global tempo as an aggregation of `localTempo` and
  recommends majority voting; that is what 0.4.1 does itself), ties by higher mean probability, then lower BPM;
  `top` = mean softmax as before. First, each patch whose span (start 5.944 i s, length 11.935 s: 256 frames of
  1024 samples, hop 512, at 11025 Hz, patch hop 128 frames) has more than half of its 1 s energy windows below
  -60 dBFS (the sections rule below) is excluded; `silent_patches` counts them and `local_count` is the audible
  patches. A patch-RMS rule was tried first and kept the patch of 10 s silence + 2 s music, which voted 30 BPM. All
  patches excluded: `{"status":"failed","code":"no_audible_patches"}` (deterministic, cached). The cross-call
  consistency failures (`patch_count_mismatch`, `argmax_mismatch`, `global_out_of_range`) can no longer occur;
  stored 0.3.1/0.4.0 files with those `detail`s stay valid, and so do `done` rows without `silent_patches`. On every
  audible synthetic track (click, kick100, kick100mid 30 s, abab 60 s, chords 241 s, C-Am-F-G loop and melody 600 s)
  0.4.1 gives the same `bpm`, `top` and `local_count` as 0.3.1/0.4.0; on `silent_edges` it gives 120 with 4 of 14
  patches excluded (local 120-121). Real songs: not re-measured.
- Key sections are a DSP estimate on nominal 20 s windows (a final tail under 10 s is merged, so the last window can
  approach 30 s). Window resolution is not a measured error bound: a boundary lands on a window edge, and a window
  straddling a change can report a blended or third key. Synthetic test: C major 0-30 s then
  D major 30-60 s gave C 0-20, G 20-40 (the straddling window; G major is the union of the two scales), D 40-60;
  the single global `key` said G major.
  Relative keys (C major / A minor) share notes and can alternate. One real song with a known modulation (E major,
  then another key area ~160-180 s, F major from 180 s) motivated the field; it was not re-measured here.
- Beat-grid chords remove sub-beat flicker (that real song had 324 frame-level segments, median 0.33 s, shorter
  than one beat) but also any real chord change shorter than a beat; head/tail can be shorter than a beat. On the
  synthetic C-Am-F-G loop beat-grid and frame-level segments agree (2 s each); on the 30 s click track (no harmony)
  frame labels flicker and the beat vote cuts 32 segments to 11. Synthetic music did not make the frame labels
  flicker (the 2 s ChordsDetection window smooths them), so the reduction on real music is untested here.

### Energy and sections (worker 0.3.0)

Purpose: song-level form for "compose using this song as a reference" (where material repeats, where it gets loud),
not a transcription. There are no downbeats, and named parts only with the section model ("Sections from All-In-One").

- `energy` `{unit: "dBFS", step_s: 1, values}`: RMS level of the 16 kHz source per non-overlapping 1 s window
  `[i, i+1)` (a last partial window of at least 0.5 s is kept, a shorter one dropped; at most 600 values), in dB
  relative to digital full scale, 2 decimals, floor -120 (silence). RMS dB was chosen over Essentia `Loudness`
  (Stevens power law, unitless) because dBFS is a plain, reproducible unit agents can compare across songs of the
  same master; it is not perceptual loudness (no K/A weighting: bass-heavy passages read louder than they sound,
  and it is not LUFS).
- `energy_summary` `{unit, window_s: 8, min, max, median, peaks, quietest, silence_db, silence_s}`
  (`silence_db`/`silence_s` from worker 0.4.1): `peaks` = the 3 loudest
  non-overlapping 8 s windows (`{start, end, mean}`, plain mean of the dB values, sliding in 1 s steps, chosen
  greedily, loudest first, ties earliest), `quietest` = the lowest 8 s window. 8 s is about 4 bars at 120 BPM, one
  phrase: long enough to ignore single hits, short enough to sit inside one section. Audio under 8 s uses one
  window of everything. Worker 0.4.1: peaks and quietest consider only windows with no value below `silence_db`
  (-60 dBFS), so a silent intro or tail is no longer the "quietest" part (the real 274 s song gave quietest
  257-265 at -120 with 0.3.1); `silence_s` = seconds of 1 s values below it, and `min` still shows the floor. No
  window without silence (all silent, or audio under 8 s holding a silent second): `peaks: []`, `quietest: null`.
- `sections` `[{start, end, label, energy_mean, top_chord, similarity_to_first}]`, contiguous from 0 to
  `duration`, at most 40. With the section model configured see "Sections from All-In-One"; otherwise the beat method
  (`sections_method` states it in short form):
  1. Beat-synchronous features: per beat interval the mean 12-bin HPCP chroma (the chord path's frames, frame
     4096, hop 2048 at 44.1 kHz) and the mean MFCC 2-13 (coefficient 1 = overall level left out, since energy is
     reported on its own). MFCCs are z-scored per coefficient over the song; chroma and timbre are each
     L2-normalized and weighted equally, so the self-similarity matrix is 0.5 cos(chroma) + 0.5 cos(timbre).
  2. Foote checkerboard novelty with a Gaussian-tapered kernel of 2 x 16 beats (sigma 8 beats), normalized so that
     novelty is (same-side minus cross similarity) / 2 on a -1..1 scale. 16 beats is about 4 bars of 4/4: a
     typical 4-bar loop then sits inside one kernel half, so chord changes inside a loop do not look like section
     changes. Novelty is computed only where the full kernel fits: truncated kernels near the ends compared part of
     a loop with another part and produced false boundaries (synthetic: up to 0.27). So no boundary falls within 16
     beats (8 s at 120 BPM) of either end; a shorter intro or outro joins its neighbour.
  3. Boundaries: novelty >= 0.15 that is the maximum of its +-8 beat neighbourhood (ties earliest), accepted
     greedily by novelty when >= 8 beats (~2 bars, the minimum section length) from every accepted one; at most 39.
     Synthetic calibration: true A/B changes gave 0.33-0.38; inside one loop and texture novelty stayed <= 0.12.
     The threshold is not calibrated on real songs.
  4. Labels: per section the mean chroma and the mean z-scored MFCC; similarity = 0.5 x cosine(mean chroma) +
     0.5 x exp(-d^2/2) with d the RMS difference of the mean MFCCs in z units (0..1). In time order a section joins
     the label group whose first section it is most similar to when that similarity is >= 0.75, else it gets the
     next letter (A, B, C, ... in order of first appearance; after Z: AA, AB, ...). Adjacent sections may share a
     label (not merged: the boundary had novelty but the material matches). `similarity_to_first` is that
     similarity (1.0 for a group's first section).
  5. `energy_mean`: mean of the energy values whose window midpoint lies in the section; `top_chord`: the beat-grid
     chord label with the most time inside the section (ties: earliest), null when no chord overlaps.
  6. Silence (worker 0.4.1): a section with more than half of its 1 s energy windows (by midpoint) below -60 dBFS
     gets `label: "silence"` and `similarity_to_first: null`; it takes no letter and is no grouping reference, so
     letters A, B, ... count only audible sections (worker 0.4.0, same sections code as 0.3.x, labelled the silent intro and tail of `silent_edges` with
     one letter, A, and the music B and C). -60 dBFS is far below soft music (synthetic soft loops sit near -26 dBFS) and
     far above digital silence (-120 floor); room tone and fades are not calibrated. Boundaries are unchanged
     (same novelty, threshold 0.15, grouping 0.75): silence gets its own section only where the novelty finds a
     boundary (16 beat intervals of context needed; on `silent_edges` the tracker kept placing beats through the
     silence and the boundaries fell within 0.04 s of the true edges). A shorter silent head or tail stays inside its
     neighbour. All silent with a beat grid: every section is `silence`.
  No beats: `sections: []` and warning `no_sections`. Fewer than 32 beat intervals: one section `A` and warning
  `sections_single`. Deterministic (fixed parameters, no random state).
- `structure_debug` (worker 0.4.3, stored file only, not in the summary or a timeline layer; calibration data for a
  later detector change, the parameters above are unchanged): `{kernel_beats: 16, min_gap_beats: 8,
  novelty_threshold: 0.15, group_threshold: 0.75, novelty: {times, values}, peaks: [{time, value, selected}],
  section_similarity}`. `novelty` = the Foote value at every beat boundary where the full kernel fits (times =
  beat times, values 4 decimals, one per beat so at most the beat count, about 2080 for 600 s at 208 BPM; not
  downsampled). `peaks` = every local maximum under the boundary rule's +-8 beat neighbourhood and end margins but
  without the 0.15 threshold (all selected ones, then the strongest others, at most 300, sorted by time);
  `selected` = became a section boundary (so the selected times are exactly `sections[1:].start`).
  `section_similarity` = the full symmetric matrix (3 decimals, 0..1) of the label similarity between every pair
  of sections, silence included, one row per section (<= 40 x 40); null when fewer than 32 beat intervals (one
  section, no features). `null` without beats. Server checks: exact keys, bounds, novelty times sorted inside the
  duration, every peak a novelty point, selected peaks >= the threshold and equal to the section boundaries,
  matrix square, sized to `sections`, symmetric. Size (600 s synthetic, 120 BPM): 20.7 KB with one section, 27.1 KB
  with 40 sections (matrix 40 x 40), stored files 67.8-124.3 KB; worst case (208 BPM, 300 peaks, 40 sections)
  estimated under 60 KB, far below the 4 MiB `rhythm_output` cap.
- Honest limits: labels are similarity groups, not verse/chorus/bridge, and two different parts with the same
  chords and timbre get one letter; a part that differs only in melody or lyrics is invisible to these features.
  Boundaries sit on beat times and need 16 beat intervals of context at both ends (about 8 s at 120 BPM, about
  5 s at 200 BPM). A section whose beats carry no chroma (silence, very quiet or peak-free material) gets chroma
  similarity 0, so even an identical repeat scores at most 0.5 and receives a new letter (unless it is quiet enough to
  be `silence`, 0.4.1). Real-song check (8 J-pop songs, stored 0.4.3 `structure_debug`, read-only): sweeping the
  novelty threshold from 0.04 to 0.20, the share of `lyric_repeats` block edges with a boundary within +-5 s equals
  the chance level implied by the boundary density at every threshold (0.15: 26% vs ~25% chance; 0.06: 62% vs ~70%),
  and novelty near lyric edges is mostly 0.02-0.10: the boundaries do not find verse/chorus edges, so use
  `overview` (lyric repeats as the backbone) for song structure. `sections_reliability:
  "low_reference"`. Synthetic results (worker 0.3.0): A-B-A-B in 15 s blocks (A = C-Am-F-G with 3 soft partials,
  B = E-C#m-A-B with 8 sawtooth-like partials at about +8 dB, soft 120 BPM pulse) gave A 0-15, B 15-30, A 30-45,
  B 45-60 (boundaries exactly on the block edges), energy_mean A -26.68 / B -18.77 dB, similarity_to_first >=
  0.999, peaks 15-23, 45-53, 23-31 and quietest 30-38; the same form over 600 s gave 40 sections ABAB... on every
  15 s edge; the C-Am-F-G loop (30 s and 600 s), the click and kick tracks gave one section A; the C major ->
  D major modulation track (same timbre) gave A 0-27.98 / B 27.98-60 (true change at 30 s). 600 s runtime is
  unchanged within noise (worker 0.3.0 7.73-7.86 s and 1.09-1.12 GB peak RSS vs 0.2.2 7.71-7.78 s and 1.10 GB on
  the same file, same session; worker 0.3.1 with TempoCNN batch 64: 7.70-7.71 s, 1.09-1.10 GB).

### Melody features (worker 0.4.0)

Purpose: the shape of the tune for "compose using this song as a reference" (range, note density, step/leap
balance, which scale degrees it sits on, where it climbs). Not a transcription and not a vocal track.

- What is tracked: the **predominant pitch of the full mix**. On a typical song that is usually the lead vocal, but
  intros, instrumental breaks and solos track whatever instrument is loudest, and accompaniment-only passages track
  some line of the accompaniment (synthetic: the C-Am-F-G loop with no melody gave 11 "notes" in 30 s and 225 in
  600 s, on the chord roots F2-C3). There is no vocal separation and no voicing classifier beyond Melodia's own.
- Method: EqualLoudness, then `PredominantPitchMelodia` (Salamon & Gomez) directly on the 16 kHz source with
  `frameSize` 1024 (64 ms) and `hopSize` 128 (8 ms), `maxFrequency` 8000 (the default 20000 is above the 16 kHz
  Nyquist); everything else default (salience 55-1760 Hz, `minFrequency` 80 Hz, so MIDI about 40-93). The
  algorithm is designed for 44.1 kHz (frame 2048 = 46 ms, hop 128 = 2.9 ms); its time parameters are in ms and
  cents/ms, so they do not depend on the rate. Checked on synthetic lines (harmonic tones, 0.5 s notes, over soft
  chords): 16 kHz direct with frame 1024 and with 2048 both recovered every note of a 30 s A4-A5 line; with frame
  1024, 20 of 24 notes of a 10 s C3-C4 line, while the same audio resampled to 44.1 kHz with the defaults found
  only 12 of 24; frame 512 split notes of the A4-A5 line (63 for 60). Then `PitchContourSegmentation` (same rate and hop, defaults: minimum note 0.1 s,
  60 cents pitch distance, rms threshold -2) gives notes; `midi` is rounded to the nearest semitone (A4 = 69 =
  440 Hz, no tuning estimate), `note` is the sharp name (`C#4`, middle C = `C4`). Worker 0.4.2 adds the contour
  median and the note merge below; every statistic and the stored notes use the merged notes.
- Note smoothing and merge (worker 0.4.2). Evidence: on 8 real songs (Windows full mixes) 0.4.1 notes averaged
  0.155-0.248 s, 75-90% touched the next note (gap <= 30 ms) and 23-34% of touching pairs moved +-1 semitone; held
  sung notes came out as 70/72/70/72 every ~0.1 s (~5 Hz vibrato around 71) or as several same-pitch pieces 20 ms
  apart. Synthetic held notes with 5.5 Hz vibrato gave 12 pieces (+-50 cents) and 53 pieces (+-100 cents, mean
  0.096 s) for 4 notes. Steps, all deterministic:
  1. Before segmentation, the voiced pitch contour is median-filtered in cents over 25 frames (0.2 s), inside
     each voiced run only (edges padded with the nearest value). 0.2 s is one period of 5 Hz vibrato: the median of
     a symmetric oscillation over a period is its centre, while a step whose plateau is longer than half the window
     (0.1 s) keeps its edges. Chosen over merging alone because it removed vibrato that merging could not (+-100
     cents at 6 Hz rounds to an uneven 67/68 alternation with long pieces), and it left a 0.12 s stepwise run, a
     mordent and a turn note-for-note unchanged.
  2. Same-pitch join: consecutive notes with the same `midi` and gap <= 30 ms become one.
  3. Oscillation join: a run of consecutive notes, every gap <= 30 ms, pitch span <= 2 semitones, with at least
     2 sign changes between consecutive nonzero pitch moves (the A-B-A-B pattern, 3 alternating moves; the brief's
     "3 reversals" is read as this pattern, since a +-100 cent held note gave 72-71-70-71-72-71, which has 2 sign
     changes), where every piece is shorter than 0.2 s except that, in a 2-semitone run, pieces at the middle pitch
     may last up to 0.5 s (the rounded pitch sits on the centre for most of a +-100 cent cycle). It becomes one note.
     From each start the longest qualifying run wins, left to right. Its pitch is the duration-weighted **mean**,
     rounded half up: the median of an even 70/72 alternation is one of the extremes, the mean is the centre 71.
     All thresholds (30 ms, 0.2 s, 0.5 s) are compared in integer milliseconds of the ms-rounded rows, and the
     mean uses integer ms weights with an exact half-up, so the same figure merges the same way at any time offset
     (review fix: float differences such as 10.2 - 10.0 = 0.1999999999999993 merged four 200 ms notes at 10 s but
     not at 0 s, and a 60.5 mean rounded to 60 at some offsets).
  4. Same-pitch join again, so a merged vibrato absorbs touching longer notes of its own pitch.
  Kept intact: monotonic runs, oscillations wider than 2 semitones, one-turn figures (mordent 72-74-72, turn
  74-72-71-72), alternations with long pieces (77-78-76(1.1 s)-78-76(1.1 s)), and pieces more than 30 ms apart. A
  trill or neighbour-note figure within 2 semitones with pieces under 0.2 s (72-74-72-74) is merged: at note level
  it cannot be told from wide vibrato. `melody.note_count_raw` = segmentation output after step 1, before steps
  2-4 (the server checks it is an integer >= `note_count`; 0.4.0/0.4.1 files have no such field and stay valid).
  Synthetic results (0.4.1 -> 0.4.2 notes, mean note length): `vibrato50` 12 -> 4 (0.476 -> 1.46 s, all four held
  notes at the right MIDI); `vibrato100` 53 -> 6 (0.096 -> 0.971 s: the 4 held notes plus two ~0.1 s glide pieces
  where Melodia bridges the 40 ms rest between notes and the median window straddles both pitches); `ornaments`
  (0.12 s run, mordent, turn) 22 -> 22, identical; `repeats` (72 x4, 0.15 s rests) 4 separate notes, unchanged;
  `melody` 60 -> 60, identical; `melody_ab` 125 -> 118 (raw 119; 120 written; mean 0.447 -> 0.475 s, A sections
  33/32 -> 29 notes for 30 written). Runtime at 600 s unchanged within noise (melody track 9.23-9.24 s vs 9.21-9.23
  s, loop 8.99-9.00 s vs 8.99 s; 1.13-1.14 GB both). Calibrated on synthetic audio plus the 8-song observation
  only; how often real melodic trills or quick neighbour-note figures are merged by mistake is unmeasured. Rests
  shorter than about 0.1 s are bridged by Melodia's contour (a 72 x4 with 50-80 ms rests already came out as
  1-4 pieces of 72 in 0.4.1), so such re-articulations now become one note.
- `melody` (summary) on success: `status: "done"`, `method`, `reliability: "low_reference"`, `voiced_fraction`
  (voiced pitch frames / all frames), `voiced_s`, `note_count`, `note_count_raw` (0.4.2, before the merge),
  `notes_per_second` (notes / the summed duration of those
  notes, i.e. per second of note time, so rests and instrumental gaps do not dilute it; null without notes; same
  definition per section, see below), `range` {`low`, `high`, `p5`, `p95`} each
  `{midi, note}` (low/high plain min/max; p5/p95 duration-weighted), `median_midi` (duration-weighted), `intervals`
  {`count`, `repeat` (0 semitones), `step` (1-2), `leap` (>= 3), `up`, `down`, `max_gap_s` 1.0} as fractions of
  the intervals between consecutive notes at most 1 s apart (a longer rest is a phrase break; null when none),
  `scale_degrees` {`tonic`, `scale`, `weight: "duration"`, `bins`}: 12 duration-weighted fractions, bin k = k
  semitones above the detected `key` tonic (the global KeyExtractor key, so a wrong or modulating key shifts
  every bin; the worker always reports a key, so this is null only without notes or with an unrecognized tonic,
  warning `melody_scale_degrees_unavailable`), and `sections`: one row per 0.3.0 section (same `label`, `start`,
  `end`; `[]` when `sections` is `[]`) with `note_count`, `low`, `high`, `median_midi`, `notes_per_second`, `peak`
  {`midi`, `note`, `time` (onset of the highest note, ties earliest)} and `direction`. A note belongs to the section
  holding its onset, and a section's `notes_per_second` divides its notes by their own full durations, so a note
  crossing a section edge counts its whole length (worker 0.4.0 as first committed divided by the voiced time inside
  the section: a 0.4 s note starting 8 ms before an edge gave 125 notes/s and made the server refuse the whole
  rhythm result; review F1). Direction rule: least-squares line of MIDI against note midpoint time; fitted change = slope x
  (last minus first midpoint); `rising` >= +2 semitones, `falling` <= -2, else `flat`; null with fewer than 4
  notes. Sections without notes have nulls. No note at all: `note_count` 0, every note statistic null, warning
  `no_melody`. A melody error never fails the worker: `{"status":"failed","code"}` (`analysis_error`,
  `dependency_missing`, `non_finite`), no notes, warning `melody_failed: ...`. A stored `analysis_error` or
  `dependency_missing` melody is recomputed on the next `rhythm=1` analyze even with the current worker (like a
  recoverable `tempo_cnn` outcome; a failed rerun keeps the stored file); `non_finite` stays cached. The summary is about 1 KB plus
  ~200 B per section (at most 40).
- `melody_notes` (stored file, `/timeline layer=melody` only): `{start, end, midi, note}` sorted by onset, at most
  6000 (`melody_notes_capped` warning and the first 6000 kept beyond that). 6000 = 600 s / the 0.1 s minimum note
  length, so valid output cannot exceed it; the cap is a guard that also bounds the stored file (~330 KB).
- Server checks: `notes_per_second` (song and every section) and section note counts are recomputed from the
  stored rows when they are not capped (tolerance 0.002; bound 1000 = one note per stored millisecond); exact key
  sets everywhere, MIDI integers 0-127 with matching names, fractions 0-1 summing to 1
  (+-0.01 for rounding), range order low <= p5 <= median <= p95 <= high, note rows sorted and inside the duration,
  stored rows = min(`note_count`, 6000), section rows aligned to `sections` and summing to `note_count`, peak =
  section high inside the section, direction null exactly when a section has under 4 notes, `scale_degrees` key =
  `key`. `melody` and `melody_notes` come together or not at all (files before 0.4.0 have neither and stay valid).
- Cache: a stored file is reused only when its `worker_sha256` equals the running worker's hash; the 0.4.0 worker
  file differs, so every older file is recomputed on the next `rhythm=1` analyze after a restart (existing rule, no
  new code).
- Synthetic results (worker 0.4.0): `melody` 30 s (16-note line A4-A5 in C major, 0.5 s notes): all 60 notes at
  the right MIDI, onsets within 50 ms, voiced 0.952, 2.16 notes per second of note time (2.10 per voiced second
  before the F1 fix), range A4-A5, intervals step
  0.814 / leap 0.186 / repeat 0 (3 leaps per 16-note cycle), up 0.61, key C major and no scale-degree weight
  outside C major, direction flat. `melody_ab` 60 s (abab form; A melody C4-G4, B melody E5-B5): sections A B A B
  as in 0.3.0, A low 60 / high 67 / median 64 / peak G4, B 76 / 83 / 80 / B5, 2.11-2.40 notes per second of note time,
  all flat, 125 notes for 120 written with 0.4.0/0.4.1 (short glide notes at block edges; 118 with 0.4.2). Silence (5 s) and a 1.5 s click: 0 notes,
  `no_melody`. Repeated notes of the same pitch can merge or split (a 3x repeat came out as 2 or 3 notes).
- Runtime (600 s, same session, 2 runs each): melody track 9.34-9.36 s and 1.13-1.14 GB peak RSS vs worker 0.3.1
  7.51-7.53 s and 1.10-1.11 GB; C-Am-F-G loop 9.10 s and 1.14 GB vs 7.68 s and 1.09-1.10 GB. So the melody layer
  adds about +1.6-1.8 s and +40 MB, far inside the 300 s worker budget.
- Honest limits: not a transcription; octave errors (a harmonic or subharmonic taken as the pitch) and
  accompaniment tracking are expected on real mixes; slides and vibrato faster or wider than the synthetic checks
  can still become several short notes (0.4.2 smooths and merges the common cases), and real trills may merge;
  no word-to-note alignment; scale degrees inherit the global key estimate. **Untested on real songs.**

Worker 0.4.1 runtime (600 s, 2 runs each, same session): melody track 9.24 s and 1.13-1.14 GB peak RSS vs 0.4.0
9.33-9.35 s and 1.14 GB; C-Am-F-G loop 8.98-9.02 s vs 9.11-9.13 s (the second TempoCNN call is gone). Stored file
+54 B (`silence_db`, `silence_s`, `silent_patches`).

### Vocal melody (worker 0.6.0, optional)

Melody notes can come from a separated vocal instead of the mix. Off unless the `rhythm` config has `separation`:

```json
"rhythm": {"python": "...", "worker": "...",
           "separation": {"demucs": "/Users/YOU/path/to/venv/bin/demucs-mlx", "rmvpe": "/Users/YOU/path/to/rmvpe.pt"}}
```

Copy `rmvpe_model.py` next to `rhythm_worker.py`. Nothing is downloaded: install demucs-mlx in its own environment and
place `htdemucs_ft.safetensors` under `~/.cache/demucs-mlx/` yourself. Both weight files are pinned by SHA-256 in the
worker. The rhythm python needs torch, scipy and librosa.

What happens on a rhythm request: the upload is decoded to 44.1 kHz mono; audio with under -40 dB of its energy above
8.2 kHz keeps the mix method, because Demucs needs the full band. Otherwise Demucs (`htdemucs_ft`, batch 1, seed 0)
gives a vocal, RMVPE reads it on CPU, and the notes go through the same segmentation and merge as before. Only melody
changes; every other layer still reads the 16 kHz mix.

`melody.separation` says what was used: `{"status": "done"}`, or `{"status": "skipped", "reason": ...}` with the mix
method. `melody.method` names the method. Reasons: `not_configured`, `canonical_16k` (a 16 kHz WAV upload has no band
above 8 kHz), `band_limited`, and the failures `models_invalid`, `decode_failed`, `separation_failed`, `rmvpe_failed`,
which also add the warning `melody_separation_failed` and are retried on the next request. A stored melody from the mix
is recomputed when a later upload of the same audio can be separated; a stored vocal melody stays for a later 16 kHz
upload. Send M4A, MP3 or a full-band WAV to get the vocal method.

Results, all on mixes built for the test (voice and accompaniment at equal level), since real songs have no pitch truth:

| Set | Method | Pitch accuracy (within 50 cents) | False voicing in rests |
|---|---|---|---|
| 4 synthetic-voice songs | mix | 79.6 | 47.5 |
| 4 synthetic-voice songs | vocal | 84.5 | 27.9 |
| 40 human-voice clips (vocadito) | mix | 54.2 | 45.8 |
| 40 human-voice clips (vocadito) | vocal | 95.8 | 37.1 |

Limits: one of the four songs got worse (77.5 to 73.5), the one where separation was poorest. Backing vocals pass
through Demucs as vocals, and that case is not in these mixes.

Cost for a 600 s file on an M4 Max: Demucs 6.8 s and 8.6 GB, RMVPE 7.6 s and 2.6 GB, run one after the other. The
default Demucs batch would take 34.1 GB.

Licenses:

| Part | Terms | Notes |
|---|---|---|
| demucs-mlx, Demucs code | MIT | Installed separately |
| `htdemucs_ft` weights | Not MIT. The Demucs maintainer states they are provided for research purposes (facebookresearch/demucs issues #267, #327, #508) | Not in this repository. Whoever installs them accepts those terms |
| `rmvpe_model.py` | MIT (RVC-Project). RMVPE's original code is Apache-2.0 | Notice kept in the file |
| `rmvpe.pt` | The hosting repository (lj1995/VoiceConversionWebUI) is tagged MIT, with nothing about this file or its training data | Not in this repository. Terms are unconfirmed |

### Chords from BTC (worker 0.7.0)

Chords can come from BTC instead of major/minor triads. Off unless the `rhythm` config has `chord_model`:

```json
"rhythm": {"python": "...", "worker": "...", "chord_model": "/Users/YOU/path/to/btc_model_large_voca.pt"}
```

Copy `btc_model.py` next to `rhythm_worker.py`. Nothing is downloaded: take `test/btc_model_large_voca.pt` from
[jayg996/BTC-ISMIR19](https://github.com/jayg996/BTC-ISMIR19). The file is pinned by SHA-256 in the worker and loads with
`weights_only`. The rhythm python needs torch and librosa.

What changes: `chords` rows use names like `Am7`, `C#maj7`, `Gsus4`, `Bm7b5`, plus `N` (no chord) and `X` (unknown).
These `label` names are sharps only because BTC has no flats; `display` gives the spelling for the key (see "Chord display"). `strength` is null. Rows keep following the beat vote, and a row with
no beat inside it has `one_beat: true`: treat these as less certain. Without beats the rows are frame-level and unmarked. `chords_reliability` is `reference_candidate` and
`chord_vocabulary` names the vocabulary.

If BTC cannot run, chords stay major/minor triads with the warning `chord_model_unavailable: models_invalid|btc_failed`.
With `chord_model` set, a stored result without BTC chords is recomputed on the next analyze, so a failed run is retried.
`/report` and `/timeline` keep the stored chords until then.

| Part | Terms | Notes |
|---|---|---|
| `btc_model.py` | MIT (jayg996/BTC-ISMIR19) | Notice kept in the file |
| `btc_model_large_voca.pt` | Same repository, MIT, with nothing separate about the weights. Trained on chord annotations with commercial recordings | Not in this repository |

### Bass notes (worker 0.8.0)

A bass layer from the separated bass stem. Off unless the `rhythm` config has `bass`, which needs `separation`:

```json
"rhythm": {"python": "...", "worker": "...", "separation": {"demucs": "...", "rmvpe": "..."},
           "bass": {"basic_pitch_python": "/Users/YOU/path/to/venv/bin/python"}}
```

Copy `bass_onsets.py` next to `rhythm_worker.py`. Install `basic-pitch` with the ONNX backend in its own environment.
`bass_onsets.py` and `nmp.onnx` are pinned by SHA-256.

What happens on a rhythm request: Demucs gives the bass stem from the same 44.1 kHz mono audio as the vocal, RMVPE reads the
pitch, and same-pitch notes split at Basic Pitch note starts. Notes under 80 ms are dropped and a same-pitch gap up to 200 ms
joins unless a start falls on the first frame after it. A 16 kHz WAV upload or band-limited audio has no bass layer.

`rhythm.bass` is `{status: "done", method, reliability: "low_reference", note_count}` or `{status: "skipped", reason}`. Read
the rows with `/timeline` `layer=bass`: `{start, end, midi, note}`, where `midi` is the sounding pitch. Reasons:
`canonical_16k`, `band_limited`, and the failures `models_invalid`, `decode_failed`, `separation_failed`, `rmvpe_failed`,
`onsets_failed`, which also add the warning `bass_failed` and are retried on the next request that can give bass. A stored
result without bass is recomputed the same way; a 16 kHz upload does not recompute for this. Without bass stored, `rhythm.bass` is
`null` and the layer answers `rhythm_not_found`.

Limits: treat the pitches as candidates. Note ends are weak, so do not read note length from this layer, and occasional wrong
notes remain.

| Part | Terms | Notes |
|---|---|---|
| Basic Pitch code and `nmp.onnx` | Apache-2.0 (spotify/basic-pitch) | Installed separately |

### Accompaniment notes (worker 0.12.0)

Notes of everything but vocals, bass and drums, as one group. It comes with the bass layer: the same `rhythm.bass` config turns it
on and there is nothing else to install. Copy `other_notes.py` next to `rhythm_worker.py`. `other_notes.py` and `nmp.onnx` are
pinned by SHA-256.

What happens on a rhythm request: Demucs gives the other stem from the same 44.1 kHz mono audio as the vocal, and Basic Pitch reads
its notes at onset threshold 0.7 and frame threshold 0.2. A 16 kHz WAV upload or band-limited audio has no such layer.

`rhythm.other` is `{status: "done", method, reliability: "low_reference", note_count}` or `{status: "skipped", reason}`. Read the
rows with `/timeline` `layer=other`: `{start, end, midi, note}`, sorted by start. Instruments are not named. Reasons: `canonical_16k`,
`band_limited`, `too_many_notes` (over 24,000), and the failures `models_invalid`, `decode_failed`, `separation_failed`,
`notes_failed`, which also add the warning `other_failed` and are retried on the next request that can give the layer. A stored
result without the layer, or a `canonical_16k` one, is recomputed the same way; a 16 kHz upload does not recompute for this. Without it stored, `rhythm.other`
is `null` and the layer answers `rhythm_not_found`.

Measured on RWC-P songs 51-100 against the aligned MIDI without drums, vocals and bass; thresholds were chosen on songs 1-50. F1 on
a 50 ms grid, mean per song, with notes matched within 50 ms at the exact pitch:

| Measure | This layer | True chord tones, for comparison |
|---|---|---|
| Exact pitch, frames | 60.9 | - |
| Pitch class, frames | 80.8 | 80.0 |
| Note starts | 31.3 | - |

Cost beyond the stem: a 600 s file took 22 s, 35 CPU-s and about 1 GB, and gave 7,257 notes (about 316 KB).

Limits: use it for register and how busy the part is, and treat single notes as candidates. It is not a score. Pitch classes say no
more than the chord layer does. YourMT3+ (55.4 exact pitch, 35.7 note starts, seven times slower) did not beat it by the 5 points
fixed beforehand. Read `chords` for harmony.

### Part activity (worker 0.13.0)

Which parts sound in each section, for reading how an arrangement builds. It needs no config of its own and no new model: it comes
with the separation config and reads the stems already made for that request. `vocals` needs only separation; `bass` and `drums` are
`null` unless the bass, drum or section layer made those stems.

`rhythm.parts` is `{status: "done", method, reliability: "reference_candidate", window_s: 2.0, threshold_db: -15, sections}` or
`{status: "skipped", reason}`. `sections` has one row per `rhythm.sections` row: `{start, end, label, vocals, bass, drums,
bass_notes_per_s, other_notes_per_s, drum_hits_per_s}`. `vocals`, `bass` and `drums` are the share of 2 s windows, 0 to 1, whose
stem RMS is within 15 dB of the whole mix's RMS. The rates count note starts or hits per second. `null` means that layer gave no
result. Reasons: `canonical_16k`, `band_limited`, the separation failures, and `levels_failed`, which adds the warning `parts_failed`
and is retried on the next request that can give the layer. Before this worker, or without separation, `rhythm.parts` is `null`.

Evaluated locally against the aligned RWC-P MIDI in 2 s windows, with the threshold chosen on songs 1-50 and checked on songs 51-100.
All three parts passed the bar set beforehand: pooled balanced accuracy at least 80, at least 70 on songs where the part comes and
goes, at most 20% false activity on songs without it. On one real song the entries and a break matched by ear.

Limits: a quiet part can read as off, and -15 dB was the top of the range tried. A cello or similar low instrument can read as bass.
The accompaniment is not judged on or off, because it sounds almost all the time; read its rate instead. Instruments are not named.
Section labels are not corrected by this: similar chords and busy vocals did not mean a chorus on the real song.

### Chord display (worker 0.10.0)

Every `/timeline` `layer=chords` row gets `display`, the name to show a person: `Bb7`, `F#m`, `C/E`, `Bbm7/Db`. No config is needed.
`label` is unchanged and stays the value to compare, with the sharps-only BTC names; section `top_chord` and the overview use it.

- Spelling: the root and the slash note follow the key section at the middle of the segment. A key whose relative major is
  F, Bb, Eb, Ab or Db uses flats; C major and A minor use Db, Eb, F#, Ab and Bb; the others use sharps. A wrong key
  gives the wrong spelling of the right pitch.
- Slash: added when one bass pitch class from the bass layer sounds for at least half of the segment and is a chord tone other
  than the root. Without a bass layer, as on a 16 kHz upload, no slash appears.

Limits: slashes are conservative, so many real inversions get none, and some that appear are wrong. A bass note outside
the chord, such as `C/D`, is never written. These were measured with BTC chords only. `N` and `X` are copied as they are.

### Drum hits (worker 0.9.0)

A drum layer from the separated drum stem. Off unless the `rhythm` config has `drum_model`, which needs `separation`:

```json
"rhythm": {"python": "...", "worker": "...", "separation": {"demucs": "...", "rmvpe": "..."},
           "drum_model": "/Users/YOU/path/to/Frame_RNN_adtofAll_0.index"}
```

Copy `adtof_model.py` next to `rhythm_worker.py`. Get `Frame_RNN_adtofAll_0.index` and `.data-00000-of-00001` from `adtof/models/` of
[MZehren/ADTOF](https://github.com/MZehren/ADTOF) and keep them side by side. Both files and `adtof_model.py` are pinned by SHA-256.
It runs in the rhythm python and needs no new environment.

What happens on a rhythm request: Demucs gives the drum stem from the same 44.1 kHz mono audio as the vocal, and the model
gives hit times for five classes. A 16 kHz WAV upload or band-limited audio has no drum layer.

`rhythm.drums` is `{status: "done", method, reliability: "reference_candidate", hit_counts}` or `{status: "skipped", reason}`.
Read the rows with `/timeline` `layer=drums`: `{time, class}`, where `class` is `kick`, `snare`, `tom`, `hihat` or `cymbal`. Reasons:
`canonical_16k`, `band_limited`, and the failures `models_invalid`, `decode_failed`, `separation_failed`, `adtof_failed`, which
also add the warning `drums_failed` and are retried on the next request that can give drums. A stored result without drums is
recomputed the same way; a 16 kHz upload does not recompute for this. Without drums stored, `rhythm.drums` is `null` and the layer
answers `rhythm_not_found`.

Limits: use it as a reference. Kick is the steadiest. Quiet snare hits are often missed. Tom is weak: expect false and missed
toms. Other percussion has no class. Hits run about 10 ms early. The test set was small and heavy on jazz, so other music may
score differently.

| Part | Terms | Notes |
|---|---|---|
| `adtof_model.py` | This repository; the filterbank and peak picking follow madmom (BSD 2-Clause, CPJKU) | Notice kept in the file |
| `Frame_RNN_adtofAll_0` checkpoint | CC BY-NC-SA 4.0 (MZehren/ADTOF): non-commercial use, attribution, share-alike | Not in this repository. Trained on crowdsourced rhythm game charts for commercial music |

### Sections from All-In-One (worker 0.11.0)

Sections can come from [All-In-One](https://github.com/mir-aidj/all-in-one) instead of the beat method in "Energy and sections". Off unless the
`rhythm` config has `sections`, which needs `separation`:

```json
"rhythm": {"python": "...", "worker": "...", "separation": {"demucs": "...", "rmvpe": "..."},
           "sections": {"allin1_python": "/Users/YOU/tools/music-lens/runtime/allin1/bin/python",
                        "allin1_model": "/Users/YOU/path/to/allin1/harmonix-fold0-0vra4ys2.pth"}}
```

It needs its own environment, because its library versions differ from the rhythm python's: Python 3.11, torch 2.5.1,
torchaudio 2.5.1, numpy 2.0.2, madmom at commit `27f032e8947204902c675e5e341a3faf5dc86dae` and All-In-One from
`jdf/all-in-one` at `3646cafab37c8a3002463a015fb7b231d30944dd`. That fork is an unmerged pull request (mir-aidj/all-in-one#39)
that lets the model run on a Mac without the NATTEN package. Get the eight `harmonix-fold*.pth` files from
[taejunkim/allinone](https://huggingface.co/taejunkim/allinone) and keep them side by side; `allin1_model` names the fold 0 file.
Copy `allin1_sections.py` next to `rhythm_worker.py`. The eight files and `allin1_sections.py` are pinned by SHA-256, and the
checkpoints load with torch's restricted unpickler. Nothing is downloaded at run time.

What happens on a rhythm request: Demucs gives all four stems from the 44.1 kHz mono audio, reusing those the vocal, bass, accompaniment and
drum layers made, and the model gives the sections from them.

Cost of the model step for a 600 s file on an M4 Max: 20 s, 150 CPU seconds and 14.5 GB, after the Demucs step, not alongside it.

Each row of `sections` gains `function`: `start`, `end`, `intro`, `outro`, `break`, `bridge`, `inst`, `solo`, `verse` or
`chorus`. `label` is still a letter, and equal functions share one, so every verse is the same letter. Mostly silent rows
stay `silence`. `similarity_to_first` compares a row with the first row of its letter. `sections_method` says which method
ran, `sections_reliability` is `reference_candidate`, and `structure_debug` is `null`.

`rhythm.sections_model` is `{status: "done"}` or `{status: "skipped", reason}`, and `null` when the section model did not run
or the stored file predates it. Reasons: `canonical_16k`, `band_limited`, and the failures `models_invalid`, `decode_failed`,
`separation_failed`, `sections_failed`, `too_many_sections` (over 40 rows, possible on long songs). In all of these the
beat method fills `sections`. The failures also add the warning `sections_model_failed` and, except `too_many_sections`, are
retried on the next request that can give stems. A stored result without `sections_model` is recomputed the same way; a 16 kHz upload does not recompute
for this.

Limits: use it as a reference. The model tells verse from chorus but not verse A from verse B, so it merges parts a person
would keep apart, and it tends to give fewer sections than a person marks. It was trained on Western pop (Harmonix Set).
Function names were not scored; only boundaries and grouping were. The song overview still builds on lyric repeats first
and does not show `function`.

| Part | Terms | Notes |
|---|---|---|
| `allin1_sections.py` | This repository | Calls All-In-One |
| All-In-One code | MIT (mir-aidj/all-in-one, jdf/all-in-one) | Installed separately |
| `harmonix-fold*.pth` checkpoints | Sources disagree: the Hugging Face repository is tagged MIT with an empty model card; the `all-in-one-infer` package lists the same checkpoints as CC BY-NC-SA 4.0 (mir-aidj/all-in-one#41, unanswered when checked). Treat them as non-commercial | Not in this repository. Whoever installs them accepts those terms |
| madmom | Code BSD; its data and model files are CC BY-NC-SA, non-commercial | Installed separately. Used here for audio reading, spectrograms and beat post-processing |

### Lyric repeats (worker 0.4.3, approximate matching 0.4.4)

Purpose: where the words repeat (likely chorus or hook), independent of the audio sections. Evidence: on the team's 8
real songs (worker 0.4.2) repeated-lyric blocks often started or ended 10-60 s away from any section boundary, and one
song had a single section 0-175 s holding two repeats: the boundary detector finds too few boundaries, so lyric
repeats are reported on their own and clients compare the two.

- Input: the ASR words of the same result. The server reads `<root>/results/<result_id>.json` (the `ml` result
  file, read-only: `O_NOFOLLOW`, regular file, <= 16 MiB, strict JSON, `result_id` must match, at most 20000 words,
  each `{text <= 200 chars, start, end}` finite 0..700; probabilities are not passed) and sends the worker
  `{"words": [[text, start, end], ...]}` on stdin (`--words-stdin`), or `{"words": null, "reason": code}` with
  `result_missing`, `result_invalid` (any malformed word, a symlink or non-regular file) or `result_unreadable`
  (other read errors; retried on the next analyze). No temporary file is named: stdin is an unlinked temporary file.
  Computing it in the worker keeps the cache correct by construction: a worker change changes `worker_sha256`, and a
  words change changes `words_sha256` (see the cache rule above).
- Method (`lyric_repeats.method`): each word's text is NFKC-normalized and lowercased, and only letters, digits and
  marks (Unicode L*, N*, M*) are kept, so punctuation, symbols, full-width/half-width and case differences do not
  matter. Words are sorted by start and clamped into the duration. Unit choice: when the median normalized word is
  one character (char-level ASR: Whisper's Japanese and Chinese output, all 8 team songs) the units are characters,
  each timed by an even split of its word span, and an n-gram is **8 characters** (about 1-2 s of singing, longer
  than common particles and short phrases); otherwise (space-delimited words such as English or Korean) the units
  are whole words and an n-gram is **4 words**, because 8 letters are only about 1.5 English words and short
  common pairs recur outside refrains. Every n-gram occurring at least twice with its occurrence starts spread over
  >= **15 s** marks all units of every occurrence covered (a line doubled within 15 s is not a repeat); covered units
  closer than **3 s** join into one block; blocks shorter than **4 s** are dropped; blocks that share any repeated
  n-gram form one group (union-find over every retained block touched by any unit of any occurrence, so an
  occurrence split by a pause longer than 3 s links all its blocks; group ids 0, 1, ... in order of first
  appearance). A group can have one block
  when the repetition lies inside it (a long block holding a line sung twice >= 15 s apart).
- Approximate matching (worker 0.4.4, char units only): katakana is folded to hiragana before matching, and besides
  exact 8-grams, two **10-character windows within 2 edits** (Levenshtein) whose starts are >= 15 s apart count as
  one repeat, trimmed at both ends while the characters at the same offset differ, so a window reaching past a
  chorus edge does not cover the next line. Candidates must share a 4-character seed at the same offset; a seed
  seen more than 64 times is skipped (filler such as "lalala", still caught by the exact 8-grams), and at most
  200,000 window checks run per song (then warning `lyric_repeats_approx_capped` and matching stops; a 12,000-char
  random 4-letter input reaches the cap in about 3 s). Why: Whisper large-v3-turbo (Music Lens local 0.2.2)
  misspells a chorus differently each time, so on 7 team songs scored against reference lyrics from j-lyric
  (repeats = exact 8-grams of the reference text, ASR chars aligned by Levenshtein; scratch check 2026-10-28, numbers
  only), exact 8-grams found 36% of the repeated reference characters at precision 0.99, this rule 65% at 0.97.
  Tuned on those 7 songs only. Word units are unchanged; the unit choice follows the median word length, not the
  language, so English or Korean ASR output that falls into char units also gets approximate matching. Both the
  exact and the approximate 15 s spread are compared in whole microseconds (inclusive, stable under time shifts).
- Output (summary and stored file): `done` `{status, method, unit: "char"|"word", n, blocks: [{start, end, group}],
  group_count, coverage_s, note}`: blocks sorted, non-overlapping, inside the duration, at most 40 (more: the 40
  longest, warning `lyric_repeats_capped`); `coverage_s` = summed block length. `unavailable` `{status, reason}`:
  `no_words` (ASR failed or heard nothing), `too_little_text` (under 50 normalized characters), `no_words_input`
  (worker run without `--words-stdin`), `words_invalid`/`words_too_large` (malformed stdin), or the server's reason
  above. `failed` `{status, code: "analysis_error"}` on an unexpected exception (never fails the worker). Server
  checks: exact key sets per status, block order, non-overlap, >= 4 s (rounding tolerance 2 ms), group ids in order
  of first appearance with `group_count` = number of groups, coverage = summed blocks. No timeline layer (the
  blocks are in the summary).
- Offline check (worker code on the stored words of the 8 songs, read-only; numbers only): one song has 9 words ->
  `too_little_text`; the other 7 gave 2-5 blocks, 1-2 groups, coverage 10.8-107.7 s. Block edges vs the stored
  0.4.2 section boundaries: 44 edges, median distance 10.8 s, 75th percentile 23.1 s, max 63.8 s, 2 within 3 s, 19
  within 10 s; 13 of 22 blocks lie fully inside one section. Runtime about 2 ms for a 216 s song (1215 words).
- Honest limits: repeated ASR text, not verified lyrics: Whisper mishearing a repeat breaks it, hallucinated repeats
  (the same line over an instrumental) create one, and an untranscribed chorus is invisible. Repeated text is not
  always a chorus (a repeated verse line, a refrain inside a verse). Word mode (English, Korean) is untested on real
  songs. Say "estimate" when relaying.

### Genre (worker 0.5.0)

Purpose: what kind of music this is (style and parent genre), for "compose using this song as a reference". A model
estimate, not a label lookup.

- Models: Essentia's Discogs-EffNet embedding `discogs-effnet-bs64-1` (Alonso-Jimenez, Serra & Bogdanov, ISMIR 2022;
  2022-02-17) and the classification head `genre_discogs400-discogs-effnet-1` (2023-05-04: 400 Discogs styles in 15
  parent genres), source https://essentia.upf.edu/models/, expected in
  `~/tools/music-lens/models/discogs/` (directory and hashes are constants in the worker):
  `discogs-effnet-bs64-1.pb` SHA-256 `3ed9af50d5367c0b9c795b294b00e7599e4943244f4cbd376869f3bfc87721b1` (17.5 MB),
  `genre_discogs400-discogs-effnet-1.pb` `3885ba078a35249af94b8e5e4247689afac40deca4401a4bc888daf5a579c01c` (2.0 MB)
  and its json `2d367319d9b782ffa10f69abf0e805b3ac4e10899025e5bdbaceda3919b243e0` (the 400 class names
  `Parent---Style`, e.g. `Pop---J-pop`, `Rock---Heavy Metal`). Training data per the json: Discogs-4M (in-house,
  unreleased; 3.3M tracks used, ROC-AUC 0.954, PR-AUC 0.206 as published). **License: CC BY-NC-ND 4.0
  (noncommercial, no derivatives)**: team use only; check before any other use.
- Pins: the three files are checked like TempoCNN (O_NOFOLLOW, regular file, <= 64 MiB, SHA-256; any failure ->
  `{"status":"unavailable","reason"}` with the TempoCNN reason codes, everything else computed as before). The class
  names come from the pinned json, parsed from exactly the bytes that matched the pin (one source of truth shipped
  with the head, as fixed as a list in the worker, without 10 KB of literals); a json that does not hold 400
  `Parent---Style` names, or a head output that is not 400 columns, gives `failed` `class_count_mismatch`.
  `--genre-models /abs/dir` (tests and measurement only) selects another directory; the pins are not overridable.
- Method: `TensorflowPredictEffnetDiscogs` (output `PartitionedCall:1`, the 1280-d embedding; batch 64, patch 128 mel
  frames, patch hop 62, last patch discarded, all passed explicitly) on the 16 kHz source itself (the models' rate),
  then `TensorflowPredict2D` (input `serving_default_model_Placeholder`, output `PartitionedCall:0`): one 400-class
  sigmoid row per patch. Measured here (2026-10-31): frames hop 256 = 16 ms, a patch covers 128 frames (frame centres
  2.032 s apart) and the patch hop is 62 frames = **0.992 s**; the first patch exists from ~2.04 s of audio (2.0 s:
  none, 3.04 s: 2), 29 patches for 30 s, 59 for 60 s, 603 for 600 s. Near-silent patches (more than half of the 1 s
  energy windows in the patch's frame-centre span below -60 dBFS, the tempo_cnn rule) are excluded and counted in
  `silent_patches`. Song score per style = mean of the audible patch rows; `top` = 5 styles; `parents` = 5 parent
  genres, parent score = the max over its styles (so `parents[0].score == top[0].score`); ties: lower class index,
  parent name. `windows`: fixed 30 s windows (a last window under 15 s joins the previous, the key_sections rule; at
  most 20 for 600 s), each patch in the window holding its centre (i x 0.992 + 1.016 s), with the window's `patches`
  and top 3 styles (`[]` when every patch in it was silent). 30 s matches the clip length the accuracy below was
  measured on; shorter windows hold fewer patches of a release-level label.
- Output `done`: `{status, model, model_sha256, embedding_model, embedding_sha256, reliability: "model_estimate",
  patches, silent_patches, patch_hop_s: 0.992, window_s: 30, top: [{style, score}], parents: [{parent, score}],
  windows: [{start, end, patches, top}], note}` (scores 3 decimals). The summary (`/report`, analyze) carries it
  without `windows`, plus `window_count`; windows come from `/timeline layer=genre`. `unavailable` `{status,
  reason}`: the pin reasons above, `too_short` (no patch, audio under ~2.04 s), `no_audible_patches` (all silent).
  `failed` `{status, code}`: `class_count_mismatch`, `non_finite`, `inference_error`, `dependency_missing`; never
  fails the worker. Server checks: exact key sets per status, scores 0..1 non-increasing, distinct names, styles
  `Parent---Style`, best parent = best style score and no listed style above its listed parent, windows contiguous
  0..duration (<= 21), top empty exactly when `patches` is 0, window patches summing to `patches - silent_patches`.
- Scores are **mean sigmoid activations, multi-label, not probabilities**: they do not sum to 1, several styles can be
  high at once, and the absolute level differs between styles (a top score of 0.15 can still be a clear first
  place). Compare the ranking, not the number.
- Accuracy (measured by Claude-Mac 2026-10-31, not re-derived here; first 30 s of each clip at 16 kHz; the 400 styles
  mapped to each dataset's labels by a fixed rule, max over a style set; wiki note
  `Research/music-genre-zero-shot-gemma-vs-discogs.md`):

  | Test set | Classes (chance top 1) | Discogs top 1 / top 3 | EmbeddingGemma 2 zero-shot top 1 / top 3 |
  | --- | --- | --- | --- |
  | GTZAN, 20 clips x 10 genres | 10 (0.10) | 0.745 / 0.965 | 0.495 / 0.830 |
  | FMA-small validation shard 0, 399 clips | 6 (0.17) | 0.576 / 0.823 | 0.463 / 0.733 |

  EmbeddingGemma zero-shot was worse on both and changed with the prompt wording, so it is not used. Caveats: GTZAN
  tracks may overlap the Discogs training data (the GTZAN number may be optimistic); FMA's "Pop" class scored about
  0.04 (a noisy label); Discogs labels are release-level, so a
  per-window genre is less reliable than the whole-song one. Not measured on the team's own songs (J-pop and similar).
  Check here (worker code on 21 of those clips, read-only): the worker's top style equals the reference script's on
  21 of 21, top score within 0.0005 (the reference loaded audio with MonoLoader; the worker reads the s16 WAV).
- Runtime (synthetic C-Am-F-G loop, full worker, 2 runs each, same session, 2026-10-31): 30 s 1.41-1.45 s and
  1.32-1.34 GB peak RSS vs 1.07-1.08 s and 0.33-0.35 GB with genre unavailable; 600 s 10.82-10.94 s and 1.54-1.69 GB
  vs 9.84-9.87 s and 1.13 GB. So genre adds about +0.4 s (30 s) to +1.0 s (600 s) and up to ~1 GB peak memory even on
  short audio (the bs64 graph always runs a batch of 64 patches). The genre call alone on 600 s: 603 patches, embedding
  1.2 s, head 0.02 s.
- Honest limits: a style is a Discogs release tag, not the song's own genre; mixed or uncommon styles share score
  across several tags; per-window rows can flip between related styles; Korean/Japanese pop is covered by `Pop---K-pop`
  and `Pop---J-pop` but accuracy on them is unmeasured. Say "estimate" when relaying.

### Overview (server, read time)

Purpose: one ordered list of song parts for agents, built from data the stored file already holds. Computed by the
server each time a summary is produced (`/report` and both analyze paths, fresh run and cache hit; the summary is the
same everywhere), never stored, never a worker run. About 1-2 ms for a 3-5 min song. `null` when the stored file
predates worker 0.4.3 (no `lyric_repeats`); no rhythm file or a failed rhythm: no summary at all, as before.

Why lyric repeats are the backbone: on the 8 real songs (stored 0.4.3 `structure_debug`) the audio boundaries match
`lyric_repeats` block edges (+-5 s) only at the chance level implied by their density, at every novelty threshold from
0.04 to 0.20 (0.15: 26% vs ~25% chance; 0.06: 62% vs ~70%); novelty near lyric edges is mostly 0.02-0.10. So audio
`sections` only split long gaps here, or stand in when there are no repeats.

- `source: "lyric"` when `lyric_repeats` is `done` with at least one block (blocks clamped into the duration):
  1. every block becomes a segment `kind: "repeat"` with its `group`;
  2. each gap before, between and after the blocks is cut at the edges of runs of >= 4 consecutive 1 s energy
     windows below `silence_db` (-60 dBFS) and, when words are available, of runs of >= 8 windows touched by no ASR
     word (about 4 bars at 120 BPM: separates an instrumental intro or interlude from a verse); a piece longer than
     40 s is split at the audio section boundary (`sections[1:].start`, at least 4 s inside) nearest its middle (ties
     earliest), repeated while longer than 40 s and a boundary exists;
  3. each piece gets a kind: `silence` when more than half of its 1 s windows (by midpoint) are below `silence_db`,
     else `vocal` when >= 25% of its windows are touched by an ASR word (zero-length words count; a word touches the
     windows its span overlaps), else `instrumental`. Without verified words (below) the same 25% rule uses the melody
     notes (`method` says "melody notes"; weak: the predominant pitch also follows instruments), and without melody
     too the kind is `gap`;
  4. (lengths, gap limits and chord times are compared in integer milliseconds, the stored precision, so an exact
     4 s gap is not tiny and equal chord durations tie exactly) a non-repeat piece under 4 s joins its longer non-repeat neighbour (ties: the earlier), else (only repeat
     neighbours) the previous segment (the next one at the song start), shortest first, ties earliest; the receiver
     keeps its kind and group, so a repeat can absorb a gap under 4 s (repeat edges then differ from the block
     edges). While more than 40 segments remain, the shortest non-repeat segment joins by the same rule. Adjacent
     non-repeat pieces of the same kind then join while the result stays <= 40 s;
  5. a repeat with more than half of its 1 s windows below `silence_db` (ASR text over a silent tail or fade, not a
     chorus candidate) becomes `silence` with `group: null`, is counted in `dropped_repeats` instead of
     `repeat_groups`, and joins an adjacent silence piece (<= 40 s);
  6. (heuristic, overview only; `lyric_repeats` is unchanged) a repeat segment longer than 45 s is cut at its best
     internal point, both parts >= 8 s, keeping kind `repeat`, the group and the block: the middle of the longest
     run of whole 1 s windows inside it touched by no ASR word (ties earliest), else the middle of the largest gap
     in the union of all word spans reaching into it (measured from the furthest end of every earlier word, so a
     short word inside a longer one makes no gap and no cut falls inside a word; ties earliest), else the audio
     section boundary nearest its middle (this last rule does not check for words); repeated while longer
     than 45 s and fewer than 40 segments exist; no candidate meeting the 8 s rule (word rules need words): no cut.
     On the 8 songs: 111-171 -> 111-138 + 138-171 and 65-116 -> 65-80 + 80-116 (both at a word-free second).
- `internal` (every segment; false unless a repeat): `internal: true` means this group has only one surviving block
  after silence filtering (step 5); splitting that block (step 6) does not add occurrences. It is weaker
  cross-section repeat evidence, not proof that the text repeats only within that block (e.g. the group's other
  block may have been dropped as silence).
  Chosen as a flag rather than a new kind so every lyric-repeat segment keeps `kind: "repeat"` (clients filtering
  by kind still see it) while `repeat_groups` lists only groups with >= 2 blocks: `count` = blocks (a cut block
  still counts once), `seconds` = their segments' total. Internal groups are not listed.
- `source: "audio"` when lyric repeats are unavailable, failed or empty: the stored `sections`, kind `section` (with
  its `label` letter) or `silence` (label `silence`), made contiguous. `structure` tells how much form there is:
  `lyric` (lyric source), `audio_labels` (audio source with >= 2 different section letters) or `unknown` (audio
  source whose audible sections all share one letter, e.g. all `A`: the segments are only novelty cuts, so do not
  infer form, repeats or a chorus from them; labels are kept as stored). No repeats and no sections (no beats):
  `{status: "failed", code: "no_structure"}`.
- Words: the ASR words of `<root>/results/<result_id>.json` read like the lyric input (same checks), used only when
  their payload hash equals the stored `words_sha256` (the words the repeats came from); otherwise (result file
  gone, changed by an ASR re-run, malformed) `words` is null on every segment. Only counts are returned, never text.
- Output: `{status: "done", source, structure, method, reliability: "low_reference", segment_count, repeat_groups:
  [{group, count, seconds}], dropped_repeats, segments, notes}`; at most 40 segments, contiguous 0..duration (times 3 decimals). Each segment
  `{start, end, kind, group (repeat only, else null), internal, label (audio only, else null), energy: {mean_db, rel_db,
  rank}, quiet, chords: {top, changes_per_min}, melody, words}`:
  - `energy.mean_db`: mean of the 1 s values whose midpoint lies in the segment, leaving out values below
    `silence_db` unless the segment is silence or holds only such values; `rel_db` = mean_db minus the song median of
    the non-silent values; `rank` 1 = loudest among non-silence segments (ties earliest), null for silence;
  - `quiet`: true only for a repeat with `rel_db <= -12` (heuristic, not calibrated: far below the -2.4..+1.2 dB
    of the plausible chorus repeats on the 8 songs, e.g. a repeat over a fade), else false;
  - `chords.top`: up to 3 `{label, share}` by time inside the segment (share of the segment length; durations in integer ms, ties by first
    appearance); `changes_per_min`: chord label changes strictly inside the segment per minute;
  - `melody` (null on every segment when the melody is missing or failed): `{note_count, low, high, median_midi,
    rel_semitones}` over notes whose onset lies in the segment, median duration-weighted like the worker,
    `rel_semitones` vs the song `melody.median_midi`; MIDI ints, null without notes;
  - `words`: `{count}` of word onsets in the segment, or null (above).
- Failure containment: any internal error gives `overview: {status: "failed", code: "internal_error"}`, a result
  failing the server's own strict shape check `{..., code: "invalid_overview"}`; the rest of the summary and the
  report are unchanged.
- Offline check (8 stored songs, read-only): one result predates 0.4.3 -> null; one has `too_little_text` -> audio
  source, 7 sections all `A` (`structure: "unknown"`); the other 7 gave 6-14 segments with 2-4 repeat blocks
  each (1-2 groups; two songs have a one-block group, flagged `internal`); repeat `rel_db`
  -2.4..+1.2 dB except two hallucination-like repeats over a fading/silent tail of one song: the -107 dB one (mostly
  silent) is dropped to `silence` (`dropped_repeats: 1`), the -23 dB one stays a repeat with `quiet: true`; repeat
  `rel_semitones` -4..+12.
- Honest limits: `repeat` = repeated ASR text, a chorus/hook candidate, not a verified chorus; verse and pre-chorus
  are not distinguished (both `vocal`); everything depends on the ASR (missed lines turn into `instrumental`,
  hallucinated text over an instrumental into `vocal` or `repeat`); split points sit on 1 s windows or section
  boundaries or word gaps; thresholds (4 s, 8 s, 25%, 40 s, 45 s) are set by reasoning, checked on 8 songs only. Say "estimate".

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
12. Optional rhythm layer (not in v0.6): `rhythm=1` on `/analyze`, a `rhythm` summary in `/report`, `layer=beats|chords|energy|melody|genre|bass|other|drums`
   on `/timeline`, and a `rhythm` object in `/status` (see "Optional rhythm layer"). New rhythm work and the `/status`
   field need the `rhythm` config key; `/report` and `/timeline` read stored files whenever they exist. The receipt
   records the ASR outcome only; the rhythm result lives in `results-rhythm/`. The worker runs while the API gate is
   held (another API analyze meanwhile gets 409 `busy`), and it takes the team flock after
   `ml analyze` released it, so a Mac-side analyze can win the race (rhythm `busy`). A stop during the rhythm step
   does not mark the receipt `unknown` (it is already `done`), but the worker may still be running.
