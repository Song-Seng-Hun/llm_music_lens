# Music Lens Mac server: operations

Where the server keeps its files, how it stops, and how to recover after a crash. The server implements the team's LAN API
spec v0.6 (`MUSIC-LENS-LAN-API-1026`, SHA256 `6282bd25…f9e524`); the last section lists where its behaviour is stricter or
more specific than that spec.

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
remain. Retained `running` receipts (ASR phase only; a rhythm-phase receipt is already `done`, see item 12 under "Request handling details") become
`unknown` on restart and never authorize an automatic retry. The flock
releases when its holding process exits; server exit or lock release alone does not prove all analysis processes
ended. Before cleanup, follow the existing owner-verification rule for the job and its descendants. Restart only
when no analyze is running if its result matters (`analyze_exit` in `api.log` precedes the final receipt and
cleanup, so it alone does not prove the pipeline is idle).


## Request handling details

1. Conversion writes to `uploads/<op>/converted.wav` and not straight to the inbox. That file then gets the
   same strict check and the same no-overwrite hard link as an uploaded WAV. The 88 MiB staging quota is
   64 MiB of upload plus 24 MiB of converted output. An ear check stages at most 64 MiB of upload, 56 MiB of
   `listen.wav` and 16 MiB of `earcheck.wav` in `uploads/ec-<hex>/`. No receipt can own such a directory, so a
   crash leftover is removed at the next start (`earcheck_leftovers_removed` with the count; only `ec-*`
   directories, only the known file names).
2. The strict WAV check also accepts WAVE_FORMAT_EXTENSIBLE, but only as plain 16-bit integer PCM (mono, mask 0 or 4,
   PCM GUID). `afconvert -f WAVE -d LEI16@16000 -c 1` writes this format for mono 16 kHz sources such as the
   fixture's M4A. The native analyzer (Python 3.12 `wave`) accepts it, and native validation stays authoritative.
3. Every header may appear at most once (not only `X-ML-*` and `Content-Length`). A GET with a `Content-Type`, any
   bytes after the head, or `Expect` gets 400 or 417. A malformed `X-ML-*` value is a 400 grammar error. An unknown
   client gets 401.
4. Status codes: target > 2048 B gives 414, headers > 16 KiB or > 40 give 431, an upload
   timeout gives 408 `request_timeout`, an early EOF gives 400 `truncated_body`, a conversion wall timeout gives
   504 `timeout`, and a conversion failure gives 400 `invalid_audio`. A body-hash mismatch gives 401.
5. A reused operation id gives 409 `operation_exists` with `request_id` (use `/receipt`). An id rejected before
   reservation (`busy`, storage, size) gets no receipt (404).
6. An address on a bind interface that falls outside `allow` is skipped and logged. The server exits (code 1) only
   when no address remains. A production `allow` must be a private /24 or narrower.
7. Bind addresses are read once at start. An address change needs a restart (KeepAlive does not detect it).
8. `server.py` adds its own directory to `sys.path` because `-I` drops it. It needs this to import `sign.py` and `sheet.py`.
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
12. Optional rhythm layer (an addition to the spec): `rhythm=1` on `/analyze`, a `rhythm` summary in `/report`, `layer=beats|chords|energy|melody|genre|bass|other|drums|bars`
   on `/timeline`, and a `rhythm` object in `/status` (see "Optional rhythm layer: API" in the [API reference](api-reference.md)). New rhythm work and the `/status`
   field need the `rhythm` config key; `/report` and `/timeline` read stored files whenever they exist. The receipt
   records the ASR outcome only; the rhythm result lives in `results-rhythm/`. The worker runs while the API gate is
   held (another API analyze meanwhile gets 409 `busy`), and it takes the team flock after
   `ml analyze` released it, so a Mac-side analyze can win the race (rhythm `busy`). A stop during the rhythm step
   does not mark the receipt `unknown` (it is already `done`), but the worker may still be running.
