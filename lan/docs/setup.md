# Music Lens Mac server: setup

How to install the Mac analysis server so other computers on your home network can use it. You do not need any of this
to analyze audio on the Mac itself: the local CLI in the [README](../README.md) works without the server.

This repository installs nothing by itself. It creates no LaunchAgent, firewall rule, certificate or key; you run each
step below yourself.

## Before you start

- A Mac with `/usr/bin/python3` (Python 3.9). The server uses only the standard library.
- The `ml` wrapper (`~/tools/music-lens/ml`) and Music Lens local set up, as described in [local/README.md](../../local/README.md).
- A client on each other computer that signs its requests as described under "Signing" below. This repository does not
  include a client.
- Your agreement to an always-on listener (RunAtLoad + KeepAlive, interfaces en0/en1, port 8765, one private /24 network).
  Addresses and network ranges in these docs are placeholders; the real ones live only in your local `api.json` and in
  the client's configuration.

## Install the server

1. Copy the code. `roll.py`, `view.py` and `view.html` draw the sheet PNG and HTML; `ear_check.py` makes ear-check
   files. The server looks for all of them next to `server.py`.
   ```sh
   mkdir -p ~/tools/music-lens/api && cp ~/dev/llm_music_lens/lan/{server.py,limit_exec.py,sign.py,sheet.py,ear_check.py,roll.py,view.py,view.html} ~/tools/music-lens/api/
   chmod 700 ~/tools/music-lens/api
   ```
2. Create the TLS certificate and key. They must have a DNS SAN and **named-curve** P-256 parameters. The system
   LibreSSL writes explicit curve parameters by default, and Python 3.9's TLS then fails with "no shared cipher".
   `<mac-host>` is the output of `scutil --get LocalHostName`.
   ```sh
   mkdir -p ~/.music-lens/tls ~/.music-lens/keys && chmod 700 ~/.music-lens ~/.music-lens/tls ~/.music-lens/keys
   /usr/bin/openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -pkeyopt ec_param_enc:named_curve \
     -days 825 -nodes -subj "/CN=<mac-host>.local" \
     -addext "subjectAltName=DNS:<mac-host>.local" \
     -keyout ~/.music-lens/tls/server.key -out ~/.music-lens/tls/server.pem
   chmod 600 ~/.music-lens/tls/server.key
   /usr/bin/openssl x509 -in ~/.music-lens/tls/server.pem -noout -fingerprint -sha256   # the pin for the client
   ```
3. Create one key per client computer (64 lowercase hex characters plus one LF, mode 0600). Name each file after the
   client, for example:
   ```sh
   (umask 077; /usr/bin/openssl rand -hex 32 > ~/.music-lens/keys/windows-client.key)
   ```
   Keep these keys separate from any other tool's keys, and never paste them into a shared log or chat.
4. Write the config from this `lan/` folder. The example uses `/Users/YOU`, and the server needs absolute paths:
   ```sh
   (umask 077; sed "s|/Users/YOU|$HOME|g" examples/api.example.json > ~/.music-lens/api.json)
   ```
   Then edit `clients` in `~/.music-lens/api.json` so it lists your clients and their key files (for example
   `"windows-client": "/Users/you/.music-lens/keys/windows-client.key"`), and set `allow` to your home network.
5. Allow incoming connections in the firewall (this needs an admin password): in System Settings > Network > Firewall >
   Options, allow `/usr/bin/python3`, or run
   `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /usr/bin/python3`.
   `/usr/bin/python3` is a shim: if the prompt names the CommandLineTools `Python3.framework` binary, allow that one.
6. Set up each client computer. Copy over, by hand: `server.pem` (the public certificate only), the SHA-256
   fingerprint from step 2, and that computer's own key file. The Windows client used with this server reads them from
   `%USERPROFILE%\.music-lens\` as `mac-cert.pem`, `client.json` and `keys\mac.key`; another client may use other names. On Windows, restrict the folder with
   `icacls "%USERPROFILE%\.music-lens" /inheritance:r /grant:r "%USERNAME%:F"`.
7. Load the LaunchAgent:
   ```sh
   sed "s|/Users/YOU|$HOME|g" examples/com.music-lens.lan-api.plist.template > ~/Library/LaunchAgents/com.music-lens.lan-api.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.music-lens.lan-api.plist
   ```
   To unload it: `launchctl bootout gui/$(id -u)/com.music-lens.lan-api`. Unloading sends SIGTERM; see
   [operations](operations.md#stopping). A clean exit does not mean every analysis finished or was cancelled.

## Turn on the rhythm layer

The rhythm layer (beats, key, chords, energy, sections, melody, genre) is off unless the config has a `rhythm` key.
Without it the server behaves exactly as before. Copy `rhythm_worker.py` and the helper scripts it runs
(`rmvpe_model.py`, `btc_model.py`, `bass_onsets.py`, `other_notes.py`, `adtof_model.py`, `allin1_sections.py`,
`sing_notes.py`) into one folder, then add:

```json
"rhythm": {"python": "/Users/YOU/path/to/essentia-runtime/bin/python",
           "worker": "/Users/YOU/tools/music-lens/api/rhythm_worker.py"}
```

Both paths must be absolute regular files, not group- or world-writable, and `python` must be executable; any other
key or value stops the server with a config error. `examples/api.example.json` leaves the key out on purpose: turning
the layer on is the owner's decision.

### Optional models

Each optional model adds layers and has its own licence; some allow only non-commercial use. Read
[Models and licences](../README.md#models-and-licences) before you install one. Each is off until its config key is set:

| Config key under `rhythm` | Adds | Details |
| --- | --- | --- |
| `separation` | vocal melody from a separated vocal (Demucs + RMVPE); needed by `singing`, `bass`, `drum_model` and `sections` | [Vocal melody](evaluation.md#vocal-melody-optional) |
| `singing` | sung notes (YourMT3+) | [Sung notes](evaluation.md#sung-notes-optional) |
| `chord_model` | chords from BTC | [Chords from BTC](evaluation.md#chords-from-btc-optional) |
| `bass` | bass notes (Basic Pitch) and accompaniment notes | [Bass notes](evaluation.md#bass-notes-optional) |
| `drum_model` | drum hits (ADTOF) | [Drum hits](evaluation.md#drum-hits-optional) |
| `sections` | named sections, bars and downbeats (All-In-One) | [Sections from All-In-One](evaluation.md#sections-from-all-in-one-optional) |
| `roll_python` | sheet PNG and HTML | below |

TempoCNN and the Discogs genre models need no config key: the worker uses them when the pinned files are in
`~/tools/music-lens/models/` ([genre](evaluation.md#genre-optional), [TempoCNN](evaluation.md#key-sections-chords-and-tempo)).

### Sheet PNG and HTML (`roll_python`)

The renderers need numpy and matplotlib (and Pillow for the HTML page), which the rhythm worker's python does not
have. Name a python that has them as `rhythm.roll_python` (checked like the other rhythm paths: a regular, executable
file, not group- or world-writable), for example the All-In-One runtime:

```json
"rhythm": {"python": "...", "worker": "...", "roll_python": "/Users/YOU/tools/music-lens/runtime/allin1/bin/python3"}
```

Without it the page index still answers (`available: false`) and rendering returns `503 roll_unavailable` (also when
the config has no `rhythm` block at all). `roll.py` also runs by hand (16-bit PCM WAV, any rate and channels):
`ROLL_PYTHON -I roll.py RESULT.json AUDIO.wav PAGE OUT.png [MARKS.json]` or
`ROLL_PYTHON -I roll.py --poster RESULT.json AUDIO.wav START END OUT.png TITLE CREDIT`.

## Signing

The canonical string is the UTF-8 bytes of 8 fields joined by LF, with no trailing LF: `CLIENT`, `METHOD`, `TARGET`
(exactly as sent, non-ASCII percent-encoded), `CONTENT_TYPE` (empty for GET), `CONTENT_LENGTH` (`0` for GET),
`TIME`, `NONCE`, `BODY_SHA256`. The signature is `X-ML-Sig = hex(HMAC-SHA256(key, canonical))`, where the key is the
**32 raw bytes** decoded from the 64-hex key file. Percent-encode labels as `encodeURIComponent` does (a space becomes
`%20`, and `+` is a literal plus sign). `sign.py` (Python) and `sign_vector.mjs` (Node) implement it; the fixed test
vector is in `tests/vectors.json`.

## Windows: name resolution

On Windows, Node 22 `getaddrinfo` may return `ENOTFOUND` for `<mac-host>.local`. The client may then resolve the name
through the Windows DnsClient (`Resolve-DnsName -Type A`) or an optional configured address list. It keeps only A
records inside the `allow` /24, connects by IP with TLS `servername` set to the `.local` name, and keeps the CA, the
hostname check and the certificate pin unchanged.
