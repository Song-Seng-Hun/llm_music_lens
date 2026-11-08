# Analysis layers: how each works and how it was evaluated

This is the detailed reference for the rhythm worker's layers: what each one measures, how, what it returns, and how it was
checked. Start with the [Mac server README](../README.md); routes and fields shared by all layers are in the
[API reference](api-reference.md). "Worker 0.x" notes record when a behaviour was added or changed; results stored by an
older worker keep their own fields. Quoted section names that are not on this page, such as "Optional rhythm layer" or
"Sheet PNG", are in the API reference.

All values are estimates. Measurements here cover the test material named next to them and do not stand for accuracy on music in general.

## Key sections, chords and tempo

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
only, a low-reliability reference, not a transcription. Downbeats, bars and beats per bar (3 or 4 only) need the section model ("Musical units"). A 600 s
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

## Energy and sections

Purpose: song-level form for "compose using this song as a reference" (where material repeats, where it gets loud),
not a transcription. Downbeats ("Musical units") and named parts ("Sections from All-In-One") need the section model.

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

## Melody features

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
  rhythm result). Direction rule: least-squares line of MIDI against note midpoint time; fitted change = slope x
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

## Vocal melody (optional)

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

One Demucs run gives the vocal, bass, drum and other stems for all stem layers. Each `htdemucs_ft` stem is its own model,
so the files are the same as four `--stem` runs, and one run skips three weight loads. If that run fails, every stem layer
is skipped with `separation_failed`. The stereo layer still runs Demucs separately. Running the sections model alongside
the accompaniment layer's Basic Pitch step was tried and dropped: 4% faster on a 600 s file (239.6 s to 228.9 s), no gain
on 20 s and 50 s clips, for extra process handling.

Scoop join (worker 0.20.0, vocal method only): a sung scoop from below used to come out as an extra short note a
semitone flat before the real note. After the merge, a note up to 0.12 s that touches (gap up to 30 ms) a note 1 or 2
semitones higher and at least twice as long is removed, and the longer note starts where it started. On songs 51-100 of
RWC-P against the MIDI vocal part, pitch on a 16-step grid went from F 31.5 to 34.3 and rhythm from 62.0 to 62.2, with
1,599 of 24,044 notes removed. The rule was fixed before scoring, but those songs had been used before, so the test was
not blind. A grace note shaped the same way is joined too. Notes stay low_reference; most of the flat lean against that
MIDI remains.

`melody.separation` says what was used: `{"status": "done"}`, or `{"status": "skipped", "reason": ...}` with the mix
method. `melody.method` names the method. Reasons: `not_configured`, `canonical_16k` (a 16 kHz WAV upload has no band
above 8 kHz), `band_limited`, and the failures `models_invalid`, `decode_failed`, `separation_failed`, `rmvpe_failed`,
which also add the warning `melody_separation_failed` and are retried on the next request. A stored melody from the mix
is recomputed when a later upload of the same audio can be separated. A stored result with anything made from stems (vocal
melody, bass, accompaniment, drums, model sections, part activity) stays for a later 16 kHz upload, with no recompute for any
reason, even if the worker changed; `tool.version` then shows the older worker, and a full-band upload recomputes it. Send M4A,
MP3 or a full-band WAV to get the vocal method.

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
default Demucs batch would take 34.1 GB. The one run for all four stems (above) takes 26 s and 14.4 GB, about as much as
the stereo layer's Demucs run on the same file.

Licenses:

| Part | Terms | Notes |
|---|---|---|
| demucs-mlx, Demucs code | MIT | Installed separately |
| `htdemucs_ft` weights | Not MIT. The Demucs maintainer states they are provided for research purposes (facebookresearch/demucs issues #267, #327, #508) | Not in this repository. Whoever installs them accepts those terms |
| `rmvpe_model.py` | MIT (RVC-Project). RMVPE's original code is Apache-2.0 | Notice kept in the file |
| `rmvpe.pt` | The hosting repository (lj1995/VoiceConversionWebUI) is tagged MIT, with nothing about this file or its training data | Not in this repository. Terms are unconfirmed |

## Sung notes (optional)

Vocal melody notes can come from YourMT3+ singing notes instead of RMVPE pitch plus segmentation. Off unless the `rhythm`
config has `singing`, which needs `separation`:

```json
"rhythm": {"python": "...", "worker": "...", "separation": {"demucs": "...", "rmvpe": "..."},
           "singing": {"yourmt3_python": "/Users/YOU/tools/music-lens/runtime/yourmt3/bin/python"}}
```

Copy `sing_notes.py` next to `rhythm_worker.py` (pinned by SHA-256). The model lives at `~/tools/music-lens/models/yourmt3`:
`code/` holds `model_helper.py` and `amt/src` of huggingface.co/spaces/mimbres/YourMT3 at commit
`5e66c1ea173a8186e0d20432b841d3180cc015b5`, and `amt/logs/2024/mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/checkpoints/last.ckpt`
is the Space's checkpoint saved weights-only (its state_dict), pinned by SHA-256 in the worker. The runtime is its own
Python 3.11 environment (torch 2.5.1, lightning 2.4.0, transformers 4.45.1). Nothing is downloaded at run time, and
every checkpoint read is forced to `weights_only`.

What happens on a rhythm request: after every other stem layer, the worker runs YourMT3+ on CPU on the same Demucs vocal
and keeps the notes on program 100 (singing voice); chorus (101), other programs and drums are dropped. Those notes
replace the RMVPE notes with no merge, scoop join or other rule. RMVPE still runs: `voiced_fraction` and `voiced_s` come
from it, and its notes are the fallback. The step gets the time left of the server's 300 s, minus 10 s for what
follows, and at most 150 s; with under 20 s left it does not start. The server passes its real limit for the run with
`--time-budget` (`rhythm_timeout`, 300 s by default, less when the request's own deadline is near).

`melody.singing` says what was used: `{"status": "done"}`, or `{"status": "skipped", "reason": ...}` with RMVPE notes
(or the mix method when there was no vocal). `melody.method` names the method. Reasons: `no_vocal` (no separated vocal),
`canonical_16k`, and `models_invalid`, `notes_failed`, `timeout`, `no_time`, `too_many_notes`, `no_notes` (no singing note at all), which add the warning
`melody_singing_failed`. `models_invalid` and `notes_failed` are retried on the next request that can give a vocal; the
others stay, since the same audio costs the same time. A stored melody without `singing` is recomputed the same way; a
16 kHz upload does not recompute for this.

Reliability: on test songs the pitch of sung notes matched the written vocal part about twice as often as RMVPE notes
did, and the note count came close to it; about half of the sung pitches still miss, so notes stay low_reference. Backing
vocals pass through Demucs as vocals. Measurements: [sung notes accuracy](../../docs/sung-notes-accuracy.md).

Cost for a 600 s vocal: about 80 s and 14.3 GB on CPU, and about 1,000 s of CPU time. The worker keeps its own CPU limit
at 600 s and sets a hard limit of 1,800 s, which only `sing_notes.py` raises its soft limit to.

| Part | Terms | Notes |
|---|---|---|
| `sing_notes.py` | This repository; it follows `model_helper.transcribe` of YourMT3 (Apache-2.0) | |
| YourMT3+ code (mimbres/YourMT3 Space) | Apache-2.0 per the Space card | Not in this repository |
| YourMT3+ checkpoint | Apache-2.0 per the Space card; nothing separate is stated for the weights or their training data | Not in this repository |

## Chords from BTC (optional)

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

## Bass notes (optional)

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

From worker 0.25.0 a note is kept only when a Basic Pitch note within a semitone overlaps it (Basic Pitch notes down to
80 ms, as the bass rows; a note an octave away does not count, as it kept drum ring on the drum-only song). RMVPE gives a
pitch to any voiced low energy, and Demucs leaves kick and tom ring in the bass stem, so drum-only music used to get bass
notes; Basic Pitch hears almost no note there. A starting kick is not a reason to drop a note: bass is often played with the
kick. Limits: a tom with a clear pitch is a note to Basic Pitch too and can stay; the check was measured on synthetic clips
and one drum-only song, with no real recording of bass to confirm that no true notes are lost. Results stored before 0.25.0
keep their notes until analysed again.

From worker 0.26.0 two more steps follow the check. RMVPE's pitch can split one plucked note into same-pitch pieces, and it
starts late on hard attacks. Same-pitch rows split by up to 50 ms join, unless a Basic Pitch note within a semitone starts
within 30 ms of the second. A row's start then moves back to the latest such Basic Pitch start up to 250 ms before it, unless
one starts within 30 ms after it, and never before the row ahead ends. The two values were picked on half of a real bass
recording set (single bass tracks, some with added synthetic drums) and checked on the other half; slap and harmonic notes
stay weak.

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

## Accompaniment notes (optional)

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

## Part activity

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
All three parts met the pass thresholds set in advance: pooled balanced accuracy at least 80, at least 70 on songs where the part comes and
goes, at most 20% false activity on songs without it. On one real song the entries and a break matched by ear.

Limits: a quiet part can read as off, and -15 dB was the top of the range tried. A cello or similar low instrument can read as bass.
The accompaniment is not judged on or off, because it sounds almost all the time; read its rate instead. Instruments are not named.
Section labels are not corrected by this: similar chords and busy vocals did not mean a chorus on the real song.

## Mix and stereo

How wide a section is, where it leans, how loud the song is, and where each part sits. It needs no config of its own and no new
model: it comes with the separation config. The rest of the worker still reads mono; this reads a second, stereo decode of the upload
and runs Demucs on it once more, which adds about 11 s to a five-minute song and the same memory as the first run, one after the other.

- `rhythm.mix` is `{status: "done", method, reliability: "reference_candidate", lufs, loudness_range, sections}` or
  `{status: "skipped", reason}`. `sections` has one row per `rhythm.sections` row: `{start, end, label, balance, width_db, low_width_db,
  mid_width_db, high_width_db}`. `balance` is `(R-L)/(R+L)` of energy, -1 to 1. `width_db` is side energy over mid energy, within
  +-40 dB, so -40 is mono and 0 is as much side as mid. Low is under 200 Hz, high is over 2 kHz. `lufs` and `loudness_range` are for the
  whole song, from Essentia `LoudnessEBUR128`. `null` means silence or not finite, and from worker 0.18.1 every value in a `silence` section, which can still hold music in under half its length.
- `rhythm.part_positions` is `{status: "done", method, reliability: "low_reference", threshold_db: -15, sections}` or
  `{status: "skipped", reason}`. A row is `{start, end, label, vocals, bass, drums, other}`, each `{pan, width_db, level_db}` or `null`
  when that part is under -15 dB of the section's mix, and from worker 0.18.1 in a `silence` section. `level_db` is against the section's mix.
- Reasons: `canonical_16k`, `band_limited`, `mono_source` (identical channels), `models_invalid`, and the failures `decode_failed`, `mix_failed`,
  `separation_failed`, which add a warning and are retried on the next request that can give the field. A failed second Demucs run
  leaves `mix` done. Before this worker, or without separation, both fields are `null`. Results are per section; there is no `/timeline` layer.

Evaluated locally with pass thresholds set in advance. Mix values were checked on signals with exact answers. Part values were checked by giving
separated stems known positions, levels and widths in 30 s blocks, mixing them, separating again and comparing, and all four parts
passed. That test is likely kind, because Demucs output is easier for Demucs than real tracks, which is why `part_positions` is
`low_reference`. It shows the values are read correctly, not that they say anything about a song's arrangement by themselves.

Limits: one bar was missed and accepted by the owner. AAC at 256 kbps moved `balance` by up to about 0.05 on a lone, slightly panned noise
with identical waveforms on both channels, against a bar of 0.02; the cause was not found, and on 15 real songs the largest change seen was about 0.001.
Other codecs and bitrates were not tried. Bleed between stems moves the part values, and a nearly mono part such as bass can show a large
width error. `lufs` is not per section. Sum and difference only: phase tricks and time-delay panning are not told apart.

## Part tone

How the parts sound, as far as this could be measured. It needs no config or model of its own: it reads the stems the stereo run in
"Mix and stereo" already made, so it is skipped for the same reasons, including `mono_source`, plus the failure `tone_failed`, which adds a warning and is retried.

- `rhythm.tone` is `{status: "done", method, reliability: "low_reference", sections}` or `{status: "skipped", reason}`. A row per
  `rhythm.sections` row: `{start, end, label, vocals_brightness_hz, drums_brightness_hz, other_brightness_hz, kick_weight_db, snare_brightness_hz}`.
- Brightness is the median spectral centroid of the frames within 30 dB of the section's loudest. `kick_weight_db` is energy under 120 Hz
  over all energy in the 120 ms after each kick, median over hits; nearer 0 means more of the hit is low end. `snare_brightness_hz` is the
  centroid of the same 120 ms after each snare. Fewer than 5 hits in a section gives `null`, as do a part under -15 dB of the section's mix and a `silence` section.
- To read a value, compare it with the song's other sections or with this range from 28 s blocks of pop test songs (10th percentile, median, 90th):
  vocals 519, 915, 1338 Hz (50 songs); drums 182, 1056, 3704 Hz (38); other 358, 657, 1136 Hz (50); kick weight -1.6, -0.7, -0.3 dB (38); snare 628, 1376, 4505 Hz (37). It is one song set, not a standard.

Evaluated locally with pass thresholds set in advance and no ground truth for tone: separated stems were changed by known amounts in 30 s blocks,
mixed, separated again and compared. These five values passed. Bass brightness failed, and roughness failed on every part, so neither is reported. On vocals,
drums and other, roughness moved more with a treble change than with distortion; on bass it did not follow distortion reliably. The test shows a value follows a change in the sound, not that it helps composing.

Limits: nothing here says distorted, clean, wet or dry, and instruments are not named. A mostly low kick reads near 0 whatever else changes: +6 dB under 120 Hz moved kick
weight by about 0.3 dB, and songs differ by about 0.5 dB, so read only large differences. Sections were not tested, only 28 s blocks; a short section is noisier by an unmeasured amount. Hit detection on changed drums was not tested. There is no `/timeline` layer.

## Musical units: bars, downbeats, swing, scale and chord splits

Existing results grouped by bar, for reading a song the way it is written. It needs no config of its own and no new model: it comes
with the section model and reads the downbeats that model already finds, the chords, and the drum hits.

- `rhythm.downbeats` is `{status: "done", method, reliability: "reference_candidate", count}`, or `{status: "not_provided", reason}`
  without the section model. The downbeats are the starts of the first `bar_count - extended_rows` bars and the end of the last
  of those; the added bars after them start on no downbeat.
- `rhythm.bars` is `{status: "done", method, reliability: "reference_candidate", beats_per_bar, steps, extended_rows, bar_count}`, or `null`. Read the rows with `/timeline`
  `layer=bars`: `{start, end, chord, split, scale, degree, kick, snare}`, one per pair of consecutive downbeats, then on at the median bar length to the song end. The last
  `extended_rows` rows are these added bars (the model gave no downbeat there), so only music before the first downbeat is in no bar.
  On RWC-P, 29 of 100 songs had annotated downbeats before the first one (65 in all); a frozen test of filling bars backwards
  failed on recall (55%), so those stay outside the bars.
  On RWC-P (100 songs, frozen test) the added bar starts matched annotated downbeats 77% of the time (the first added bar 91%,
  later ones less as they drift) and found 80% of the downbeats after the last one; added bars also run over a fade-out or
  silence at the end. Results stored before 0.23.0 have no `extended_rows` and end at the last downbeat. `chord` is the label sounding longest in the bar, `N` for none, `null` unless BTC made the chords. `kick` and
  `snare` list steps of `steps` equal steps per bar (a last bar cut at the song end keeps the median bar length, so it holds only the
  early steps), `null` without a drum layer. Results stored by worker 0.14.0 have no `scale` or `degree`, and those stored before 0.18.0 no `split`.
- `beats_per_bar` is the median count of the section model's beats in a bar, `3` or `4`, else `null`. `steps` is `12` for 3 and `16`
  otherwise (4 per beat). Results stored before 0.22.0 have neither and use 16 steps. Swing reads a beat as a third of the bar when it is 3.
- `split` is `[first half's chord, second half's chord]` when each holds at least 60% of its half and their roots differ, else `null`. `chord`,
  `scale` and `degree` do not change with it, so `chord` is one of the two or, in a bar with no clear winner, a third.
- `scale` is one of 12 major scales, sharps as in the chord labels, so `C` also stands for A minor. `degree` is where the chord's root
  sits in that scale: `1`, `b2`, `2`, ... `7`, so Am in `C` is `6`, and a minor song's tonic chord reads `6`, not `1`. It names the root only;
  read major or minor from `chord`. It is `null` for a bar with no chord, and both are `null` unless BTC made the chords.
  The path takes the share of each bar's chord tones inside the scale, and a change must be worth a fixed penalty, so brief visits stay in
  the surrounding scale. In results stored before 0.23.0, chords after the last downbeat count for the path but get no row.
- `rhythm.swing` is `{status: "done", method, reliability: "reference_candidate", off_beat_phase, hits, feel}`, or `null` without bars and a
  drum layer. `off_beat_phase` is the median position inside the beat of the hi-hat and cymbal hits between 0.4 and 0.8: about 0.5 for straight
  eighths, 0.67 for triplet swing. `feel` is `swung` from 0.58, else `straight`. `hits` counts those hits; under 30, `off_beat_phase` and `feel` are `null`.

Evaluated locally on RWC-P songs 51-100 against the aligned annotations and MIDI. Downbeats, the chord root per bar, and kick and snare
steps each met the pass thresholds set in advance, and the downbeats beat every 4th Essentia beat.
Beats per bar was evaluated on the Ballroom set (30 s excerpts, beat annotations by CPJKU) with pass thresholds set in advance: it read 3 on 88%
of the 175 excerpts in 3/4 and on none of 77 in 4/4. The misses read 4, or had almost no beats. 6/8 and other meters are not tested.

Limits: a wrong downbeat shifts every bar built on it; two songs scored under 50 on downbeats. The model can stop before the music does:
on one real song the bars ended about 13 s early, in a fading outro. On the 50 test songs the music ran on past the last downbeat by a median of 3.5 s, over 5 s on 14 songs, over 10 s on 5, and at most 26 s; from worker 0.23.0
  the added bars cover that span at the median bar length, drifting off the real downbeats as they go (see `extended_rows`). A bar whose own length differs from `beats_per_bar` still gets the song's step count. Drum scores leave out songs
whose MIDI has no standard kick or snare notes.

Swing was evaluated on the same songs against the MIDI hats, with its pass thresholds set in advance, and met them. Few of those songs swing (3 of 35), so
the call rests on little evidence, and sixteenth-note swing is not measured. Steady straight sixteenth hats put as many hits at 0.75 as at 0.5, so they can read as `swung` with a phase
near 0.625; no evaluated song did this, so how often it happens is unknown. A beat is a third of the bar when `beats_per_bar` is 3, else a quarter, so a bar of another length misplaces it.
A fill flag per bar was tried too and failed its threshold, so it is not provided; the hit times are in `layer=drums`.

`split` was evaluated on the same songs against the annotated chords, with 60% chosen on songs 1-50, and it missed one of its three thresholds, kept by the
owner's decision. Answers with `split` were right 87.4% of the time and 2.5% of one-chord bars were split, but it found 47.9% of two-chord bars, an F of 61.9 against a threshold of 65.
So `split` is worth reading, and `null` does not mean one chord. A half-bar reading of the root agreed 84.0% of the time, against 77.3% with one chord per bar. A change away from the middle of the bar, or a third chord, cannot show.

Scale and degree differ from `key`, one estimate for the whole song, and from `key_sections`, which comes from fixed 20 s windows of audio
and can change more often; they may disagree. On the same RWC-P reference, the 20 s windows changed key about three times per song where the reference had no change and the scale path did not, so from 0.24.0 the `key_changes` warning and the sheet's per-section changes follow the scale path when bars carry one; `key_sections` itself is unchanged. They were evaluated on RWC-P against a reference built from the MIDI notes and annotated chords, and they met the pass thresholds set
in advance, on a second try after the first failed, so those songs were no longer blind. The reference shares the method's pitch-class
idea and holds clear changes only, not checked by ear, so subtle changes are unmeasured. Most songs were right throughout, but in a few the
whole path was one scale off, and then every degree in that stretch is wrong. Treat both as an estimate. A real song's half-step rise,
heard by a person, was found about where they heard it.

## Chord display

Every `/timeline` `layer=chords` row gets `display`, the name to show a person: `Bb7`, `F#m`, `C/E`, `Bbm7/Db`. No config is needed.
`label` is unchanged and stays the value to compare, with the sharps-only BTC names; section `top_chord`, the overview and the
`/timeline` bar rows use it. The sheet and its zoom show `display`: a bar (or each half of a split bar) shows the `display` held
longest among the rows of the bar's chord, and the `label` for rows stored without one.

- Spelling: the root and the slash note follow the key section at the middle of the segment. A key whose relative major is
  F, Bb, Eb, Ab or Db uses flats; C major and A minor use Db, Eb, F#, Ab and Bb; the others use sharps. A wrong key
  gives the wrong spelling of the right pitch.
- Slash: added when one bass pitch class from the bass layer sounds for at least half of the segment and is a chord tone other
  than the root. Without a bass layer, as on a 16 kHz upload, no slash appears.

Limits: slashes are conservative, so many real inversions get none, and some that appear are wrong. On RWC-P songs 51-100
the bass under a slash matched the annotation on 75% of the slashed length, and 29% of the annotated inversions got the right slash. A bass note outside
the chord, such as `C/D`, is never written. These were measured with BTC chords only. `N` and `X` are copied as they are.

## Drum hits (optional)

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

## Sections from All-In-One (optional)

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
The song overview still builds on lyric repeats first and does not show `function`.

The `chorus` name was scored on RWC-P songs 51-100 against the AIST chorus annotation, with pass thresholds set in advance. It covers
most of the chorus time: median F 82.7 per song, where calling everything chorus gives 51.0, and 75.4% of the time it names
is chorus. It missed one bar: the first section named `chorus` is right on 68% of songs, against a bar of 80%, so this is not
a pass. Looking after the result, those misses are mostly the verse just before the first chorus, and later names do better
(second name right on 43 of 49 songs). Read a first `chorus` with care and prefer a later one. Other function names were not
scored. Annotation: AIST Annotation for the RWC Music Database.

| Part | Terms | Notes |
|---|---|---|
| `allin1_sections.py` | This repository | Calls All-In-One |
| All-In-One code | MIT (mir-aidj/all-in-one, jdf/all-in-one) | Installed separately |
| `harmonix-fold*.pth` checkpoints | Sources disagree: the Hugging Face repository is tagged MIT with an empty model card; the `all-in-one-infer` package lists the same checkpoints as CC BY-NC-SA 4.0 (mir-aidj/all-in-one#41, unanswered when checked). Treat them as non-commercial | Not in this repository. Whoever installs them accepts those terms |
| madmom | Code BSD; its data and model files are CC BY-NC-SA, non-commercial | Installed separately. Used here for audio reading, spectrograms and beat post-processing |

## Lyric repeats

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
  songs.

## Genre (optional)

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
- Accuracy (measured 2026-10-31; first 30 s of each clip at 16 kHz; the 400 styles
  mapped to each dataset's labels by a fixed rule, max over a style set; details in
  [genre estimate accuracy](../../docs/genre-accuracy.md)):

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
  and `Pop---J-pop` but accuracy on them is unmeasured.

## Overview (server, read time)

Purpose: one ordered list of song parts for the reader, built from data the stored file already holds. Computed by the
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
- Offline check (8 stored songs, read-only): one result predates 0.4.3 -> null; one has `too_little_text` -> audio
  source, 7 sections all `A` (`structure: "unknown"`); the other 7 gave 6-14 segments with 2-4 repeat blocks
  each (1-2 groups; two songs have a one-block group, flagged `internal`); repeat `rel_db`
  -2.4..+1.2 dB except two hallucination-like repeats over a fading/silent tail of one song: the -107 dB one (mostly
  silent) is dropped to `silence` (`dropped_repeats: 1`), the -23 dB one stays a repeat with `quiet: true`; repeat
  `rel_semitones` -4..+12.
- Honest limits: `repeat` = repeated ASR text, a chorus/hook candidate, not a verified chorus; verse and pre-chorus
  are not distinguished (both `vocal`); everything depends on the ASR (missed lines turn into `instrumental`,
  hallucinated text over an instrumental into `vocal` or `repeat`); split points sit on 1 s windows or section
  boundaries or word gaps; thresholds (4 s, 8 s, 25%, 40 s, 45 s) are set by reasoning, checked on 8 songs only.

## Measured costs

### Sheet PNG

Cost on a 20 s span (Apple Silicon, under `limit_exec.py`): a plain page about 0.5 s and 230 MB, marks about
0.9 s and 460 MB, a poster about 1 s and 510 MB (PNG about 2.4 MB); all well inside CPU 60 s and FSIZE 8 MiB.

### Quick rhythm profile

- Measured once on a 3.5 min pop song (stereo WAV, all optional models configured):
  - `ml` ASR: 12.6 s.
  - Full rhythm worker: 107 s. Stems took 102 s of that: sung notes 46, All-In-One sections and downbeats 19,
    stereo separation 10, bass 8, vocal melody 7, other notes 6, drums 4.5.
  - Mix-only stages took about 5 s: Essentia about 2.6, BTC chords 1.5, genre 0.6, TempoCNN 0.3.
  - Quick therefore needs about 5 s of rhythm time, so the whole analyze takes about 18 s instead of 120 s.

## Version notes

The worker version that added each layer:

- Energy and sections: worker 0.3.0
- Melody features: worker 0.4.0
- Vocal melody: worker 0.6.0
- Sung notes: worker 0.21.0
- Chords from BTC: worker 0.7.0
- Bass notes: worker 0.8.0
- Accompaniment notes: worker 0.12.0
- Part activity: worker 0.13.0
- Mix and stereo: worker 0.17.0
- Part tone: worker 0.19.0
- Musical units: worker 0.14.0; scale and degree 0.15.0, swing 0.16.0, two chords in a bar 0.18.0, beats per bar 0.22.0, bars to the song end 0.23.0
- Chord display: worker 0.10.0
- Drum hits: worker 0.9.0
- Sections from All-In-One: worker 0.11.0
- Lyric repeats: worker 0.4.3; approximate matching 0.4.4
- Genre: worker 0.5.0
