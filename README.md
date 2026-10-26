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
| `limit_exec.py` | Sets RLIMIT_CPU 60 s / FSIZE 24 MiB / NOFILE 32, then `execv /usr/bin/afconvert -f WAVE -d LEI16@16000 -c 1 src dst` |
| `sign.py` | Shared canonical-string and HMAC helper (server, tests, Python clients) |
| `sign_vector.mjs` | Reference Node signer; prints or checks the fixed vector (`tests/vectors.json`) |
| `examples/api.example.json` | Example production config (`~/.music-lens/api.json`, mode 0600) |
| `examples/com.music-lens.lan-api.plist.template` | LaunchAgent **template** |
| `tests/` | unittest suite (fake `ml`, throwaway cert, 127.0.0.1 test mode) plus one opt-in live test |

## Tests

```sh
cd ~/dev/music-lens-lan
/usr/bin/python3 -m unittest discover -s tests -v            # offline; uses a fake ml and a temp root
MUSIC_LENS_LIVE=1 /usr/bin/python3 -m unittest discover -s tests -p test_live.py -v   # real wrapper, once
```

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
`stop_grace` (630 s: the 600 s analyze bound plus margin) for the running job and its waiting client. A job still
running after that is not killed: its receipt becomes `unknown` with `error_code` `interrupted_by_stop`, its paths
are logged (`receipt_unknown`, `reason: stop`) and kept, as after a crash. The LaunchAgent template sets
`ExitTimeOut` 660 so launchd does not SIGKILL the server during this wait.

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
