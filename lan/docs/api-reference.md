# Music Lens Mac server: API reference

Routes, parameters, fields and cache rules of the Mac analysis server. For what the server is and how to start, see the
[README](../README.md); for installation, [setup](setup.md); for how each analysis layer works, [analysis layers](evaluation.md);
for logs, stopping and recovery, [operations](operations.md). Quoted section names such as "Genre" or "Musical units"
that are not on this page are in [analysis layers](evaluation.md).

If an analysis response is lost, check its receipt (`GET /receipt/<operation id>`). Never resubmit the analysis automatically.

## Ear check

Hear whether a stored rhythm result matches the song: the left ear plays the original, the right ear a synth built
only from the result's rows (melody, bass, accompaniment, chord pad, drums; clicks at bar starts on request). The right
ear's loudness follows the original over 0.4 s windows, so both sides sit at the same level. Notes play at their
detected pitch. Run it with a python that has numpy (for example the Essentia or emotion runtime):

```sh
python -I ear_check.py RESULT.json SOURCE.wav 60 85 check.wav                  # default layers, at most 60 s
python -I ear_check.py RESULT.json SOURCE.wav 30 50 check.wav bass,drums,clicks  # chosen layers
afconvert -f m4af -d aac -b 192000 check.wav check.m4a                         # optional, smaller file
```

`RESULT.json` is the stored worker file (`<root>/results-rhythm/<result_id>.json`); an API response has no note
rows and would leave the right ear silent. `SOURCE.wav` is any PCM 16/24/32-bit or float WAV of the same audio. Limits: the accompaniment layer has many notes
and plays quietly; a note an octave above another note sounding at the same time may be a real note or an overtone,
and the result cannot tell them apart. A filter that dropped such notes was tried and rejected: on a song with real
octave jumps it cut the played high octave. Tests: `tests/test_ear_check.py` (skipped without numpy). Before deploying the LAN
route, run it once with the configured rhythm python (`<rhythm python> -I -m unittest discover -s tests -p
test_ear_check.py`): its `ServedTest` runs `ear_check.py` under `limit_exec.py --earcheck` (CPU 60 s, FSIZE 16 MiB,
NOFILE 64) and fails if numpy does not start under those limits.

A LAN client can get the same file from the server: `POST /earcheck/<result_id>?start=&end=` with the audio
re-uploaded (see "Optional rhythm layer"); spans there are at most 30 s.

## Sheet PNG (spectrogram + piano roll per section)

One image per sheet page, to check a stored result's notes against the audio's real energy (a drum boom read as a
long bass note shows as a note outline with no matching line in the spectrogram) and to show a result to people.
Each page is one source section (S01, S02, ... as in the sheet); a section longer than 22 s (1.1 x 20 s) is split
into k pages and labelled `A 1/3`, `A 2/3`, ... (each cut is the beat nearest the equal cut among the beats after the
previous cut and before the next equal cut, else the equal cut, so pages always move forward); a result with no section layer
gives one page `all` over the whole song. Layout, top to bottom:

- spectrogram of the audio on a semitone axis (1/3-semitone rows centred on each semitone, MIDI 24-96, 70 dB below
  the page's own peak, so brightness is not comparable between pages), with each melody, bass and other note drawn
  as an outline over it. The STFT is multi-resolution with a 256-sample hop: below MIDI 45 a 0.74 s window, below
  57 a 0.37 s window, above that 0.19 s (32768 / 16384 / 8192 samples at 44.1 kHz). Half a Hann main lobe (2 sr/N
  Hz) is under one semitone from about MIDI 29 in the lowest band and throughout the other two, so two notes a
  semitone apart show as two lines there (one 8192-point STFT merged them below about MIDI 54); below MIDI 29
  neighbouring semitones still merge. All bands share frame centres and are scaled by their window sum, so levels match across bands;
  the price is time detail in the low rows (onsets there smear by about 0.3 s);
- piano roll coloured by part (melody, bass, other), chord labels, and bar lines: when the result's `bars` layer is
  done, darker lines at the bars' starts (the downbeats) and lighter lines at the other beats; otherwise every 4th
  beat counted from the song's first beat is darker and the page says so in its bottom-left corner ("no downbeats
  in this result"), also on pages past the last stored bar (older results). The poster draws the same lines;
- drum hits by class (kick, snare, hihat, tom, cymbal), hidden when the result has no drum hits.

The server keeps no audio: the analysed WAV and the upload are removed when `POST /analyze` ends, so the audio is
sent again for each image, as for the ear check. It must be the analysed audio: its duration must be within 0.5 s of
the result's, otherwise `400 invalid_argument` ("not this result's audio"). Any upload kind works (it is converted to
44.1 kHz mono first).

- `GET /sheet/<id>?format=png` returns the page index, no audio needed:
  `{"result_id", "format": "png", "available": true|false, "pages": [{"page": 1, "section": "S01", "label": "A",
  "start": 0.0, "end": 19.7}, ...], "render": "POST /sheet/<result_id>?format=png&page=N ..."}`. `available` is
  false when the server has no `rhythm.roll_python`. Any other parameter with `format=png` is `400 bad_request`.
- `POST /sheet/<id>?format=png&page=N` with the audio as the body returns that page as `image/png` (about 0.5 MB,
  about 1270 x 700 px). As `/earcheck`: synchronous, nothing stored, the API gate and the team flock held (`409 busy`
  beside an analysis), uploads removed before the reply, the renderer under `limit_exec.py --roll` (CPU 60 s,
  FSIZE 8 MiB) with `roll_timeout` 60 s. Errors are JSON, never a partial image: `404 rhythm_not_found`,
  `400 invalid_argument` (page past the last page, or another file's audio), `400 invalid_audio`,
  `503 roll_unavailable` (no `roll_python`), `500 roll_failed` (renderer exit, or output that is not a PNG),
  `504 timeout`. A POST to `/sheet/<id>` without `format=png` stays `405 method_not_allowed`; the text and JSON
  sheets are unchanged.
- CLI: `local_cli.py sheet ID --png DIR --audio FILE [--page N]` renders every page (or page N) into the existing
  directory DIR as `<first 12 hex of ID>-pNN.png` (mode 0644, never overwriting: an existing file is
  `invalid_path`) and prints `{"result_id", "format": "png", "pages": [{"page", "section", "label", "start", "end",
  "path"}, ...]}`. Example:

  ```sh
  /usr/bin/python3 -I local_cli.py sheet <result_id> --png ~/Pictures/roll --audio song.wav
  ```

Config: the renderer needs numpy and matplotlib, which the rhythm worker's python does not have. Name a python that
has them as the optional `rhythm.roll_python` (validated like the other rhythm paths: a regular, executable file,
not group or world writable), for example the All-In-One runtime:

```json
"rhythm": {"python": "...", "worker": "...", "roll_python": "/Users/YOU/tools/music-lens/runtime/allin1/bin/python3"}
```

Without it the index still answers (`available: false`) and rendering is `503 roll_unavailable` (also when the
config has no `rhythm` block at all). `roll.py` also runs by hand (16-bit PCM WAV, any rate and channels):
`ROLL_PYTHON -I roll.py RESULT.json AUDIO.wav PAGE OUT.png [MARKS.json]` or
`ROLL_PYTHON -I roll.py --poster RESULT.json AUDIO.wav START END OUT.png TITLE CREDIT`.

### Marks (opt-in): suspect notes and missed energy

`POST /sheet/<id>?format=png&page=N&marks=1` (CLI: add `--marks`) draws the same page with checks of the result's
notes against the audio. The spectrogram shows the harmonic part only (a median-filter harmonic/percussive split:
about 0.2 s along time, 25 rows along pitch, soft mask), and the drum lane shows the percussive energy behind the
drum hits. Without `marks` the page is byte-identical to the plain one.

- Suspect notes (red dashed outline, faded in the roll), flags per note:
  `weak` (the note's row is less than 3 dB above the rows 2.5 semitones away), `semitone` (the row 1 semitone above
  or below is more than 6 dB stronger over the note, compared only on frames that no other note or harmonic at that
  pitch reaches, give or take half the band's window: the usual pitch-off-by-one error), `drum` (the percussive share within 1 semitone is more than 0.25 above the
  median of the page's notes), `quiet` (more than 20 dB below the median level of the page's notes), `reattackN`
  (N >= 2 separate rises of more than 6 dB within 30 ms in a 2048-point STFT band f0/1.26..2.5 f0, more than 0.1 s
  apart, the note's own attack excluded, and rises where another note with a harmonic in that band starts within
  0.1 s ignored: a held note that is really N strikes).
- Missed energy (green dotted box): harmonic peaks more than 12 dB above the rows 4 away (4/3 semitones), within
  20 dB of the page's 99.5th percentile, not explained by any note +-1 semitone or its harmonics (+0, 12, 19, 24, 28,
  31 semitones), held at least 0.15 s (0.4 s below MIDI 45, where the 0.74 s window cannot place shorter runs); harmonics of a lower missed run are dropped. A note explains its pitches from
  half its band's window before its start (the STFT smear) to half the window plus 0.3 s after its end (ringing
  tails). A run shorter than 0.4 s near a drum hit of the result (within the smear, up to 0.2 s after) is the drum.
- Each page is analysed on its own span plus 1 s on each side, so the levels are relative to that page.

These thresholds are unvalidated rules of thumb: a mark says "look here", and the counts are not an accuracy measure.
They were set by eye on a few clips and then checked against seeded error injection on three synthetic clips and two
real clips (pitch off by a semitone or octave, deleted notes, merged stabs, notes added on kicks or in quiet spots);
that check covers five sources only. On real songs a result that leaves out a part (a melody-only result, say)
shows that part as many missed runs. The tests pin clear synthetic cases only: a sustained tone with drums, nine
stabs written as one note, a note left out, a note a semitone off, a 0.25 s note left out, a tone ringing past its
written end.

The server returns the PNG only. The CLI also writes `<id12>-pNN-marks.json` beside `<id12>-pNN-marks.png` and
prints the lists: `{"result_id", "format": "png", "pages": [{"page", "section", "label", "start", "end", "path",
"marks_path", "suspects": [{"start", "end", "lane", "note", "midi", "flags", "contrast_db",
"semitone_db", "perc_ratio", "level_db", "reattacks"}], "missed": [{"start", "end", "midi", "note"}]}], "marks_note"}`. Example:

```sh
/usr/bin/python3 -I local_cli.py sheet <result_id> --png DIR --audio song.wav --page 1 --marks
```

### Poster (opt-in)

`POST /sheet/<id>?format=png&style=poster&start=S&end=E[&title=T&credit=C]` (CLI: `--style poster --start S --end E
[--title T] [--credit C]`) draws one span of 0.5 to 30 s, inside the result's duration, as a 2400 x 1350 image
for people: glowing note lines (width from the semitone height, opacity from the note's harmonic energy) over a
faint harmonic spectrogram, a keyboard strip, a chord band, kick and snare ticks, the title, the tempo and key
line, and the credit. The legend lists only parts with at least 0.5 s visible; the pitch range reaches down to
strong sustained low energy that has no note (a bass pedal, say). Title and credit: at most 80 printable
characters each (any script; no control characters), empty when left out. The CLI writes
`<id12>-poster-<start>-<end>.png` and prints `{"result_id", "format": "png", "poster": {"start", "end", "title",
"credit", "path"}}`. Example:

```sh
/usr/bin/python3 -I local_cli.py sheet <result_id> --png DIR --audio song.wav --style poster --start 40 --end 60 --title "My take"
```


## Sheet HTML (whole song, with audio)

One offline HTML file per stored result: the whole song as one horizontally scrolling strip, played in the browser.
Top to bottom: section and chord labels, the spectrogram (the PNG's multi-resolution rows, 20 ms columns, as JPEG
tiles) with each note outlined over it, the piano roll by part, drum hits, a time axis; bar lines as on the PNG pages
(downbeats when the result's bar layer covers that part of the song, else every 4th beat, with the note under the
strip). Controls: play/pause (button or space), a playhead that follows the audio (the view scrolls with it while
"follow" is on), click or drag on the strip to seek, speed 0.5 / 0.75 / 1x (pitch kept), zoom 40 / 80 / 160 px/s,
toggles for the spectrogram, each part, drums and marks; hovering a note shows its part, name, start-end and, with
marks, its flags and their numbers. With marks the same checks as the PNG marks run page by page (see "Marks") and
the corner note says "marks: unvalidated heuristics, not accuracy".

- `POST /sheet/<id>?format=html[&marks=1]` with the analysed audio as the body returns `text/html; charset=utf-8`.
  Same rules as the PNG: the audio must be this result's (duration within 0.5 s), at most 600 s, synchronous,
  nothing stored, gate and team flock held, uploads removed before the reply; errors are JSON (`400 bad_request` for
  any other parameter, `400 invalid_argument`, `400 invalid_audio`, `503 roll_unavailable`, `500 roll_failed`,
  `504 timeout`). `GET /sheet/<id>?format=html` is `400 bad_request` (POST only). The text, JSON and PNG sheets are
  unchanged.
- CLI: `local_cli.py sheet ID --html DIR --audio FILE [--marks]` writes `DIR/<first 12 hex of ID>.html` (0644, never
  overwriting: an existing file is `invalid_path`) and prints `{"result_id", "format": "html", "path", "bytes",
  "duration", "marks"}` (plus `marks_note` with `--marks`). Example:

  ```sh
  /usr/bin/python3 -I local_cli.py sheet <result_id> --html ~/Pictures/roll --audio song.m4a --marks
  ```

Audio: the upload is converted to stereo AAC 96 kbps at 44.1 kHz (`limit_exec.py --aac`, afconvert; mono is doubled, 5.1 is downmixed) and
embedded as a `data:audio/mp4` URI; the spectrogram is drawn from the 44.1 kHz mono conversion as for the PNG.
**The file contains the audio**: share it only where the audio itself may go.

Size and time: the page is at most 24 MiB (`view.py` refuses more; the response has its own cap, above
`response_max`); a 600 s song comes to about 12 MiB, mostly audio. The renderer runs under CPU 150 s and
`view_timeout` 180 s: marks cost one analysis per sheet page, so a long song with marks takes far longer than one
without.

Safety: the file loads nothing from anywhere. A Content-Security-Policy meta allows only inline script and style and
`data:` images and media (`default-src 'none'`), there is no external URL, and no tag has a `src` or `href`. Every
text from the result (chord and section labels, note names, marks) sits in one `application/json` block with `<`,
`>`, `&` and U+2028/U+2029 escaped and reaches the page through `textContent` or canvas text only, never `innerHTML`.

The page's JavaScript (playback, seeking, scrolling, toggles, hover) has no unit tests; the tests check the file's
structure, escaping, CSP, size cap and the routes.

## Local CLI (this Mac)

The LAN server refuses loopback in production and holds only the Windows client keys, so programs on the Mac itself
cannot call it. `local_cli.py` gives them the same results without the network. It runs as the same OS user, the
same trust boundary as the `ml` wrapper: no TLS, no signing, no nonces, no receipts.

```sh
/usr/bin/python3 -I local_cli.py analyze /abs/song.m4a --language ko --emotion-mode music   # ml analyze, then the rhythm layer
/usr/bin/python3 -I local_cli.py analyze /abs/song.m4a --language ko --quick                # rhythm layer without stems (seconds, not minutes)
/usr/bin/python3 -I local_cli.py report <result_id>
/usr/bin/python3 -I local_cli.py timeline <result_id> --layer chords --start 30 --end 60 --limit 50
/usr/bin/python3 -I local_cli.py sheet <result_id>                                         # same body as GET /sheet/<id>
/usr/bin/python3 -I local_cli.py sheet <result_id> --start 60 --end 90 --format json       # zoom, at most 30 s
/usr/bin/python3 -I local_cli.py sheet <result_id> --view brief                            # one line per section
/usr/bin/python3 -I local_cli.py sheet <result_id> --layers chords,drums                   # only those section layers
/usr/bin/python3 -I local_cli.py sheet <result_id> --png DIR --audio song.wav            # PNG pages (see "Sheet PNG")
/usr/bin/python3 -I local_cli.py sheet <result_id> --png DIR --audio song.wav --marks    # with suspect/missed marks
/usr/bin/python3 -I local_cli.py sheet <result_id> --html DIR --audio song.wav [--marks] # one offline page, with audio
/usr/bin/python3 -I local_cli.py sheet <result_id> --png DIR --audio song.wav --style poster --start 40 --end 60
```

- It reads the server's config (`--config`, default `~/.music-lens/api.json`) through `server.load_config`, so the
  same checks apply. It never prints key material.
- The commands run the server's own code in `server.Engine` (the class `App` builds on). That covers staging into
  `<root>/uploads/<op>/`, afconvert under `limit_exec.py`, the strict WAV check, the hard link into the inbox and
  `ml analyze`. The rhythm worker then runs with the same `rhythm` config section, under the same team flock
  (`<root>/.analyze.lock`, `busy` when it is held). Results go to the same `results-rhythm/` directory with the
  same cache and reuse rules. A result stored by the CLI is served by the LAN server, and the reverse also holds.
- stdout is the body the server would send: the analyze summary (with `rhythm` unless `--no-rhythm`), the `ml
  report` with the stored rhythm merged in, a rhythm timeline page (`words`/`emotion` pass through to `ml
  timeline`), or the sheet body. Errors use the server's error body on stderr. Exit codes: 0 ok, 1 error, 2 config
  or usage error.
- Limits: there is no in-process API gate across processes. A CLI analyze beside a server analyze is serialised
  by the team flock (`ml` reports `busy`, and the rhythm layer reports `busy`). Events are logged to
  `<root>/logs/local.log`.
- Stopping: SIGTERM, SIGINT and SIGHUP stop the CLI's children the same way the server's timeout paths do.
  `ml` gets SIGINT first, so its own `finally` can end its inference child, which runs in a separate session and
  holds the team lock. Only after `ml_stop_grace` (40 s) does `ml`'s process group get SIGKILL; the server's `ml`
  read timeout uses the same order. afconvert and the rhythm worker get SIGKILL to their process group at once.
  A second signal skips the grace. The CLI waits for them, removes
  `uploads/<op>/` and the inbox link `api-<op>.wav`, and exits 1 with `cancelled`. A cancelled run stores no rhythm
  file. Only SIGKILL skips this. Then `uploads/<op>/` and `<inbox>/api-<op>.wav` stay behind, and the children
  keep running, with the rhythm worker still holding the team flock. No receipt owns those paths, so remove them by
  hand once the children have ended. An analyze with the rhythm layer can take minutes, so run it in the
  background or with a long command timeout, not under a short timeout that kills it.

Tests: `tests/test_local_cli.py` (fake `ml` and fake worker; the stop-signal path; checks the CLI output byte for byte against the served
`/report`, `/timeline` and `/sheet` bodies).

## ml error reasons (`error.detail`, `error.hint`)

When `ml` fails with one of its own fixed reasons (ml 0.2.6 `Failure` texts, listed in `server.ML_REASONS` per
code), the error body keeps its fixed `message` and adds that text as `error.detail`. This applies to the server and
the local CLI (stderr JSON), on analyze and on reads. Other stderr text (paths, OS errors, diagnostics, a code the
server remaps) is never passed on: those bodies stay `{code, message}`. An analyze that ml fails as `empty_output`
(no layer succeeded: no words recognized, e.g. instrumental audio, or speech recognition failed; and no emotion
layer) with `emotion_mode` `none` or `voice` also gets `error.hint`: for instrumental audio use `emotion_mode=music`
with `rhythm=1` (CLI `--emotion-mode music`, without `--no-rhythm`) so the music-mood layer succeeds and the rhythm
layer runs. Example:
`{"error":{"code":"empty_output","message":"Analyzer returned no output","detail":"No requested analysis layer
succeeded; no result published","hint":"no layer succeeded (no words recognized, or speech recognition failed); ..."},"request_id":"..."}`.

Music fallback: when the rhythm layer is requested (`rhythm=1` or `quick`; CLI without `--no-rhythm`), mode `none`
or `voice`, and ml fails with that `empty_output` reason, ml runs once more with `--emotion-mode music`. Music mood
needs no words, so this succeeds for instrumental audio; the rhythm layer then runs as usual. ml gives the same
reason when speech recognition itself failed, so the fallback runs then too and the result has no words. The summary adds
`"emotion_fallback": "music"`, and the result is ml's music-mode result (its own `result_id`, a music-mood layer).
There is no loop: if the second run fails, its error body is returned (`detail`, no `hint`). Without the rhythm
layer nothing is retried (the error carries the hint). Audio with words is unchanged. Cost: ml runs its full speech
recognition pass before it reports no words, so every fallback request pays that pass twice (seconds for a short
clip, minutes for a long song); a later mode-`none` request for the same audio repeats both runs. When no words were
recognized, the music run is deterministic: it lands on the same `result_id` and the stored rhythm file is reused under
the usual rules. When speech recognition failed transiently, ml gives each run a new `result_id` (a `failure_attempt`
in its provenance), so the rhythm layer is computed again each time.

## Quick rhythm profile (`rhythm=quick`, `analyze --quick`)

`POST /analyze?rhythm=quick` (CLI: `local_cli.py analyze FILE --language … --quick`) runs the rhythm worker
without stem separation. The worker follows the same path it takes for a 16 kHz upload: no `--melody-source`, so
every stem stage is skipped with `canonical_16k`. Beats, tempo (Essentia and TempoCNN), key and key sections,
chords (BTC on the mix when configured), energy, genre, lyric repeats, sections (beat method) and the melody (from
the mix) are kept. Not measured: bass, other notes, drums, parts, bars and downbeats (All-In-One needs stems), swing,
mix, part positions, tone and sung notes.

- The stored file gets the server-owned field `"profile": "quick"`, but only when stems were possible and were
  skipped. The summary, `/report` and the sheet then show `profile` (`profile_note`, or a `profile` header line).
  Dropped layers read `skipped`/`unavailable`, never `empty`.
- A later `rhythm=1` run on full-band audio replaces it. Every stem layer at `canonical_16k` counts as "can
  improve". A later `rhythm=quick` run is treated like a 16 kHz upload: it reuses a stored quick or full result,
  and never replaces a full result that holds a layer made from stems (a `done` separation, bass, other, drums,
  sections model, parts, mix, part positions or tone). A full result in which every stem stage failed holds none,
  so the usual recompute rules apply and quick can replace it. Nothing measured is lost that way.
- Without `quick`, behaviour and bodies are unchanged.
- Measured run times: see [analysis layers](evaluation.md#quick-rhythm-profile).
- Chords: the full run also reads BTC chords from the mix with the same mix beats, so quick chord labels and
  segments are the same as a full run's. Quick only lacks the `/bass` slash in "display" (needs the bass stem) and
  the two-chords-per-bar split (needs bars).
- Accuracy caveat: quick melody (Melodia on the mix, not RMVPE on the vocal stem) and sections (beat method, not the
  section model) differ from a full run; their accuracy against it was not measured here. Use quick for a first look,
  then run `rhythm=1` for the layers that need stems.

## Optional rhythm layer: API

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
    them with `/timeline` `layer=beats|chords|energy|melody|genre|bass|other|drums|bars`);
  - `{"status":"busy","code":"busy"}` when the team lock is held (never waits);
  - `{"status":"timeout","code":"timeout"|"no_time_left"}`;
  - `{"status":"failed","code":...}` with the worker's error code (`invalid_audio`, `too_short`, `analysis_failed`,
    `dependency_missing`, `timeout`), or `invalid_output`, `internal_error`, `server_misconfigured`,
    `lock_unavailable`, `no_result_id`, `shutting_down`.
- Exception to the reuse rules below: with separation configured, a 16 kHz WAV upload never recomputes a stored result that holds anything made from stems, see "Vocal melody".
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
  `sections_reliability`, `melody`, `lyric_repeats`, `overview`, `genre`, `downbeats`, `duration`, `tool`, `warnings`,
  `read_next`) when a stored file exists, re-serializing the native JSON (number formatting may change). Files stored by worker 0.1.0 lack the
  0.2.0 fields and still load; the summary then has `bpm_alternatives`, `bpm_alternatives_note`, `key_sections`,
  `key_change_count` and `chords_grid` set to `null` (they are not recomputed; old chord rows stay frame-level).
  Files from 0.1.0 and 0.2.0 likewise give `double_tempo_evidence` and `double_tempo_evidence_note` as `null`, and
  files from before 0.2.2 give `tempo_cnn: null`, and files from before 0.3.0 give `energy_summary`, `sections`,
  `sections_method` and `sections_reliability` as `null`, and files from before 0.4.0 give `melody: null`, and files
  from before 0.4.3 give `lyric_repeats: null` and `overview: null`, and files from before 0.5.0 give `genre: null`
  (summary `genre` = the stored one without `windows`, plus `window_count`). `structure_debug` is never in the summary (stored file only). A damaged file gives `rhythm: {"status":"failed","code":"invalid_result"}`.
  No file: native bytes unchanged.
- `GET /timeline/<id>?layer=beats|chords|energy|melody|genre|bass|other|drums|bars` is served by the server from the stored file with the native rules:
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
- `read_next` (in `/report` and in analyze summaries whose result is stored) points an LLM at the next reads with
  this result's ID filled in: `sheet` (`GET /sheet/<id>`), `zoom` (`GET /sheet/<id>?start=&end=`), `earcheck` (only
  when `POST /earcheck` is available, the same condition as `/status` `rhythm.earcheck_available` plus the rhythm
  config) and a `note` to read the sheet first.
- `GET /sheet/<id>?format=text|json` (default `text`) builds the LLM sheet (`sheet.py`, format `music-lens-sheet/1`) from the
  stored file: `{"result_id", "format", "text"}`, or `{"result_id", "sheet"}` with the JSON the text is rendered from.
  One block per source section (ID, times, bars, key estimate and in-section scale changes over bars, or key changes from `key_sections` in a section with no scaled bar, level, chords per bar or
  timed with N.C., melody, parts, drum coverage and groove ID), each layer marked detected / empty / unavailable /
  failed. The tempo line gives Essentia's bpm, or the bar tempo when the two differ about 2x (on RWC-P the bar tempo
  matched the annotated beat on 10 of 12 such songs, Essentia on 2). On 8 of 100 RWC-P songs the two agree at the
  other level (5 doubled, 3 halved); a frozen chord-rate test could not tell them apart, so treat the half/double in
  `bpm_alternatives` as a real candidate. Give an LLM the sheet first and let it drill down with `/timeline` for exact rows. No stored file: 404
  `rhythm_not_found`; a damaged one: `invalid_result`. Question-test results: [sheet question test](sheet-eval.md).
- `GET /sheet/<id>?start=<s>&end=<s>[&format=text|json]` zooms into at most 30 s (the same span as one `/earcheck`
  clip): one block per bar overlapping the span (number as in the sheet, times, section, chord with scale and degree,
  kick/snare pattern on the bar grid's `steps` (12 or 16), other drum hits counted, melody and bass as `name@step+length` on that bar's same
  grid, other notes as count and range). Rows belong to the bar holding their onset (the last half step rounds to
  the next bar's step 0, as in the kick/snare grid); rows starting outside the span are left out; a skipped layer is
  unavailable, not empty; bars without chords (chord model off) list the timed chords instead; rows in no bar, and every row of a result without bars, are listed by time. JSON:
  `{"result_id", "zoom"}` (format `music-lens-sheet-zoom/1`). Both or neither of `start`/`end`; a span over 30 s, an
  empty span or a start at or past the song's end: 400 `bad_request` (a span over 30 s or an empty span adds
  `error.detail` naming the limit).
- `GET /sheet/<id>?view=brief[&format=text|json]` is the short sheet: the same header block (tempo, key, form, level,
  genre, layer status), then one line per section (ID, label and role, times, bars, key, level). The per-section detail
  lines and the legend lines for them (parts, melody, grooves) are left out. `GET /sheet/<id>?layers=<csv>` is the full
  sheet with per-section detail only for the chosen layers: `grid` (straddling bars, spans with no bar), `key` (key
  and scale changes), `chords`, `melody`, `parts`, `drums` (with the groove lines). Both add a `view` line, and the
  JSON gets `"view": {"layers": [...]}`. Use one of the two, never both, and not with `start`/`end`. An unknown or
  repeated layer name, an empty list, or any other `view` value gives 400 `bad_request` with an `error.detail` naming
  the options. With neither parameter, the sheet is byte-identical to the sheet before these options existed. On the
  test fixture (three sections, no bars), the text body is 2577 bytes full, 1989 brief and 2091 with
  `layers=chords`. On a five-bar unit fixture with bars and drums, it is 2786 full and 1954 brief. The saving grows
  with the number of sections and bars. CLI: `local_cli.py sheet <id> --view brief` or `--layers chords,drums`.
- `POST /earcheck/<id>?start=<s>&end=<s>[&layers=<csv>]`, body = the song's audio again (WAV, MP3 or M4A; the server
  deletes uploads after every job, so the client re-uploads). Returns an ear-check file for you to listen to: left ear the
  original, right ear `ear_check.py`'s synth replay of the stored result, as `{"result_id", "start", "end", "layers",
  "format": "wav", "sample_rate": 44100, "channels": 2, "bytes", "sha256", "audio_base64"}`. `start >= 0`,
  `0 < end - start <= 30` (numbers as in `/timeline`), `layers` a subset of `melody,bass,other,chords,drums,clicks`
  (default all but `clicks`); anything else, unknown or repeated keys: 400 `bad_request` (a bad span adds `error.detail`
  naming the 30 s limit). Same signing, framing, size
  limit, formats, staging and `Expect: 100-continue` as `/analyze`. The stored file is checked before the body is
  read: 404 `rhythm_not_found`, 500 `invalid_result`. No `rhythm` config or no `ear_check.py` next to `server.py`:
  503 `earcheck_unavailable`. It holds the API gate, and after the upload the team flock, so it never runs beside an
  analysis (409 `busy`). The upload is always converted with `limit_exec.py --listen` (44.1 kHz mono; the analyze path's 16 kHz
  is too dull to listen to) and checked strictly; a start past the audio's end gives 400 `invalid_argument`. The
  synth runs as `limit_exec.py --earcheck <rhythm python> <install dir>/ear_check.py <stored file> ...` (cwd `/`,
  minimal env, own session, 60 s wall timeout with the process group killed: 504 `timeout`; a failed run or an
  output that is not a stereo 44.1 kHz WAV of at most the span: 500 `earcheck_failed`). It is synchronous and has
  no receipt: nothing is stored and nothing runs after the reply, so a retry with a new nonce is always safe. The
  upload, the converted file and the output are deleted in every path; the log holds ids, exit codes and seconds
  only. 30 s gives 5,292,044 bytes, about 7.06 MB in base64, under the 8 MiB response cap.
- `GET /status`: when the layer is configured, adds `rhythm: {configured, python_present, worker_present,
  worker_sha256, ..., earcheck_available}` (`earcheck_available`: `ear_check.py` is next to `server.py`) (file checks only, Essentia is not run; `worker_sha256` is the start-time hash, null if unreadable). Not configured: native bytes unchanged (no `rhythm` key means unavailable).

`bpm_confidence_band` maps the native confidence to Essentia's documented BeatTrackerMultiFeature bands:
`very_low` [0, 1), `low` [1, 1.5], `good` (1.5, 3.5] (Essentia: about 80% AMLt), `excellent` above 3.5. It is
Essentia's guide, not a calibration on the team's music.

Worker output (worker 0.4.1): `bpm`, `bpm_confidence` (RhythmExtractor2013 multifeature, native 0..~5.32,
uncalibrated), `bpm_estimates` (top 5 shares), `bpm_alternatives` (`[bpm/2, bpm*2]`, 2 decimals, only values within
40..300; empty when bpm is 0) with `bpm_alternatives_note`, `double_tempo_evidence` with
`double_tempo_evidence_note` (diagnostic, see below), `tempo_cnn` (learned estimate, see below), `beats` (s, ms rounding),
`downbeats: {"status":"not_provided"}` without the section model, `key` (`tonic`, `scale`, `strength`, `profile: "bgate"`), `key_sections`
(rows `{start, end, tonic, scale, strength}`, see below), `key_change_count` (sections - 1), `key_section_window`
(20), `chords` (see below), `chords_grid` (`beat` or `frame`), `chord_frame_segment_count` (frame-level segment
count before the beat vote, stored file only), `chords_reliability` (`low_reference`, or `reference_candidate` for BTC
chords), `chord_vocabulary`, `sample_rate` 44100 (input
resampled from 16 kHz because RhythmExtractor2013 assumes 44.1 kHz), `duration`, `tool` (name, version,
`essentia_version`), `warnings` (`short_audio` < 10 s, `near_silent`, `no_beats`, `low_bpm_confidence` < 1.5,
`low_key_strength` < 0.5, `key_changes` (from 0.24.0, when bars carry a scale: the number of bars whose scale differs from the bar before, none when it never changes; otherwise when there is more than one key section), `no_chords`,
`double_tempo_evidence_unavailable`, `no_sections`, `sections_single`, `tempo_cnn_patch_count_mismatch` (0.3.1-0.4.0
only), `no_melody`, `melody_failed`, `melody_scale_degrees_unavailable`,
`melody_notes_capped`, `lyric_repeats_capped` (0.4.3), `lyric_repeats_approx_capped` (0.4.4)), `energy`, `energy_summary`, `sections`, `sections_method`, `sections_reliability`
(`low_reference`; `reference_candidate` with the section model, see "Energy and sections" and "Sections from All-In-One" below), `melody` and `melody_notes` (see "Melody features" below), and (worker 0.4.3) `structure_debug` (stored file only, see
"Energy and sections") and `lyric_repeats` (see "Lyric repeats"), and (worker 0.5.0) `genre` (see "Genre"). Audio under 1 s is refused (`too_short`).

## Overview fields

How the overview is built is in [analysis layers](evaluation.md#overview-server-read-time).

- Output: `{status: "done", source, structure, method, reliability: "low_reference", segment_count, repeat_groups:
  [{group, count, seconds}], dropped_repeats, segments, notes}`; at most 40 segments, contiguous 0..duration (times 3 decimals). Each segment
  `{start, end, kind, group (repeat only, else null), internal, label (audio only, else null), energy: {mean_db, rel_db,
  rank}, quiet, chords: {top, changes_per_min}, melody, words}`:
  - `kind: "gap"`: voice is unknown. This includes final spans where punctuation or symbols alone supported
    the vocal label, and lyric-source spans without usable word or melody evidence;
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
