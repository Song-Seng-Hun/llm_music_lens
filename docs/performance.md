# Memory and time

Measured on one Mac Studio (Apple M4 Max, 16 cores, 64 GB unified memory, macOS) on 2026-11-09, master 4c4b035, with every optional model of the Mac analysis server turned on, models already on disk and the OS file cache warm. Other machines were not measured; an 8 GB or 16 GB Mac has not been tried. The web app runs in the browser and was not measured here.

A Mac analysis run has two parts: speech recognition and the optional emotion or mood scores (Music Lens local, `ml`), then the rhythm layer (beats, chords, sections and, in a full run, the separated parts). The tables below time each part on its own.

## Shared stereo stems with mono bass notes

Worker 0.28.0 shares one stereo all-stem separation. When bass notes are configured it also runs a mono bass-only separation.
On the same Mac, one fresh paired run of a 358 s stereo recording took 124.50 → 113.18 s for full rhythm (−9.09%),
with 31.12 → 19.63 s in Demucs. Both runs reported 8.73 GB maximum process RSS. The mono bass WAV matched byte for byte
and bass note rows matched on all three recorded inputs. On those inputs vocal-note F1 changed by −0.61 points on average
and drum-hit F1 by +1.19. This measured pair excludes speech recognition and network transfer.

## Rhythm layer, whole runs at 4c4b035

| Song | Mode | Wall time | Peak memory |
| --- | --- | ---: | ---: |
| 20 s clip | quick (`--quick`) | 3.6 s | 1.0 GB |
| 50 s clip | quick | 3.2 s | 1.0 GB |
| 600 s loop | quick | 16 s | 1.6 GB |
| 20 s clip | full | 17 s | 5.3 GB (Demucs) |
| 50 s clip | full | 25 s | 5.8 GB (Demucs) |
| 600 s loop | full | 183 s | 15 GB |

Quick peaks are the process tree's resident memory (RSS). Full-run peaks: for the 600 s loop, system memory in use (active + wired + compressed pages from `vm_stat`) minus what was in use before the run, sampled every 0.5 s. For the short clips, the largest single step's peak footprint (Demucs), because RSS does not see the memory Demucs holds on the GPU (MLX). Leave some room above these numbers.

The first full run after an install, or after the font cache folder is removed, is about 8 s longer: allin1's import builds matplotlib's font list once (20 s clip: 25 s instead of 17 s).

## Rhythm layer, steps of a full run

Each step runs as its own process, one after another. Time and peak footprint of each process:

| Step | 20 s clip | 50 s clip | 600 s loop | Peak footprint (600 s) |
| --- | ---: | ---: | ---: | ---: |
| Demucs, mono stems | 1.3 s | 2.5 s | 26 s | 13.7 GB |
| Demucs, stereo stems (mix and tone) | 1.3 s | 2.5 s | 26 s | 14.1 GB |
| YourMT3+ sung notes | 4.3 s | 6.4 s | 45 s | 3.7 GB |
| allin1 sections | 2.1 s | 3.1 s | 20 s | 13.8 GB |
| Basic Pitch, other notes | 0.7 s | 0.9 s | 20 s | 0.9 GB |
| Basic Pitch, bass onsets | 0.7 s | 0.8 s | 6.4 s | 0.7 GB |
| RMVPE, vocal and bass pitch (two runs) | 1.9 s | 2.7 s | 17 s | 2.4 GB |
| ADTOF drums | 0.8 s | 1.0 s | 4.2 s | 8.8 GB |
| BTC chords | 1.1 s | 1.1 s | 2.2 s | 0.5 GB |
| everything else, in the worker | about 3 s | about 3 s | about 18 s | |

About 10 s of every full run is fixed start-up cost, whatever the song's length: Python and library imports and model loading in each step (YourMT3+ about 3 s, allin1 1.6 s, BTC 1 s, the rest under 0.8 s each). On short songs it is most of the time; on a 10-minute song, about 5%.

## Speech recognition and emotion (`ml`)

| Song | Layers | Wall time | Peak memory |
| --- | --- | ---: | ---: |
| sung 6-minute song (363 words, Japanese) | words | 27 s | 7.1 GB |
| same | words + voice emotion | 55 s | 6.9 GB |
| same | words + music mood | 35 s | 6.8 GB |
| 50 s clip, almost no lyrics | words | 3.6 s | 4.7 GB |
| same | words + voice emotion | 11 s | 4.7 GB |
| same | words + music mood | 4.9 s | 4.7 GB |
| 600 s loop, almost no lyrics | words | 8.8 s | 5.2 GB |
| same | words + voice emotion | 52 s | 5.5 GB |
| same | words + music mood | 21 s | 5.2 GB |

Whisper large-v3 time grows with the amount of singing, not only with length. Peak memory here is system memory in use minus the level before the run (the process tree's RSS stayed at 3.6–3.8 GB). Voice emotion scores 4 s windows, music mood 5 s windows.

## Disk

With every optional model on: models about 5.6 GB, Python environments about 5.0 GB.

- Whisper large-v3: 2.9 GB. Emotion and mood models: 1.7 GB.
- Demucs htdemucs_ft weights: 0.64 GB. YourMT3+: 0.18 GB. RMVPE: 0.17 GB. allin1, BTC, ADTOF, TempoCNN and the Discogs genre models: 0.05 GB together.
- Python environments: speech recognition 1.1 GB, Essentia/emotion 1.5 GB, YourMT3+ 0.9 GB, allin1 0.8 GB, Basic Pitch 0.5 GB, Demucs 0.3 GB.
- Each analysis also needs scratch space for its separated stems while it runs (about 1 GB for a 10-minute song); it is removed at the end of the run.

## How it was measured

- Inputs: a 20 s clip, a 50 s clip and that 50 s clip looped to 600 s (all 44.1 kHz stereo), plus one sung 6-minute song from the RWC Popular Music database for speech recognition. One run at a time under the team lock, with no other heavy job on the machine.
- Rhythm runs used the server's worker command line with the configured models. Step times and per-process peak footprints come from `/usr/bin/time -l` around each step.
- Quick runs are the same command without the original upload, so no stems are made.
- Speech recognition ran Music Lens local 0.2.6 with the configured models on the 16 kHz mono copy the server makes.
