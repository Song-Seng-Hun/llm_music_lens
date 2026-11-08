# Music Lens Mac server: contributor notes

Files and tests of `lan/`. People using the server need only the [README](../README.md).

## Files

| File | Purpose |
| --- | --- |
| `server.py` | The server: config, en0/en1 address discovery, TLS 1.2+, strict framing, HMAC auth, durable nonce and receipt logs, pipeline gate, staging, afconvert, strict WAV check, hard-link promotion, `ml` calls by argv, error mapping, cleanup, logs |
| `rhythm_worker.py` | Optional rhythm layer worker (Essentia DSP plus pinned local models: TempoCNN, Discogs-EffNet + genre head): BPM (+ half/double, + learned TempoCNN estimate), beats, key (+ 20 s sections), beat-grid chords, 1 s energy curve, repeated-section structure (A/B/...), melody feature summary (predominant pitch notes), genre/style estimate (song + 30 s windows). Runs under the Essentia python, never in the listener |
| `rmvpe_model.py` | Optional vocal melody: RMVPE inference on CPU, run by `rhythm_worker.py` as its own process (reduced from RVC-Project, MIT) |
| `btc_model.py` | Optional chords: BTC inference on CPU, run by `rhythm_worker.py` as its own process (reduced from BTC-ISMIR19, MIT) |
| `bass_onsets.py` | Optional bass layer: Basic Pitch notes `[start, end, midi]` (starts to split notes, pitches to confirm them), run by `rhythm_worker.py` in its own environment |
| `other_notes.py` | Optional accompaniment layer: Basic Pitch notes of the other stem, run by `rhythm_worker.py` in its own environment |
| `adtof_model.py` | Optional drum layer: ADTOF Frame_RNN inference on CPU, run by `rhythm_worker.py` as its own process (separate implementation, reads the official checkpoint) |
| `limit_exec.py` | Sets RLIMIT_CPU 60 s / FSIZE 24 MiB / NOFILE 32, then `execv /usr/bin/afconvert -f WAVE -d LEI16@16000 -c 1 src dst`. Two more fixed modes for the ear check: `--listen SRC DST` (`-d LEI16@44100 -c 1`, FSIZE 56 MiB) and `--earcheck PYTHON SCRIPT RESULT SRC START END OUT LAYERS` (FSIZE 16 MiB, NOFILE 64, `execv PYTHON -I SCRIPT ...`), and `--roll PYTHON SCRIPT RESULT SRC PAGE OUT`, `--roll-marks ... PAGE OUT MARKS` and `--roll-poster ... START END OUT TITLE CREDIT` for the sheet PNG (FSIZE 8 MiB, NOFILE 64), and for the sheet HTML `--aac SRC DST` (`-f m4af -d aac@44100 -b 96000 -c 2`, FSIZE 12 MiB) and `--view` / `--view-marks PYTHON SCRIPT RESULT SRC AAC OUT` (CPU 150 s, FSIZE 25 MiB, NOFILE 64) |
| `local_cli.py` | Mac-local CLI (no network): analyze + rhythm layer, report, timeline and sheet through the server's own code (`server.Engine`); see "Local CLI (this Mac)" |
| `sign.py` | Shared canonical-string and HMAC helper (server, tests, Python clients) |
| `sheet.py` | LLM sheet: compact section-indexed text/JSON of a stored rhythm result, served at `/sheet/<id>`, with a bar-by-bar zoom (stdlib only) |
| `view.py`, `view.html` | Sheet HTML: the whole song as one offline page with its audio (run under `rhythm.roll_python`, which also needs Pillow; never imported by the server); see "Sheet HTML" |
| `roll.py` | Sheet PNG page: spectrogram on a semitone axis over a piano roll of a stored result (run under `rhythm.roll_python` with numpy and matplotlib; never imported by the server); see "Sheet PNG" |
| `ear_check.py` | Ear check you listen to: left ear the original, right ear a synth replay of a stored result, loudness following the original (local tool, and run by the server for `POST /earcheck/<id>` under the rhythm python; needs numpy) |
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
WAV check, and `NODE` (or `node` on PATH) runs the signer vector test. `MUSIC_LENS_ROLL_PYTHON` (a python with numpy
and matplotlib) runs the real sheet PNG renderer in `tests/test_roll.py`; without it that test is skipped and the
image itself is unverified (the route and CLI tests use a fake renderer).

Tests bind 127.0.0.1 only through the test-only `"test_mode": true` config flag. A production config (no
`test_mode`) refuses loopback, `0.0.0.0`, literal bind IPs, port 0, `limits` overrides and `test_ready_file`.

Local CLI tests: `tests/test_local_cli.py` (fake `ml` and fake worker; the stop-signal path; checks the CLI output byte for byte against the served `/report`, `/timeline` and `/sheet` bodies).
