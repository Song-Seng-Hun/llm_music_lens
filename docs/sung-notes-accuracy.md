# Sung notes accuracy

Measured 2026-11-06 on the Mac (Claude-Mac), before adopting YourMT3+ singing notes as the vocal melody of the Mac analysis server (`lan/`, rhythm worker 0.21.0). Output format, failure reasons and model pins: [lan/README.md, Sung notes](../lan/README.md#sung-notes-worker-0210-optional).

## Setup

- Songs: RWC Music Database, Popular Music, songs 51-100. Truth: the vocal track of the RWC MIDI files (the mapped track with the most notes). The audio and MIDI stay local and are not in this repository.
- Vocal: Demucs `htdemucs_ft` (demucs-mlx, batch 1, seed 0) on the stereo files.
- Before (worker 0.20.0): RMVPE pitch, 0.2 s median, PitchContourSegmentation, merge, scoop join.
- After: YourMT3+ (`mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops`, CPU) on the same vocal, notes on program 100 (singing voice) only, no rule after it.
- Score: note onsets on a 16-step grid per bar; rhythm F counts an onset step, pitch F needs the step and the MIDI note. Songs 51-100 had been used for earlier melody rules; songs 76-100 were not used to choose anything here.
- Bar, written before scoring: pitch F at least 2 points higher on songs 76-100, rhythm F no more than 1 point lower. Then repeated on 51-75 as a check.
- YourMT3's data presets give RWC-Pop no training split; it is in the authors' evaluation sets, so checkpoint selection may have seen it.

## Results

| Songs | Notes | Rhythm F | Pitch F | Notes per truth note |
|---|---|---|---|---|
| 76-100 | RMVPE (0.20.0) | 61.0 | 30.5 | 1.31 |
| 76-100 | YourMT3+ singing | **75.7** | **54.2** | 1.11 |
| 51-75 | RMVPE (0.20.0) | 63.3 | 38.0 | 1.36 |
| 51-75 | YourMT3+ singing | **80.1** | **60.6** | 1.15 |

Both halves pass the bar.

## Cost

| Input | Wall time | Peak memory | CPU time |
|---|---|---|---|
| 600 s vocal, YourMT3+ alone | 82.4 s | 14.3 GB | 995 s (826 user, 168 sys) |
| 600 s vocal from the server's mono decode, YourMT3+ alone | 84.2 s | 12.6 GB | 1,017 s (840 user, 177 sys) |
| 600 s file, whole worker 0.20.0 as the server runs it | 150 s | 14.5 GB | not measured |
| 600 s file, whole worker 0.21.0, singing step not limited | 262 s | 14.5 GB | 1,636 s (758 user, 878 sys) |

M4 Max, 64 GB. Inside the worker the singing step started at 152 s and took 110 s, slower than alone; the worker ended
under 1 s after it. The server's limit is 300 s, so the step keeps a 10 s reserve.

## Limits

- One song collection (Japanese pop, 1990s studio recordings). Other styles may score differently.
- The vocal stem can include backing vocals; their notes count as sung notes.
- The server used a mono decode at the time of this comparison. These evaluation runs used the stereo files.
