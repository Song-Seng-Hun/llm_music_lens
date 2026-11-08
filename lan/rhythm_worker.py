"""Music Lens LAN API optional rhythm layer worker (Essentia 2.1-beta6-dev DSP plus one pinned local TempoCNN model,
no network).

Usage: <essentia python> -I rhythm_worker.py [--words-stdin] WAV
       <essentia python> -I rhythm_worker.py --tempo-cnn-model /abs/model.pb [--words-stdin] WAV   (tests and
       measurement only; the server never passes --tempo-cnn-model. The SHA-256 pin is not overridable, so the
       option can only select a byte-identical copy or make tempo_cnn unavailable.)
       --genre-models /abs/dir (0.5.0; tests and measurement only, same rule: the three pinned Discogs files are
       looked up by name in that directory and their SHA-256 pins are not overridable)
       --demucs /abs/demucs-mlx --rmvpe-model /abs/rmvpe.pt --melody-scratch /abs/dir [--melody-source /abs/upload]
       (0.6.0; melody from a separated vocal, see "Vocal melody" below. No --melody-source = canonical 16 kHz upload.)
       (0.17.0: the same options also give mix and part_positions from a stereo decode, see "Mix and stereo" in README.md.)
       (0.19.0: and tone from those stems, see "Part tone" in README.md.)
       (0.20.0: vocal melody notes get a scoop join, see "Vocal melody" in README.md.)
       --singing-python /abs/python (0.21.0; with the Demucs options: vocal melody notes from YourMT3+ singing notes,
       RMVPE notes as the fallback, see "Sung notes" in README.md)
       --time-budget S (0.21.0; the server always passes it): the seconds the server gives this run; the singing step
       fits in what is left of it. Without it the server's default 300 s is assumed.
       --chord-model /abs/btc_model_large_voca.pt (0.7.0; chords from BTC instead of ChordsDetection)
       --basic-pitch-python /abs/python (0.8.0; with the Demucs options: a bass layer, see "Bass notes" below, and from
       0.12.0 an accompaniment layer, see "Accompaniment notes" in README.md)
       --drum-model /abs/checkpoint.index (0.9.0; with the Demucs options: a drum layer, see "Drum hits" in README.md)
       With the Demucs options, 0.13.0 adds part activity per section, see "Part activity" in README.md.
       With the section model, 0.14.0 adds downbeats and bars, and 0.15.0 a scale and degree per bar, and 0.16.0 swing, and 0.18.0 a bar's two chords, and 0.22.0 beats per bar, and 0.23.0 bars to the song end (extended_rows), and 0.24.0 the key_changes warning from bar scales, see "Musical units" in README.md.
       0.25.0 keeps a bass note only where Basic Pitch hears a note within a semitone of it, and 0.26.0 joins split notes and
       moves late starts back to Basic Pitch's, see "Bass notes" in README.md.
WAV is the server's already-validated canonical file (PCM mono 16 kHz s16, 0 < duration <= 600 s).
--words-stdin (0.4.3; the server always passes it): stdin holds one JSON object {"words": [[text, start, end], ...]}
(the ASR words of the same result) or {"words": null, "reason": code} (the server could not provide them), at most
WORDS_MAX_BYTES; used only for lyric_repeats. Without the option lyric_repeats is unavailable (no_words_input).
stdout: one JSON object. Error: exit 1, one JSON line {"error":{"code","message"}} on stderr.

Choices:
- Audio is resampled 16 kHz -> 44.1 kHz (Essentia Resample) because RhythmExtractor2013 has no sample-rate
  parameter and assumes 44.1 kHz; KeyExtractor/HPCP/ChordsDetection then run at the same rate. The source
  carries nothing above 8 kHz; that does not matter for beats, key or chroma below 5 kHz.
- bpm / beats / bpm_confidence: RhythmExtractor2013 method "multifeature". Its confidence is native,
  uncalibrated, on a 0..~5.32 scale (Essentia: >3.5 means very likely correct, <1.5 low).
- bpm_alternatives: [bpm/2, bpm*2] kept within 40..300. The metrical level (half/double tempo) is ambiguous for the
  tracker; no heuristic picks one.
- key: KeyExtractor profile "bgate" (Essentia's default; fit to real music not measured here).
- key_sections: the same KeyExtractor on fixed 20 s windows (a last window under 10 s joins the previous one), then
  adjacent windows with the same tonic+scale merged (strength = plain mean of the window strengths, null if any is
  null). 20 s holds ~8-10 bars at common tempos, enough chroma for a key profile; shorter windows flip on single
  chords. Boundary resolution is therefore one window (20 s). Weak (low strength) sections keep their row.
- chords: HPCP (12 bins) + ChordsDetection (hop 2048, window 2 s) frame labels. With beats, labels are re-voted per
  beat interval (plus the head before the first beat and the tail after the last): the label with the largest
  summed strength wins (null strength counts 0; ties: more frames, then earliest frame), then identical neighbours
  merge, so every segment spans >= one beat except head/tail ("chords_grid": "beat"). Without beats, consecutive
  identical frame labels merge ("frame"). Major/minor triads only; reliability "low_reference".
- chords with --chord-model (0.7.0): BTC frame labels (CHORD_VOCABULARY) go through the same beat vote, by frame count
  because BTC gives no strength (null). A segment with no beat inside it gets "one_beat": true: these agree with hand
  annotations much less often, yet folding them into a neighbour lost more than it fixed, so they stay and are marked.
  Any BTC failure falls back to ChordsDetection with the warning chord_model_unavailable.
- chord display (0.10.0): every chord row gets "display", the label with its root spelled for the key section at the
  segment's middle, plus "/bass" when one bass pitch class sounds for at least half of the segment and is a chord tone other
  than the root. "label" stays as it was, because sections and the overview compare labels.
- downbeats: Essentia has no native downbeat tracker; reported as {"status":"not_provided"} unless the section model
  gave them (0.14.0, see "Musical units" in README.md).
- double_tempo_evidence (diagnostic, uncalibrated; no decision, bpm untouched): onset-strength envelope = L1
  half-rectified spectral flux (Essentia Flux) of Hann-windowed 1024-sample frames, hop 512 (11.6 ms), frames centred
  on i*hop, divided by its maximum (0..1). Flux reacts to any broadband or low-frequency attack (a kick), unlike HFC,
  and needs no phase. It is max-pooled over +-30 ms at each tracked beat and at the midpoint of each consecutive
  beat pair (gap 0.12..1.5 s): 30 ms absorbs tracker jitter plus the one-frame flux lag, and stays far below the
  shortest half-beat (144 ms at 208 BPM), so beat and midpoint windows never overlap. If the true tempo is double,
  strong onsets should fall at the midpoints too.
- tempo_cnn (learned estimate, uncalibrated; bpm untouched): the pinned deeptemp-k16-3 TempoCNN model, loaded only
  after the file is verified (regular file opened with O_NOFOLLOW, <= 64 MiB, SHA-256 equal to the pin); otherwise
  {"status":"unavailable","reason"} and the rest runs as before. Input: the 16 kHz source resampled to 11025 Hz
  (Essentia Resample, the model's inference rate). TempoCNN (aggregation "majority") gives the global BPM and one
  BPM per ~11.9 s patch (hop ~5.9 s; audio under ~12 s gives no patch); TensorflowPredictTempoCNN on the same
  audio and model gives each patch's 256-class softmax. Verified on synthetic audio (2026-10-26): predictions have
  shape (patches, 256), rows sum to 1, and 30 + argmax(row) equals TempoCNN's local BPM for every patch, i.e.
  class i = 30 + i BPM (30..285; the model json says "last ... 286"), while TempoCNN's localTempoProbabilities is
  only the per-patch maximum (shape (patches,)), so `top` comes from the mean of the softmax rows. The worker
  re-checks that argmax relation on every run. Any exception -> {"status":"failed","code"}; never fails the worker.
  0.3.1: both calls get batchSize 64, patchHopSize 128, lastPatchMode "discard" explicitly. Measured (2026-10-28,
  synthetic 12-600 s): patch counts already matched at every length with the shared defaults, but the predictor's
  default batchSize 16 (TempoCNN: 64) changed softmax values by up to 6e-7, while batchSize 64 reproduced TempoCNN's
  per-patch maxima exactly, so a near-tie could flip the argmax (likely cause of one real-song inconsistent_output).
  A patch whose local BPM class is within 1e-6 of the row maximum (about 2x the observed batch noise, far below the
  3-decimal probabilities reported) counts as consistent. inconsistent_output now carries `detail` (class_count_
  mismatch, patch_count_mismatch, argmax_mismatch, global_out_of_range); a patch count off by exactly one (not seen,
  kept as a guard) uses the shared patches plus warning tempo_cnn_patch_count_mismatch.
  Residual: the file could change between hashing and the Essentia load (same trust as the worker file itself).
  0.4.1: one source only. A real song (274 s, ~21 s near-silent tail) failed argmax_mismatch on its last patch with
  a real probability gap of 0.08, and a synthetic track with a 20 s silent tail reproduced it (gap 0.04): TempoCNN
  and TensorflowPredictTempoCNN disagree on near-silent patches, so the 0.3.1 batch-size explanation was wrong.
  TempoCNN is no longer called. Everything comes from TensorflowPredictTempoCNN's per-patch softmax: local BPM = 30 +
  argmax per patch; global bpm = the most frequent local BPM (Essentia documents TempoCNN's global as an aggregation
  of localTempo, recommending "majority" voting), ties: higher mean probability, then lower BPM; top = mean softmax.
  A patch (frames 1024/hop 512 at 11025 Hz, 256 frames, patch hop 128 frames: start 5.944 i s, length 11.935 s) is
  excluded first (`silent_patches`) when more than half of the 1 s energy windows in its span are below SILENCE_DB
  (the sections rule). A plain patch-RMS rule was tried first and kept a patch of 10 s silence + 2 s music (its RMS
  is the music's minus ~8 dB), which voted 30 BPM. All excluded -> failed no_audible_patches.
- silence (0.4.1): SILENCE_DB -60 dBFS (RMS per 1 s energy window; quiet room tone and dithered digital silence sit
  far below it, soft music above). A section with more than half of its energy windows (by midpoint) below it is
  labelled "silence": it takes no letter, is no grouping reference, similarity_to_first null. energy_summary peaks and
  quietest consider only windows with no value below it; silence_s counts the seconds below it.
- energy (0.3.0): RMS dBFS of the 16 kHz source per 1 s window (floor -120). Plain dBFS rather than Essentia Loudness
  (Stevens power law, unitless): a reproducible unit; not perceptual (unweighted, not LUFS). energy_summary: 3 loudest
  non-overlapping 8 s windows (~4 bars at 120 BPM, one phrase) and the quietest one, plain means of the dB values.
- sections (0.3.0, reliability "low_reference"): beat-synchronous HPCP chroma + z-scored MFCC 2-13 (level coefficient
  left out), cosine SSM, Foote checkerboard 2 x 16 beats (a 4-bar loop fits one half) computed only where the full
  kernel fits (truncated edge kernels gave false boundaries), boundaries at novelty >= 0.15 (synthetic: true changes
  0.33-0.38, inside one loop <= 0.12) >= 8 beats apart, then letters by segment-mean similarity >= 0.75 to each
  group's first segment, in order of first appearance. Similarity groups, not verse/chorus. No beats -> [].
- melody (0.4.0, reliability "low_reference"): the predominant pitch of the full mix (usually the lead vocal; intros
  and solos may track an instrument, accompaniment-only passages track the loudest line, e.g. a chord root). No
  vocal separation. EqualLoudness + PredominantPitchMelodia run on the 16 kHz source itself with frameSize 1024
  (64 ms; the 44.1 kHz default 2048 is 46 ms) and hopSize 128 (8 ms); time-based parameters (ms, cents/ms) are rate
  independent, maxFrequency is set to the 8 kHz Nyquist (the default 20 kHz is above it) and the salience range is the
  default 55 Hz..1760 Hz with minFrequency 80 Hz. Measured on synthetic lines (2026-10-28): 16 kHz direct (frame 1024 or
  2048) recovered every note of a 0.5 s line at MIDI 69-81, and 20 of 24 at MIDI 48-60 where the same audio
  resampled to 44.1 kHz with the defaults gave 12; frameSize 512 split notes. PitchContourSegmentation (same hop,
  defaults: min 0.1 s, 60 cents, rms -2) turns the contour into notes; midi is rounded to the nearest semitone (A4 = 69 = 440 Hz).
  Stored notes are capped at MELODY_MAX_NOTES (6000 = 600 s / 0.1 s minimum note length, so never reached by valid
  output; kept as a guard with a warning). notes_per_second = notes / their summed duration, for the song and per
  section (onset attribution, full note length).
  0.4.2 note merge (8 real songs with 0.4.1: mean note 0.155-0.248 s, 75-90% of notes touching the next, vibrato
  read as 70/72/70/72 every ~0.1 s; synthetic +-100 cent vibrato gave 51 pieces for 4 held notes). Before
  segmentation the voiced pitch contour is median-filtered in cents over MEL_SMOOTH frames (25 x 8 ms = 0.2 s, one
  period of 5 Hz vibrato: the median of a symmetric oscillation over a period is its centre, while a step whose
  plateau is longer than half the window, 0.1 s, keeps its edges), inside each voiced run only (edges padded with
  the nearest value; unvoiced frames untouched). Synthetic: +-100 cents at 5 Hz 54 pieces -> 4 (exact), at 6 Hz
  53 -> 7; a 0.12 s stepwise run, a mordent and a turn unchanged. After segmentation: (1) same-pitch join: consecutive notes with the same midi and gap <= MERGE_GAP (30 ms) become one;
  (2) oscillation join: a run of consecutive notes, every gap <= 30 ms, pitch span <= MERGE_SPAN (2 semitones), with
  >= MERGE_REVERSALS (2) sign changes between consecutive nonzero pitch moves (A-B-A-B = 3 alternating moves; the
  spec's "3 reversals" is read as that pattern, since a +-100 cent held note gave 72-71-70-71-72-71, 2 sign changes),
  where every piece is shorter than MERGE_SHORT (0.2 s) except, in a 2-semitone run, pieces at the middle pitch
  (shorter than MERGE_CENTRE 0.5 s: the rounded pitch sits on the centre for most of a +-100 cent cycle, and step 1
  already joined its pieces), becomes one note; from each start the longest qualifying run wins (greedy, left to
  right); its pitch = duration-weighted MEAN rounded half up (the median of a 70/72 alternation is an extreme, the
  mean is its centre 71); (3) same-pitch join again, so a merged vibrato absorbs touching longer notes of its own
  pitch. All merge thresholds are compared in integer milliseconds (rows are ms-rounded, so this is exact and
  offset-independent) and the mean uses integer ms weights: pitch = (2 sum(w midi) + sum(w)) // (2 sum(w)).
  Monotonic runs, runs spanning more than 2 semitones, figures with one turn (mordent 72-74-72) and long
  alternations (pieces >= 0.2 s) stay; a trill or neighbour-note figure within 2 semitones with pieces < 0.2 s
  (72-74-72-74) is merged: at note level it is indistinguishable from wide vibrato.
  note_count_raw = segmentation output after smoothing, before the merge. Not a transcription: octave errors, split or merged repeated notes.
- structure_debug (0.4.3, stored file only; calibration data, the detector parameters are unchanged): the Foote
  novelty at every beat boundary where the full kernel fits (times = the beat times, values rounded to 4 decimals,
  at most one per beat, so <= MAX_BEATS; not downsampled), the kernel/threshold/gap/grouping parameters, `peaks` =
  every local maximum of the novelty under the boundary rule's +-8 beat neighbourhood and end margins but with no
  threshold (all selected ones plus the strongest others, at most SEC_DEBUG_PEAKS, sorted by time; `selected` =
  became a section boundary), and `section_similarity` = the full symmetric matrix of segment-mean similarities
  between all sections (silence included; <= 40 x 40, 3 decimals; null when fewer than 2 x 16 beat intervals, the
  single-section case without features). null without beats. Measured 20.7-27.1 KB for 600 s synthetic at 120 BPM
  (27.1 KB with 40 sections); estimated under 60 KB at 208 BPM with 300 peaks.
- lyric_repeats (0.4.3): repeated ASR text, likely chorus/hook lines; not verified, as good as the ASR. Each word's
  text is NFKC-normalized and lowercased, and only letters, digits and marks (Unicode L*, N*, M*) are kept
  (punctuation, symbols, spaces dropped). Words are sorted by start and clamped to [0, duration]. Units: when the
  median normalized word length is 1 (char-level ASR such as Japanese/Chinese Whisper output) the units are
  characters, each timed by an even split of its word span, and an n-gram is LYRIC_N_CHAR (8) characters (about one
  sung phrase fragment, 1-2 s); otherwise (space-delimited words: English, Korean) the units are whole words and an
  n-gram is LYRIC_N_WORD (4) words, since 8 letters are only ~1.5 English words and common pairs ("iloveyou")
  recur outside refrains. Every n-gram occurring >= 2 times with its occurrence starts spread >= LYRIC_SPREAD (15 s;
  compared in whole microseconds since 0.4.4, so an exact 15 s spread counts after any time shift)
  marks all units of every occurrence covered; covered units closer than LYRIC_JOIN (3 s) join into blocks; blocks
  shorter than LYRIC_MIN_BLOCK (4 s) are dropped; blocks sharing a repeated n-gram are one group (union-find over
  every retained block touched by any unit of any occurrence, also when a >3 s pause splits it; ids
  0, 1, ... in order of first appearance). At most LYRIC_MAX_BLOCKS (40) blocks (the longest; warning
  lyric_repeats_capped). Fewer than LYRIC_MIN_CHARS (50) normalized characters: unavailable too_little_text (no
  word at all: no_words). Independent of sections; clients compare. Untested on real English or Korean songs.
  0.4.4 (char units only): katakana is folded to hiragana, and besides exact 8-grams, LYRIC_APPROX_W (10) char
  windows within LYRIC_APPROX_K (2) edits whose starts are >= 15 s apart count as a repeat pair, trimmed at both ends
  while the chars at the same offset differ (candidates share a
  4-char seed; seeds seen > 64 times skipped; at most 200,000 checks, else warning lyric_repeats_approx_capped).
  Reason: Whisper large-v3-turbo misspells a chorus differently each time, so exact 8-grams found only 36% of the
  repeated reference-lyric characters on 7 J-pop songs; this rule found 65% at precision 0.97 (scratch check
  2026-10-28, j-lyric references; tuned on those 7 songs only).
- genre (0.5.0, reliability "model_estimate"): Essentia's Discogs-EffNet embedding (discogs-effnet-bs64-1) plus the
  genre_discogs400 head, both pinned like TempoCNN (O_NOFOLLOW, regular file, <= 64 MiB, SHA-256), on the 16 kHz
  source itself (the models' rate). The 400 class names ("Parent---Style", 15 parents) are read from the head's
  pinned json and parsed from exactly the bytes that matched the pin: one source of truth shipped with the model,
  as fixed as an embedded list, without 10 KB of literals here. Patches: 128 mel frames (hop 256 = 16 ms), patch hop
  62 frames = 0.992 s (measured 2026-10-31: 1 patch from ~2.04 s, 29 for 30 s, 603 for 600 s); near-silent patches
  (the tempo_cnn rule on the frame-centre span) are excluded. Scores = mean per-patch sigmoid (multi-label, not
  probabilities); top 5 styles, top 5 parents (parent = max over its styles), and per fixed 30 s window (last under
  15 s joins the previous, the key_sections rule; a patch belongs to the window holding its centre) the top 3
  styles. No patch -> unavailable too_short; all silent -> unavailable no_audible_patches; head output not 400
  columns -> failed class_count_mismatch; any exception -> failed; never fails the worker.
"""
import bisect
import errno
import hashlib
import json
import math
import os
import re
import resource
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave

TOOL = {"name": "music-lens-rhythm-worker", "version": "0.26.0"}
SRC_RATE = 16000
RATE = 44100
MAX_SECONDS = 600.0
MIN_SECONDS = 1.0  # below this the analysis is refused
SHORT_SECONDS = 10.0  # below this a warning is added
START = time.monotonic()  # reset in main; sing_input counts the server's time from here
BUDGET = 300.0  # s; --time-budget, else SING_BUDGET (set in main)
WALL_SECONDS = 330  # self-bound in case the parent never kills us (server wall timeout is 300 s)
CPU_SECONDS = 600
SING_CPU_SECONDS = 1800  # hard CPU limit; only sing_notes.py raises its soft limit to it (600 s vocal: 995 s CPU)
HOP = 2048
FRAME = 4096
KEY_PROFILE = "bgate"
LOW_BPM_CONFIDENCE = 1.5
LOW_KEY_STRENGTH = 0.5
MAX_BEATS = 4000  # 600 s at RhythmExtractor2013's maxTempo 208 yields at most ~2080
MAX_ESTIMATES = 5
ALT_MIN_BPM, ALT_MAX_BPM = 40.0, 300.0
ALT_NOTE = "metrical level is ambiguous; half or double tempo may match a published tempo"
KEY_WINDOW = 20.0
KEY_MIN_TAIL = 10.0  # a last window shorter than this joins the previous one
ONSET_FRAME, ONSET_HOP = 1024, 512
ONSET_METHOD = "spectral_flux_l1_halfrect_hann1024_hop512_maxnorm"
DTE_WINDOW = 0.030  # max-pool half-width, s
DTE_STRONG = 0.5  # a midpoint is "strong" when its onset >= this x the mean onset of its two flanking beats
DTE_MIN_PAIRS = 7  # ~8 beats
DTE_MIN_BEAT_MEDIAN = 1e-3  # of the envelope maximum; below this the ratio is null (ratio therefore <= 1000)
DTE_GAP = (0.12, 1.5)  # beat pairs outside this gap (s) are skipped (tracker range 40..208 BPM is 0.29..1.5 s)
DTE_NOTE = "diagnostic only, uncalibrated, untested on real songs; do not pick a tempo from it alone"
TEMPO_CNN_MODEL = os.path.expanduser("~/tools/music-lens/models/tempocnn/deeptemp-k16-3.pb")
TEMPO_CNN_SHA256 = "21c328332a221695dd6e8572728c617373064df882e8f81da6d88dc3a821e3b3"
TEMPO_CNN_NAME = "deeptemp-k16-3"
TEMPO_CNN_RATE = 11025
TEMPO_CNN_CLASSES = 256
TEMPO_CNN_MIN_BPM = 30  # class i = 30 + i BPM
TEMPO_CNN_MAX_BYTES = 64 * 1024 * 1024
TEMPO_CNN_TOP = 3
TEMPO_CNN_BATCH = 64  # TempoCNN's default; the predictor's default is 16 (see tempo_cnn notes in the docstring)
TEMPO_CNN_PATCH_HOP = 128  # both algorithms' default, passed explicitly so the patching matches by construction
TEMPO_CNN_LAST_PATCH = "discard"
TC_FRAME_HOP, TC_PATCH_FRAMES, TC_FRAME = 512, 256, 1024  # TensorflowInputTempoCNN at 11025 Hz (fixed in Essentia)
TEMPO_CNN_NOTE = "learned estimate, uncalibrated on team songs; compare with bpm and bpm_alternatives"
GENRE_DIR = os.path.expanduser("~/tools/music-lens/models/discogs")
GENRE_EMBED_FILE, GENRE_EMBED_SHA256 = ("discogs-effnet-bs64-1.pb",
                                        "3ed9af50d5367c0b9c795b294b00e7599e4943244f4cbd376869f3bfc87721b1")
GENRE_HEAD_FILE, GENRE_HEAD_SHA256 = ("genre_discogs400-discogs-effnet-1.pb",
                                      "3885ba078a35249af94b8e5e4247689afac40deca4401a4bc888daf5a579c01c")
GENRE_LABELS_FILE, GENRE_LABELS_SHA256 = ("genre_discogs400-discogs-effnet-1.json",  # the head's metadata: 400 classes
                                          "2d367319d9b782ffa10f69abf0e805b3ac4e10899025e5bdbaceda3919b243e0")
GENRE_EMBED_NAME, GENRE_HEAD_NAME = "discogs-effnet-bs64-1", "genre_discogs400-discogs-effnet-1"
GENRE_EMBED_OUTPUT = "PartitionedCall:1"  # the 1280-d embedding (PartitionedCall:0 is the effnet's own 400 sigmoids)
GENRE_HEAD_INPUT, GENRE_HEAD_OUTPUT = "serving_default_model_Placeholder", "PartitionedCall:0"
GENRE_LABELS_MAX_BYTES = 1024 * 1024
GENRE_CLASSES = 400
GENRE_SEP = "---"  # class names are "Parent---Style"
GENRE_TOP, GENRE_PARENTS, GENRE_WINDOW_TOP = 5, 5, 3
GENRE_WINDOW = 30.0  # s; fixed windows for the per-window estimate
GENRE_MIN_TAIL = 15.0  # s; a last window shorter than this joins the previous one (the key_sections rule)
GD_FRAME_HOP, GD_PATCH_FRAMES, GD_PATCH_HOP = 256, 128, 62  # TensorflowPredictEffnetDiscogs at 16 kHz (defaults)
GENRE_NOTE = ("model estimate; scores are mean per-patch sigmoid activations (multi-label, not probabilities); "
              "Discogs labels are release-level, so windows are less reliable than the whole song")
ENERGY_UNIT = "dBFS"
ENERGY_STEP = 1.0  # s; one RMS value per non-overlapping 1 s window of the 16 kHz source
ENERGY_FLOOR = -120.0  # dB; digital silence and anything quieter
ENERGY_MIN_TAIL = 0.5  # s; a last partial window shorter than this is dropped
ENERGY_WINDOW = 8  # s (values) per summary window: ~4 bars at 120 BPM, one phrase
ENERGY_PEAKS = 3
SILENCE_DB = -60.0  # dBFS; an energy window below this counts as silence (sections, energy_summary)
SILENCE_LABEL = "silence"
SEC_KERNEL = 16  # beats per checkerboard half (~4 bars of 4/4): a 4-bar loop sits inside one half
SEC_MIN = 8  # beats; minimum section length and peak-picking neighbourhood (~2 bars)
SEC_NOVELTY_MIN = 0.15  # absolute Foote novelty (-1..1 scale) a boundary needs
SEC_GROUP_MIN = 0.75  # segment similarity (0..1) needed to join an existing label group
SEC_MAX = 40
MEL_FRAME, MEL_HOP = 1024, 128  # at 16 kHz: 64 ms window, 8 ms hop (see docstring)
MEL_MAX_FREQ = 8000.0  # Nyquist of the 16 kHz source
MELODY_MAX_NOTES = 6000  # 600 s / PitchContourSegmentation's 0.1 s minimum note: an upper bound for valid output
MEL_PHRASE_GAP = 1.0  # s; consecutive notes further apart than this are a phrase break, not an interval
MEL_DIR_MIN = 2.0  # semitones of fitted change a section needs to be rising/falling
MEL_DIR_NOTES = 4  # fewer notes in a section: direction null
MEL_SMOOTH = 25  # pitch frames (0.2 s at hop 8 ms) of the contour median filter, odd
MERGE_GAP = 0.030  # s; notes this close count as touching (merge rules)
MERGE_SHORT = 0.2  # s; only pieces shorter than this join an oscillation (5-6 Hz vibrato halves are ~0.1 s)
MERGE_CENTRE = 0.5  # s; in a 2-semitone oscillation the middle pitch may last this long (the rounded pitch sits
# there for most of each +-100 cent cycle, and same-pitch pieces join before the oscillation join)
MERGE_MAX_RUN = 400  # pieces per oscillation window (bounds the work; ~40 s of 0.1 s pieces)
MERGE_SPAN = 2  # semitones; widest oscillation joined (+-100 cents vibrato reads as -1..+1)
SCOOP_MAX = 0.12  # s; a vocal note this short that rises 1-2 semitones into a note twice as long is a scoop (0.20.0)
SCOOP_STEPS = (1, 2)  # semitones up into the target note
MERGE_REVERSALS = 2  # sign changes an oscillation needs: A-B-A-B = 3 alternating moves = 2 sign changes
# Vocal melody (0.6.0, optional): Demucs on the full-band upload, then RMVPE on the vocal, instead of Melodia on the mix.
# Mix0 mean against Melodia (RPA / false voicing, 2026-11-03): 4 synthetic songs 84.5/27.9 vs 79.6/47.5, 40 human clips
# 95.8/37.1 vs 54.2/45.8. With 16 kHz input to Demucs RPA falls to 53.9, so band-limited audio keeps Melodia.
# 44.1 kHz mono files had -20.4..-27.6 dB above 8.2 kHz; 16 kHz resampled up had -50.6 dB.
# Batch 1 keeps Demucs at 8.6 GB for 600 s (34.1 GB with the automatic batch); the seed makes it repeatable.
SEP_RATE = 44100
SEP_BAND_HZ, SEP_BAND_MIN_DB = 8200.0, -40.0
# One Demucs run: the vocal, bass, drum, other and section stems come from one Demucs run on full.wav (MONO_DEMUCS_ARGS). Each
# htdemucs_ft stem is its own model, so the four files are byte-identical to four --stem runs; one run skips three weight
# loads (50 s: 2.5 s instead of 3.3 s). Its peak footprint is higher (600 s: 14.4 GB instead of 8.6 GB), about the stereo
# layer's own all-stems run, so the run's peak stays. When the run fails, every stem stage is skipped.
MONO_DEMUCS_ARGS = ("-n", "htdemucs_ft", "-b", "1", "--seed", "0")
MONO_STEMS = ("vocals", "bass", "drums", "other")
DEMUCS_WEIGHTS = "~/.cache/demucs-mlx/htdemucs_ft.safetensors"  # where demucs-mlx looks; it has no path option
DEMUCS_WEIGHTS_SHA256 = "53f03b1ad4b4d211025a35da65460ba61a17547adf9c0544cad0ebcc8d7bbabb"
DEMUCS_WEIGHTS_MAX_BYTES = 1024 * 1024 * 1024
RMVPE_SHA256 = "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193"
RMVPE_MAX_BYTES = 256 * 1024 * 1024
RMVPE_CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rmvpe_model.py")
RMVPE_CODE_SHA256 = "b948ecc0449018dc0ae6c12d584d5af59acbb5a8b477b95b5898a751c2cce325"
RMVPE_CODE_MAX_BYTES = 64 * 1024
RMVPE_HOP_S = 0.01
BTC_SHA256 = "1673d23f8f9a55ae7f9e8b80a51da616debb22675b8d8b67ea6ce0ef37b0ab51"
BTC_MAX_BYTES = 64 * 1024 * 1024
BTC_CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "btc_model.py")
BTC_CODE_SHA256 = "f446ab618b8df691c0ab8566d4060558b401b6e21a99c19943daba51ac2ca16a"
BTC_CODE_MAX_BYTES = 64 * 1024
BTC_MAX_FRAMES = 8000  # 600 s at 10.8 frames per second, with room to spare
CHORD_VOCABULARY = "BTC large vocabulary: 12 roots with m, maj, dim, aug, 6, m6, 7, maj7, m7, mMaj7, dim7, m7b5, sus2, sus4; N none, X unknown"
# Bass notes (0.8.0, optional): Demucs bass stem, RMVPE for the pitch, Basic Pitch note starts to split repeated notes.
# Settings and the checks behind them come from listening tests; note ends are weak, so reliability stays low_reference.
# 0.25.0: RMVPE labels any voiced low energy, so Demucs's kick and tom ring in the bass stem came out as notes. A note is kept
# only when a Basic Pitch note (down to 80 ms) within BASS_CONFIRM_SEMIS overlaps it (synthetic drums, bass and
# bass-on-kick clips and one drum-only song; numbers in the PR, no real bass clip to check against).
BASS_ONSETS_CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bass_onsets.py")
BASS_ONSETS_SHA256 = "6a8842bc6bb14b111959020f7354b183c598e247a87ee6b825f61b3c359932de"
BASS_ONSETS_MAX_BYTES = 16 * 1024
BASS_MIN_FRAMES, BASS_JOIN_FRAMES = 8, 20  # at RMVPE's 10 ms: shortest note 80 ms, same pitch joins across 200 ms
BASS_CONFIRM_SEMIS = 1  # Basic Pitch and RMVPE may round a detuned note to neighbouring semitones
# 0.26.0, after the check: RMVPE's pitch splits one plucked note into same-pitch pieces and starts late on hard attacks.
# Same-pitch rows split by up to BASS_JOIN_GAP_S join unless a Basic Pitch note (within BASS_CONFIRM_SEMIS) starts within
# BASS_JOIN_BLOCK_S of the second; a row's start moves back to the latest such Basic Pitch start up to BASS_SNAP_S before
# it, unless one starts within BASS_JOIN_BLOCK_S after it (never before the row ahead ends; the earliest, or a latest that
# skips a start just after the row, picked up short early fragments). Both values were picked on half of a real bass set and checked on the other half.
BASS_JOIN_GAP_S, BASS_JOIN_BLOCK_S, BASS_SNAP_S = 0.05, 0.03, 0.25
BASS_MAX_NOTES = 7500  # 600 s / the 80 ms shortest note
BASS_METHOD = ("Demucs htdemucs_ft bass, RMVPE pitch on CPU, split at Basic Pitch starts, kept where a Basic Pitch note "
               "is within a semitone, pieces joined, starts at Basic Pitch's. Sounding pitch; ends are weak.")
BASS_FAIL_REASONS = ("models_invalid", "decode_failed", "separation_failed", "rmvpe_failed", "onsets_failed")
# Drum hits (0.9.0, optional): Demucs drum stem, then ADTOF Frame_RNN. Measured on 23 songs of MDB Drums; tom is weak.
ADTOF_CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adtof_model.py")
ADTOF_CODE_SHA256 = "8362b7295c75719f7d4dd269701113aff1b5165fa9a14c778bd1d96c6653e73c"
ADTOF_CODE_MAX_BYTES = 64 * 1024
ADTOF_INDEX_SHA256 = "3fe35e421c448c395984168cd3aa1f979def902df784b6766291e55bb9aa069f"
ADTOF_DATA_SHA256 = "ff611129cb4a4d30722a54251e893341a58a016a40c419ab1e3786fc77a04779"
ADTOF_INDEX_MAX_BYTES, ADTOF_DATA_MAX_BYTES = 64 * 1024, 16 * 1024 * 1024
DRUM_CLASSES = ("kick", "snare", "tom", "hihat", "cymbal")
DRUM_MAX_HITS = 100000  # 5 classes, one hit per 30 ms over 600 s
DRUM_METHOD = ("Demucs htdemucs_ft drums at 44.1 kHz, then ADTOF Frame_RNN on CPU: kick, snare, tom, hihat, cymbal hit "
               "times. Tom is weak and quiet snare hits are often missed.")
DRUM_FAIL_REASONS = ("models_invalid", "decode_failed", "separation_failed", "adtof_failed")
# Sections from All-In-One (0.11.0, optional): functional segments on the four Demucs stems, run in their own environment.
# See "Sections from All-In-One" in README.md. Audio without stems keeps the beat method.
ALLIN1_CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "allin1_sections.py")
ALLIN1_CODE_SHA256 = "9aedd5ca7e7a8aae957fe646fde9b0d74daa33099c306a4afec75dc511d738d0"
ALLIN1_CODE_MAX_BYTES = 16 * 1024
ALLIN1_WEIGHTS = (("harmonix-fold0-0vra4ys2.pth", "0db596dfb0995f41d62f6267d76a9d54c046f1649bd35e1dbeca0c5f9a7b8acd"),
                  ("harmonix-fold1-3ozjhtsj.pth", "607b4864eaabccd4f6951410d5ea6676666ad1cfa6ce2e18bc99f3824b77c3b7"),
                  ("harmonix-fold2-gmgo0nsy.pth", "67c28ec7ca612d365e5fcb152f6530eb53e34c4303940afb30fd17a295c958a4"),
                  ("harmonix-fold3-i92b7m8p.pth", "446d05672b2669cdb7d9b8bbbc228a4f3592484335a7995cca89a744e3950ddc"),
                  ("harmonix-fold4-1bql5qo0.pth", "96327222ab9b33c005388b788ba602f529aa03cc4dde1c859c011f1004490493"),
                  ("harmonix-fold5-x4z5zeef.pth", "c87f162a1090290bcbb863688746784919cf912c56744fb3d81a1a7dba3b114e"),
                  ("harmonix-fold6-x7t226rq.pth", "13e7e639bf30fcd3fa049ad863088773a4e57b06d7c68581385b3ef625c60688"),
                  ("harmonix-fold7-qwwskhg6.pth", "b888b7fba95efa3d5a37ca013fe7efe2741621ea9ca335e6b2723e4fd0fade57"))
ALLIN1_WEIGHT_MAX_BYTES = 8 * 1024 * 1024
SEC_FUNCTIONS = ("start", "end", "intro", "outro", "break", "bridge", "inst", "solo", "verse", "chorus")
SEC_MIN_SEGMENT = 0.05  # s; the model can give empty or near-empty segments at the very start and end
SEC_MODEL_FAIL_REASONS = ("models_invalid", "decode_failed", "separation_failed", "sections_failed", "too_many_sections")
SEC_METHOD_ALLIN1 = ("All-In-One 8-fold ensemble on Demucs htdemucs_ft stems at 44.1 kHz; letters group equal functions "
                     "(verse parts are not told apart); similarity as in the beat method")
# Accompaniment notes (0.12.0, with the bass layer's Basic Pitch): the Demucs other stem as one group of notes.
# Part activity (0.13.0): which of vocals, bass and drums is sounding in each section, from the stems already made.
PART_STEMS = ("vocals", "bass", "drums")
PART_WINDOW_S = 2.0
PART_THRESHOLD_DB = -15  # stem window RMS against the whole mix's RMS; chosen on RWC-P 1-50
PART_METHOD = ("Demucs htdemucs_ft stems: share of 2 s windows whose RMS is within 15 dB of the mix's RMS. "
               "Quiet parts can read as off. Note and hit rates come from the note layers.")
# Mix and stereo (0.17.0): the upload decoded to stereo, and a second Demucs run on it; see "Mix and stereo" in README.md.
STEREO_ARGS = ("-n", "htdemucs_ft", "-b", "1", "--seed", "0")
STEREO_PARTS = ("vocals", "bass", "drums", "other")
STEREO_WINDOW_S = 1.0
STEREO_BANDS = (("low_width_db", 0.0, 200.0), ("mid_width_db", 200.0, 2000.0), ("high_width_db", 2000.0, None))
STEREO_WIDTH_DB = 40.0  # width is side energy over mid energy, kept within +-40 dB
STEREO_PART_DB = -15.0  # a part under this against the section's mix has no position
TONE_PARTS = ("vocals", "drums", "other")  # bass brightness and every roughness value failed evaluation
TONE_WIN, TONE_HOP, TONE_CHUNK = 2048, 1024, 256
TONE_GATE = 1e-3  # frames within 30 dB of the section's loudest
TONE_LOW_HZ, TONE_HIGH_HZ = 20.0, 16000.0
TONE_KICK_HZ, TONE_HIT_S, TONE_MIN_HITS = 120.0, 0.12, 5
TONE_METHOD = ("Spectral centroid per section of the Demucs htdemucs_ft stems of the stereo decode, summed to mono; "
               "kick and snare from 120 ms after each ADTOF hit.")
MIX_METHOD = ("Stereo decode. balance = (R-L)/(R+L) of energy; width = side over mid energy in dB; low under 200 Hz, high over "
              "2 kHz; loudness by Essentia LoudnessEBUR128.")
PART_POSITION_METHOD = ("Demucs htdemucs_ft on the stereo decode: balance, width and level against the mix per section. "
                        "Stem bleed moves these values.")
# Musical units (0.14.0): existing results grouped by the section model's downbeats; see "Musical units" in README.md.
BAR_MAX = 2000
BAR_STEPS = {3: 12, 4: 16}  # steps per bar by beats per bar; 16 when the count is unknown
# Beats per bar (0.22.0): the median count of the model's beats in a bar, kept only when it is 3 or 4. Ballroom (2026-11-07,
# frozen bars): 154 of 175 3/4 excerpts read 3, 0 of 77 4/4 excerpts read 3. 6/8 is not tested.
DOWNBEAT_METHOD = "All-In-One 8-fold ensemble on Demucs htdemucs_ft stems. A wrong downbeat shifts every bar built on it."
BAR_METHOD = ("Between downbeats, then median-length bars to the song end: longest chord; kick and snare on equal steps "
              "(12 if 3 beats per bar, else 16); scale, degree from chord tones; split = each half's chord.")
# Two chords in a bar (0.18.0): each half's chord must hold this share of its half. Evaluated at this value.
BAR_SPLIT_SHARE = 0.6
# Swing (0.16.0): where hi-hat and cymbal hits fall between two beats. Evaluated at these values.
SWING_METHOD = ("Median position inside the beat, 0 to 1, of hi-hat and cymbal hits between 0.4 and 0.8, a beat being a third "
                "of a bar in 3-beat songs, else a quarter: about 0.5 straight, 0.67 triplet swing.")
SWING_OFF_BEAT = (0.4, 0.8)
SWING_MIN_HITS = 30
SWING_AT = 0.58
# Scale and degree (0.15.0): best path over the 12 major scales, relative minors merged. Evaluated at this penalty.
SCALE_PENALTY = 4.0
SCALE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
DEGREE_NAMES = ("1", "b2", "2", "b3", "3", "4", "b5", "5", "b6", "6", "b7", "7")
MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
NOTE_PCS = {"C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4, "F": 5, "E#": 5, "F#": 6, "Gb": 6,
            "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11, "Cb": 11}
OTHER_NOTES_CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "other_notes.py")
OTHER_NOTES_SHA256 = "53de95ae2b1abd6c793a91241a81cfd9836d021ff4f2847feb4edf821494e37c"
OTHER_NOTES_MAX_BYTES = 16 * 1024
OTHER_MAX_NOTES = 24000  # 40 notes per second over 600 s; the densest of 100 pop songs had 14.7
OTHER_METHOD = ("Demucs htdemucs_ft other stem at 44.1 kHz, Basic Pitch notes at onset 0.7 and frame 0.2: all instruments "
                "but vocals, bass and drums, unnamed. Rough on exact pitch and note starts.")
OTHER_FAIL_REASONS = ("models_invalid", "decode_failed", "separation_failed", "notes_failed")
# Chord display (0.10.0): measured on RWC-P with BTC chords and the bass layer; see "Chord display" in README.md.
SLASH_MIN_SHARE = 0.5
SHARP_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
FLAT_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
NATURAL_NAMES = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")  # C major, A minor: the usual borrowed chords
FLAT_KEY_MAJORS = frozenset((1, 3, 5, 8, 10))  # relative major pitch classes written with flats
CHORD_TONES = {"": (4, 7), "m": (3, 7), "dim": (3, 6), "aug": (4, 8), "m6": (3, 7, 9), "6": (4, 7, 9), "m7": (3, 7, 10),
               "mMaj7": (3, 7, 11), "maj7": (4, 7, 11), "7": (4, 7, 10), "dim7": (3, 6, 9), "m7b5": (3, 6, 10),
               "sus2": (2, 7), "sus4": (5, 7)}
SEP_RETRY_REASONS = ("models_invalid", "decode_failed", "separation_failed", "rmvpe_failed")
SEP_FLAGS = {"--demucs": "demucs", "--rmvpe-model": "rmvpe", "--melody-scratch": "scratch", "--melody-source": "source",
             "--basic-pitch-python": "basic_pitch", "--drum-model": "drum_model", "--singing-python": "singing_python",
             "--sections-python": "sections_python", "--sections-model": "sections_model"}
MEL_METHOD_VOCAL = ("Demucs htdemucs_ft vocal at 44.1 kHz, then RMVPE threshold 0.03 on CPU in 30 s pieces; "
                    "same note segmentation and merge as the mix method, then scoop join. The vocal stem can include backing vocals.")
# Sung notes (0.21.0, optional): YourMT3+ on the same Demucs vocal, program 100 (singing voice). RWC-P songs 76-100 against
# MIDI vocal notes (Goal V, bars fixed before scoring): pitch F 30.5 -> 54.2, rhythm F 61.0 -> 75.7; songs 51-75 38.0 -> 60.6
# and 63.3 -> 80.1. About 80 s and 14.3 GB for a 600 s vocal on CPU, so it runs last, inside what is left of the server's
# 300 s, and RMVPE notes stay the fallback.
SING_DIR = os.path.expanduser("~/tools/music-lens/models/yourmt3")
SING_CKPT = "amt/logs/2024/mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/checkpoints/last.ckpt"
SING_CKPT_SHA256 = "bd7b9b6e609c05478eff4b9f5c574e6635fd6e6b306036eed1cb1466e775e5c4"  # weights-only copy (state_dict)
SING_CKPT_MAX_BYTES = 512 * 1024 * 1024
SING_CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sing_notes.py")
SING_CODE_SHA256 = "82806f9225a98e3041eb689d6e8a09169ad1414fe2a2eb272fa3030e4f55c505"
SING_CODE_MAX_BYTES = 16 * 1024
SING_BUDGET = 300.0  # s; the server's default rhythm_timeout, used when --time-budget is absent
SING_RESERVE = 10.0  # s kept for the steps after it (600 s file: the worker ended under 1 s after this step) and the server
SING_MIN = 20.0  # s; with less left the model is not started
SING_MAX = 150.0  # s; the most it may take (600 s vocal: about 80 s)
SING_FAIL_REASONS = ("models_invalid", "notes_failed", "timeout", "no_time", "too_many_notes", "no_notes")
MEL_METHOD_SING = ("Demucs htdemucs_ft vocal at 44.1 kHz, then YourMT3+ (mc13 MoE, CPU) notes on program 100, singing voice; "
                   "no merge. The vocal stem can include backing vocals.")
MEL_METHOD = ("EqualLoudness+PredominantPitchMelodia 16 kHz frame 1024 hop 128 (predominant pitch of the full mix); "
              "0.2 s median f0 (cents); PitchContourSegmentation notes, midi rounded; vibrato/same-pitch merge")
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
PITCH_CLASS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7,
               "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
SEC_DEBUG_PEAKS = 300  # novelty peaks kept in structure_debug (4000 beats / 9-beat spacing could give ~440)
WORDS_MAX_BYTES = 8 * 1024 * 1024  # stdin cap for --words-stdin
LYRIC_MAX_WORDS = 20000
LYRIC_MAX_TEXT = 200  # characters per word text
LYRIC_N_CHAR = 8  # characters per n-gram (char-level ASR)
LYRIC_N_WORD = 4  # words per n-gram (space-delimited ASR)
LYRIC_SPREAD = 15.0  # s; an n-gram counts as repeated when its occurrence starts span at least this
LYRIC_JOIN = 3.0  # s; covered units closer than this join one block
LYRIC_MIN_BLOCK = 4.0  # s; shorter blocks are dropped
LYRIC_MIN_CHARS = 50  # normalized characters below which lyric_repeats is unavailable
LYRIC_MAX_BLOCKS = 40
LYRIC_APPROX_W = 10  # chars; char units also match windows this long within LYRIC_APPROX_K edits (0.4.4)
LYRIC_APPROX_K = 2
LYRIC_APPROX_SEED = 4  # chars; a window pair is checked only when both contain the same 4-char seed
LYRIC_APPROX_SEED_MAX = 64  # seeds occurring more often than this are skipped (filler such as "lalala")
LYRIC_APPROX_MAX_CHECKS = 200000  # window-pair edit-distance checks per song; beyond: warning, matching stops
RE_REASON = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")  # the server's code pattern
LYRIC_METHOD = ("NFKC lowercase, letters/digits/marks, katakana as hiragana; char 8-gram or 10-char window within "
                "2 edits (word 4-gram) repeats >= 15 s apart; joined within 3 s; blocks >= 4 s; groups share a repeat")
LYRIC_NOTE = "repeated ASR text, likely chorus/hook; not verified, depends on ASR quality"
SEC_METHOD = ("beat-sync HPCP12+MFCC2-13(z) cosine SSM; Foote checkerboard 2x16 beats (full kernel only), "
              "novelty>=0.15, min 8 beats; groups: segment-mean similarity>=0.75 to the group's first segment")


class Fail(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def fail_out(code, message):
    sys.stderr.write(json.dumps({"error": {"code": code, "message": message[:500]}}) + "\n")
    sys.stderr.flush()
    os._exit(1)


def on_alarm(signum, frame):
    fail_out("timeout", "rhythm worker exceeded its own wall bound")


def r3(x):
    return round(float(x), 3)


def finite(x):
    x = float(x)
    if not math.isfinite(x):
        raise Fail("analysis_failed", "non-finite value from the analyzer")
    return x


def bpm_alternatives(bpm):
    if not bpm > 0:
        return []
    return [a for a in (round(bpm / 2.0, 2), round(bpm * 2.0, 2)) if ALT_MIN_BPM <= a <= ALT_MAX_BPM]


def key_windows(duration, size=KEY_WINDOW, min_tail=KEY_MIN_TAIL):
    """[(start, end)] of `size` s covering [0, duration]; a last window under `min_tail` joins the previous."""
    out, t = [], 0.0
    while t < duration:
        out.append([t, min(t + size, duration)])
        t += size
    if len(out) > 1 and out[-1][1] - out[-1][0] < min_tail:
        last = out.pop()
        out[-1][1] = last[1]
    return [tuple(w) for w in out]


def merge_key_sections(rows):
    """rows: [(start, end, tonic, scale, strength|None)] in time order -> merged section dicts."""
    out = []
    for start, end, tonic, scale, s in rows:
        if out and (out[-1]["tonic"], out[-1]["scale"]) == (tonic, scale):
            out[-1]["end"] = end
            out[-1]["_s"].append(s)
            continue
        out.append({"start": start, "end": end, "tonic": tonic, "scale": scale, "_s": [s]})
    for r in out:
        ss = r.pop("_s")
        r["strength"] = None if any(x is None for x in ss) else round(sum(ss) / len(ss), 3)
        r["start"], r["end"] = r3(r["start"]), r3(r["end"])
    return out


def merge_frames(frames, duration):
    """frames: [(time, label, strength|None)] -> segments of consecutive identical labels (strength mean, null-sticky)."""
    segs = []
    for t, lab, s in frames:
        if segs and segs[-1]["label"] == lab:
            segs[-1]["_s"].append(s)
            continue
        if segs:
            segs[-1]["end"] = t
        segs.append({"start": t, "end": duration, "label": lab, "_s": [s]})
    return _finish(segs)


def _finish(segs):
    out = []
    for c in segs:
        ss = c.pop("_s")
        c["strength"] = None if any(x is None for x in ss) else round(sum(ss) / len(ss), 3)
        c["start"], c["end"] = r3(c["start"]), r3(c["end"])
        if c["end"] > c["start"]:
            out.append(c)
    return out


def beat_chords(frames, beats, duration):
    """Beat-synchronous chords. Intervals: [0, b0), [b0, b1), ..., [b_last, duration]. Per interval the label with the
    largest summed strength among its frames wins (null counts 0; ties: more frames, then earliest frame); an interval
    holding no frame takes the frame nearest its midpoint. Identical neighbours then merge. The segment strength is
    the mean of the winning label's frame strengths in it (null if any of them is null: never a measured 0.0)."""
    if not frames:
        return []
    edges = [0.0]
    for b in beats:
        b = min(float(b), duration)
        if b > edges[-1]:
            edges.append(b)
    if duration > edges[-1]:
        edges.append(duration)
    times = [f[0] for f in frames]
    segs, i, n = [], 0, len(frames)
    for j in range(len(edges) - 1):
        a, b = edges[j], edges[j + 1]
        last = j == len(edges) - 2
        while i < n and times[i] < a:
            i += 1
        k = i
        while k < n and (times[k] < b or (last and times[k] <= b)):
            k += 1
        group = frames[i:k]
        if not group:
            mid = (a + b) / 2.0
            group = [min(frames, key=lambda f: abs(f[0] - mid))]
        votes = {}
        for order, (_, lab, s) in enumerate(group):
            v = votes.setdefault(lab, [0.0, 0, -order, []])
            v[0] += 0.0 if s is None else s
            v[1] += 1
            v[3].append(s)
        lab, (_, _, _, ss) = max(votes.items(), key=lambda kv: tuple(kv[1][:3]))
        if segs and segs[-1]["label"] == lab:
            segs[-1]["end"] = b
            segs[-1]["_s"].extend(ss)
        else:
            segs.append({"start": a, "end": b, "label": lab, "_s": ss})
    return _finish(segs)


def _pool(env, hop_s, t, half):
    """Max of env over frames whose centre lies within t +- half (frame i centred at i*hop_s); 0.0 when none."""
    a = max(0, int(math.ceil((t - half) / hop_s - 1e-9)))
    b = min(len(env) - 1, int(math.floor((t + half) / hop_s + 1e-9)))
    return max(env[a:b + 1]) if b >= a else 0.0


def double_tempo_evidence(env, hop_s, beats, half=DTE_WINDOW):
    """env: onset strength per frame (0..1, frame i centred at i*hop_s). Returns (evidence dict, warning|None).
    pairs = consecutive beat pairs with gap in DTE_GAP. mid_to_beat_ratio = median(midpoint onset) / median(beat
    onset over both flanking beats of every pair); mid_strong_fraction = share of pairs whose midpoint onset is
    >= DTE_STRONG x the mean of its two flanking beat onsets (and > 0). Null ratio/fraction (+ warning) when pairs <
    DTE_MIN_PAIRS or the beat median < DTE_MIN_BEAT_MEDIAN."""
    beat_v, mid_v, strong = [], [], 0
    for b1, b2 in zip(beats, beats[1:]):
        if not DTE_GAP[0] <= b2 - b1 <= DTE_GAP[1]:
            continue
        o1, o2 = _pool(env, hop_s, b1, half), _pool(env, hop_s, b2, half)
        om = _pool(env, hop_s, (b1 + b2) / 2.0, half)
        beat_v += [o1, o2]
        mid_v.append(om)
        if om > 0 and om >= DTE_STRONG * (o1 + o2) / 2.0:
            strong += 1
    pairs = len(mid_v)
    bmed = statistics.median(beat_v) if beat_v else None
    mmed = statistics.median(mid_v) if mid_v else None
    ratio = frac = warn = None
    if pairs < DTE_MIN_PAIRS:
        warn = "double_tempo_evidence_unavailable: fewer than %d usable beat pairs" % DTE_MIN_PAIRS
    elif bmed < DTE_MIN_BEAT_MEDIAN:
        warn = "double_tempo_evidence_unavailable: onset strength at beats is ~0"
    else:
        ratio, frac = round(mmed / bmed, 3), round(strong / float(pairs), 3)
    rnd = lambda x: None if x is None else round(min(1.0, max(0.0, x)), 4)
    return ({"method": ONSET_METHOD, "window_ms": round(half * 1000.0, 1), "beat_onset_median": rnd(bmed),
             "mid_onset_median": rnd(mmed), "mid_to_beat_ratio": ratio, "mid_strong_fraction": frac,
             "mid_strong_threshold": DTE_STRONG, "pairs": pairs}, warn)


def verify_model(path, expected=TEMPO_CNN_SHA256, cap=TEMPO_CNN_MAX_BYTES):
    """None when path is a regular file (not a symlink, not a FIFO), <= cap bytes, whose SHA-256 is `expected`;
    otherwise a short reason code."""
    return read_pinned(path, expected, cap, keep=False)[0]


def read_pinned(path, expected, cap, keep=True):
    """(reason, data): reason None when path passes verify_model's checks; data = the hashed bytes when keep (so a
    small metadata file is parsed from exactly the bytes that matched the pin), else None."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (FileNotFoundError, NotADirectoryError):
        return "model_missing", None
    except OSError as e:
        return ("not_regular_file" if e.errno in (errno.ELOOP, errno.EISDIR) else "model_unreadable"), None
    h, parts = hashlib.sha256(), []
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return "not_regular_file", None
        with os.fdopen(fd, "rb") as f:
            if st.st_size > cap:
                return "model_too_large", None
            n = 0
            for chunk in iter(lambda: f.read(65536), b""):
                n += len(chunk)
                if n > cap:
                    return "model_too_large", None
                h.update(chunk)
                if keep:
                    parts.append(chunk)
    except OSError:
        return "model_unreadable", None
    if h.hexdigest() != expected:
        return "sha256_mismatch", None
    return None, (b"".join(parts) if keep else None)


def tempo_cnn_summary(preds, patch_silent, sha256=TEMPO_CNN_SHA256):
    """preds: TensorflowPredictTempoCNN softmax rows (lists of TEMPO_CNN_CLASSES floats); patch_silent: one bool per
    row (True = excluded as near-silent). Local BPM = 30 + argmax per audible patch; bpm =
    the most frequent local BPM (ties: higher mean probability, then lower BPM); top = mean softmax of the audible
    patches. Plain lists in, the tempo_cnn dict out."""
    if not preds:
        return {"status": "failed", "code": "too_short"}
    if not all(math.isfinite(float(x)) for row in preds for x in row):
        return {"status": "failed", "code": "non_finite"}
    for i, row in enumerate(preds):
        if len(row) != TEMPO_CNN_CLASSES:
            return {"status": "failed", "code": "inconsistent_output",
                    "detail": {"code": "class_count_mismatch", "row": i, "classes": len(row)}}
    rows = [row for row, quiet in zip(preds, patch_silent) if not quiet]
    silent = len(preds) - len(rows)
    if not rows:
        return {"status": "failed", "code": "no_audible_patches"}
    k = float(len(rows))
    mean = [sum(col) / k for col in zip(*rows)]
    local = [max(range(len(row)), key=lambda i, r=row: (r[i], -i)) for row in rows]  # argmax, ties: lower class
    votes = {}
    for c in local:
        votes[c] = votes.get(c, 0) + 1
    best = max(votes, key=lambda c: (votes[c], mean[c], -c))
    top = sorted(range(len(mean)), key=lambda i: (-mean[i], i))[:TEMPO_CNN_TOP]
    loc = [float(TEMPO_CNN_MIN_BPM + c) for c in local]
    return {"status": "done", "model": TEMPO_CNN_NAME, "model_sha256": sha256, "bpm": float(TEMPO_CNN_MIN_BPM + best),
            "local_bpm_median": round(statistics.median(loc), 2), "local_bpm_min": min(loc),
            "local_bpm_max": max(loc), "local_count": len(rows), "silent_patches": silent,
            "top": [{"bpm": float(TEMPO_CNN_MIN_BPM + i), "prob": round(min(1.0, max(0.0, mean[i])), 3)}
                    for i in top],
            "note": TEMPO_CNN_NOTE}


def patch_span(i):
    """(start, end) in s of TempoCNN patch i (see docstring)."""
    a = i * TEMPO_CNN_PATCH_HOP * TC_FRAME_HOP / float(TEMPO_CNN_RATE)
    return a, a + ((TC_PATCH_FRAMES - 1) * TC_FRAME_HOP + TC_FRAME) / float(TEMPO_CNN_RATE)


def tempo_cnn(x16, model, energy):
    """tempo_cnn dict for the 16 kHz source; never raises."""
    reason = verify_model(model)
    if reason:
        return {"status": "unavailable", "reason": reason}
    try:
        import essentia.standard as es
        import numpy as np
        a = es.Resample(inputSampleRate=SRC_RATE, outputSampleRate=TEMPO_CNN_RATE, quality=1)(x16)
        a = np.ascontiguousarray(a, dtype=np.float32)
        preds = np.asarray(es.TensorflowPredictTempoCNN(graphFilename=model, batchSize=TEMPO_CNN_BATCH,
                                                        patchHopSize=TEMPO_CNN_PATCH_HOP,
                                                        lastPatchMode=TEMPO_CNN_LAST_PATCH)(a), dtype=np.float64)
        rows = preds.tolist() if preds.size else []
        return tempo_cnn_summary(rows, [section_silent(energy, *patch_span(i)) for i in range(len(rows))])
    except Exception as e:  # missing TensorFlow algorithms, graph load or inference error
        return {"status": "failed", "code": "dependency_missing" if isinstance(e, (ImportError, AttributeError))
                else "inference_error"}


def genre_labels(data):
    """The head json's 400 class names ("Parent---Style"), or None when the (pinned) file does not hold them."""
    try:
        c = json.loads(data.decode("utf-8")).get("classes")
    except (ValueError, AttributeError):
        return None
    if not (isinstance(c, list) and len(c) == GENRE_CLASSES
            and all(isinstance(x, str) and 0 < len(x) <= 64 and x.count(GENRE_SEP) == 1
                    and all(x.split(GENRE_SEP)) for x in c)):
        return None
    return c


def genre_patch_span(i):
    """(first, last) frame centre in s of effnet patch i: frames hop 256 at 16 kHz (16 ms), 128 frames per patch,
    patch hop 62 frames (0.992 s); measured: the first patch exists from ~2.04 s of audio, 603 patches for 600 s."""
    a = i * GD_PATCH_HOP * GD_FRAME_HOP / float(SRC_RATE)
    return a, a + (GD_PATCH_FRAMES - 1) * GD_FRAME_HOP / float(SRC_RATE)


def _genre_top(mean, labels, k):
    idx = sorted(range(len(mean)), key=lambda i: (-mean[i], i))[:k]  # ties: lower class index
    return [{"style": labels[i], "score": round(min(1.0, max(0.0, mean[i])), 3)} for i in idx]


def genre_summary(preds, patch_silent, labels, duration):
    """preds: per-patch sigmoid rows (lists of GENRE_CLASSES floats); patch_silent: one bool per row (True = near
    silent, excluded). Song scores = mean of the audible rows; parent score = max over its styles; windows: fixed
    GENRE_WINDOW s (key_windows rule), each patch in the window holding its centre. Plain lists in, the genre dict
    out."""
    if not preds:
        return {"status": "unavailable", "reason": "too_short"}
    if not all(math.isfinite(float(x)) for row in preds for x in row):
        return {"status": "failed", "code": "non_finite"}
    if any(len(row) != GENRE_CLASSES for row in preds):
        return {"status": "failed", "code": "class_count_mismatch"}
    audible = [i for i, quiet in enumerate(patch_silent) if not quiet]
    if not audible:
        return {"status": "unavailable", "reason": "no_audible_patches"}

    def mean_of(ix):
        return [sum(col) / float(len(ix)) for col in zip(*(preds[i] for i in ix))]

    mean = mean_of(audible)
    parents = {}
    for i, name in enumerate(labels):
        p = name.split(GENRE_SEP)[0]
        parents[p] = max(parents.get(p, 0.0), mean[i])
    wins = key_windows(duration, GENRE_WINDOW, GENRE_MIN_TAIL)
    members = [[] for _ in wins]
    for i in audible:
        c = sum(genre_patch_span(i)) / 2.0
        k = next((k for k, (ws, we) in enumerate(wins) if c < we), len(wins) - 1)
        members[k].append(i)
    windows = [{"start": r3(ws), "end": r3(we), "patches": len(ix),
                "top": _genre_top(mean_of(ix), labels, GENRE_WINDOW_TOP) if ix else []}
               for (ws, we), ix in zip(wins, members)]
    return {"status": "done", "model": GENRE_HEAD_NAME, "model_sha256": GENRE_HEAD_SHA256,
            "embedding_model": GENRE_EMBED_NAME, "embedding_sha256": GENRE_EMBED_SHA256,
            "reliability": "model_estimate", "patches": len(preds), "silent_patches": len(preds) - len(audible),
            "patch_hop_s": GD_PATCH_HOP * GD_FRAME_HOP / float(SRC_RATE), "window_s": GENRE_WINDOW,
            "top": _genre_top(mean, labels, GENRE_TOP),
            "parents": [{"parent": p, "score": round(min(1.0, max(0.0, s)), 3)}
                        for p, s in sorted(parents.items(), key=lambda kv: (-kv[1], kv[0]))[:GENRE_PARENTS]],
            "windows": windows, "note": GENRE_NOTE}


def genre(x16, duration, energy, model_dir=GENRE_DIR):
    """genre dict for the 16 kHz source (the models' inference rate); never raises."""
    paths = [os.path.join(model_dir, f) for f in (GENRE_EMBED_FILE, GENRE_HEAD_FILE, GENRE_LABELS_FILE)]
    for p, h in zip(paths[:2], (GENRE_EMBED_SHA256, GENRE_HEAD_SHA256)):
        reason = verify_model(p, h)
        if reason:
            return {"status": "unavailable", "reason": reason}
    reason, data = read_pinned(paths[2], GENRE_LABELS_SHA256, GENRE_LABELS_MAX_BYTES)
    if reason:
        return {"status": "unavailable", "reason": reason}
    labels = genre_labels(data)
    if labels is None:
        return {"status": "failed", "code": "class_count_mismatch"}
    try:
        import essentia.standard as es
        import numpy as np
        a = np.ascontiguousarray(x16, dtype=np.float32)
        emb = np.asarray(es.TensorflowPredictEffnetDiscogs(
            graphFilename=paths[0], output=GENRE_EMBED_OUTPUT, batchSize=64, patchSize=GD_PATCH_FRAMES,
            patchHopSize=GD_PATCH_HOP, lastPatchMode="discard")(a), dtype=np.float32)
        if emb.size == 0:
            rows = []
        else:
            preds = np.asarray(es.TensorflowPredict2D(graphFilename=paths[1], input=GENRE_HEAD_INPUT,
                                                      output=GENRE_HEAD_OUTPUT)(emb), dtype=np.float64)
            if preds.ndim != 2:
                return {"status": "failed", "code": "class_count_mismatch"}
            rows = preds.tolist()
        return genre_summary(rows, [section_silent(energy, *genre_patch_span(i)) for i in range(len(rows))],
                             labels, duration)
    except Exception as e:  # missing TensorFlow algorithms, graph load or inference error
        return {"status": "failed", "code": "dependency_missing" if isinstance(e, (ImportError, AttributeError))
                else "inference_error"}


def energy_values(x16, duration):
    """RMS level in dBFS per ENERGY_STEP window of the 16 kHz source ([i, i+1) s; a last partial window of at least
    ENERGY_MIN_TAIL s is kept, a shorter one dropped); floor ENERGY_FLOOR."""
    import numpy as np
    step = int(ENERGY_STEP * SRC_RATE)
    n = int(duration // ENERGY_STEP)
    if duration - n * ENERGY_STEP >= ENERGY_MIN_TAIL or n == 0:
        n += 1
    out = []
    for i in range(n):
        seg = np.asarray(x16[i * step:(i + 1) * step], dtype=np.float64)
        rms = float(np.sqrt(np.mean(seg * seg))) if len(seg) else 0.0
        db = 20.0 * math.log10(rms) if rms > 0 else ENERGY_FLOOR
        out.append(round(min(0.0, max(ENERGY_FLOOR, finite(db))), 2))
    return out


def _window_means(values, w):
    """[(start index, mean)] of every window of w consecutive values (one window of all when fewer)."""
    w = min(w, len(values))
    s = sum(values[:w])
    out = [(0, s / w)]
    for i in range(1, len(values) - w + 1):
        s += values[i + w - 1] - values[i - 1]
        out.append((i, s / w))
    return out, w


def energy_summary(values, duration, step=ENERGY_STEP, w=ENERGY_WINDOW, floor=SILENCE_DB):
    """min/max/median of the values, the ENERGY_PEAKS loudest non-overlapping windows of w values (greedy by mean,
    ties earliest) and the quietest window (ties earliest), both among windows holding no value below `floor` (no
    such window: peaks [] and quietest None). Window means are plain means of the dB values. silence_s = seconds of
    values below `floor`."""
    if not values:
        return None
    wins, w = _window_means(values, w)
    wins = [(i, m) for i, m in wins if min(values[i:i + w]) >= floor]
    silence_s = round(min(duration, sum(1 for x in values if x < floor) * step), 3)
    span = lambda i, m: {"start": r3(i * step), "end": r3(min((i + w) * step, duration)), "mean": round(m, 2)}
    peaks = []
    for i, m in sorted(wins, key=lambda x: (-x[1], x[0])):
        if all(abs(i - j) >= w for j, _ in peaks):
            peaks.append((i, m))
            if len(peaks) == ENERGY_PEAKS:
                break
    quiet = min(wins, key=lambda x: (x[1], x[0])) if wins else None
    return {"unit": ENERGY_UNIT, "window_s": w * step, "min": min(values), "max": max(values),
            "median": round(statistics.median(values), 2), "peaks": [span(i, m) for i, m in peaks],
            "quietest": span(*quiet) if quiet else None, "silence_db": floor, "silence_s": silence_s}


def novelty_peaks(nov, min_gap=SEC_MIN, thresh=None):
    """Indices i with nov[i] >= thresh (any value when thresh is None), >= min_gap from both ends (0 and len(nov)),
    that are the maximum of their +-min_gap neighbourhood (ties: the earliest)."""
    n = len(nov)
    cand = []
    for i, x in enumerate(nov):
        if x is None or (thresh is not None and x < thresh) or i < min_gap or n - i < min_gap:
            continue
        lo, hi = max(0, i - min_gap), min(n, i + min_gap + 1)
        if all(nov[j] is None or nov[j] < x for j in range(lo, i)) and \
                all(nov[j] is None or nov[j] <= x for j in range(i + 1, hi)):
            cand.append(i)
    return cand


def pick_boundaries(nov, min_gap=SEC_MIN, thresh=SEC_NOVELTY_MIN, cap=SEC_MAX - 1):
    """nov[i]: novelty at boundary index i (None where not computed). Candidates: nov >= thresh and the maximum of
    its +-min_gap neighbourhood (ties: the earliest). Accepted greedily by novelty (ties earliest) when >= min_gap
    from every accepted one and from both ends (0 and len(nov)); at most cap. Returns sorted indices."""
    cand = novelty_peaks(nov, min_gap, thresh)
    out = []
    for i in sorted(cand, key=lambda k: (-nov[k], k)):
        if len(out) >= cap:
            break
        if all(abs(i - j) >= min_gap for j in out):
            out.append(i)
    return sorted(out)


def label_name(k):
    return chr(65 + k) if k < 26 else chr(65 + k // 26 - 1) + chr(65 + k % 26)


def assign_labels(sim, thresh=SEC_GROUP_MIN):
    """sim[a][b]: segment similarity 0..1. In time order each segment joins the label group whose first segment it is
    most similar to (ties: the earlier group) when that similarity >= thresh, else it starts a new label (A, B, ...
    in order of first appearance). Returns [(label, similarity to its group's first segment)]."""
    reps, out = [], []
    for k in range(len(sim)):
        best = max(range(len(reps)), key=lambda g: (sim[k][reps[g]], -g)) if reps else None
        if best is not None and sim[k][reps[best]] >= thresh:
            out.append((label_name(best), round(min(1.0, max(0.0, sim[k][reps[best]])), 3)))
        else:
            reps.append(k)
            out.append((label_name(len(reps) - 1), 1.0))
    return out


def top_chord(chords, start, end):
    """Label with the most chord time inside [start, end] (ties: earliest overlap), or None."""
    acc = {}
    for order, c in enumerate(chords):
        ov = min(c["end"], end) - max(c["start"], start)
        if ov > 0:
            a = acc.setdefault(c["label"], [0.0, -order])
            a[0] += ov
    if not acc:
        return None
    return max(acc.items(), key=lambda kv: (round(kv[1][0], 6), kv[1][1]))[0]


def section_silent(values, start, end, step=ENERGY_STEP, floor=SILENCE_DB):
    """True when more than half of the energy values whose window midpoint lies in [start, end) (else the window
    holding the midpoint) are below `floor`."""
    sel = [v for i, v in enumerate(values) if start <= (i + 0.5) * step < end]
    if not sel:
        sel = [values[min(len(values) - 1, int(((start + end) / 2.0) // step))]]
    return sum(1 for v in sel if v < floor) * 2 > len(sel)


def section_energy(values, start, end, step=ENERGY_STEP):
    """Mean of the energy values whose window midpoint lies in [start, end) (else the window holding the midpoint)."""
    sel = [v for i, v in enumerate(values) if start <= (i + 0.5) * step < end]
    if not sel:
        sel = [values[min(len(values) - 1, int(((start + end) / 2.0) // step))]]
    return round(sum(sel) / len(sel), 2)


def _segment_similarity(ca, cb, za, zb):
    """0.5 x cosine of the mean chroma (both >= 0, so 0..1) + 0.5 x exp(-d^2/2), d = RMS difference of the mean
    standardized MFCCs in z units (identical timbre 1, one z unit apart 0.61)."""
    na, nb = math.sqrt(sum(x * x for x in ca)), math.sqrt(sum(x * x for x in cb))
    cc = sum(x * y for x, y in zip(ca, cb)) / (na * nb) if na > 0 and nb > 0 else 0.0
    d2 = sum((x - y) ** 2 for x, y in zip(za, zb)) / float(len(za))
    return 0.5 * min(1.0, max(0.0, cc)) + 0.5 * math.exp(-d2 / 2.0)


def structure_debug(nov, times, cut, sim):
    """structure_debug dict (see docstring). nov[i]: novelty at boundary index i (None where not computed), times[i]:
    its time; cut: the selected boundary indices; sim: the full section similarity matrix or None."""
    idx = [i for i, x in enumerate(nov) if x is not None]
    peaks = novelty_peaks(nov)
    sel = set(cut)
    keep = [i for i in peaks if i in sel]
    rest = sorted((i for i in peaks if i not in sel), key=lambda i: (-nov[i], i))
    keep = sorted(keep + rest[:max(0, SEC_DEBUG_PEAKS - len(keep))])
    rv = lambda x: round(min(1.0, max(-1.0, x)), 4)
    return {"kernel_beats": SEC_KERNEL, "min_gap_beats": SEC_MIN, "novelty_threshold": SEC_NOVELTY_MIN,
            "group_threshold": SEC_GROUP_MIN,
            "novelty": {"times": [r3(times[i]) for i in idx], "values": [rv(nov[i]) for i in idx]},
            "peaks": [{"time": r3(times[i]), "value": rv(nov[i]), "selected": i in sel} for i in keep],
            "section_similarity": None if sim is None else [[round(min(1.0, max(0.0, x)), 3) for x in row]
                                                            for row in sim]}


def structure(chroma, mfcc, frame_times, beats, duration, energy, chords):
    """Sections from beat-synchronous chroma + timbre (see SEC_METHOD). chroma/mfcc: per-frame arrays (frames x 12),
    frame_times: their times. Returns (sections, warning|None, structure_debug|None)."""
    import numpy as np
    edges = [b for b in beats if 0.0 < b < duration]
    if not beats or len(frame_times) == 0:
        return [], "no_sections: no beats, sections need a beat grid", None
    nb = len(edges) - 1  # beat intervals [edges[k], edges[k+1])
    debug_nov, cut, full = [], [], None
    if nb < 2 * SEC_KERNEL:
        sections = [(0.0, duration)]
        warn = "sections_single: fewer than %d beat intervals, one section" % (2 * SEC_KERNEL)
    else:
        warn = None
        ft = np.asarray(frame_times, dtype=np.float64)
        lo = np.searchsorted(ft, edges[:-1], side="left")
        hi = np.searchsorted(ft, edges[1:], side="left")
        C, M = np.zeros((nb, 12)), np.zeros((nb, mfcc.shape[1]))
        for k in range(nb):
            a, b = lo[k], hi[k]
            if b <= a:
                a = int(np.argmin(np.abs(ft - (edges[k] + edges[k + 1]) / 2.0)))
                b = a + 1
            C[k], M[k] = chroma[a:b].mean(axis=0), mfcc[a:b].mean(axis=0)
        sd = M.std(axis=0)
        Z = (M - M.mean(axis=0)) / np.where(sd > 1e-9, sd, 1.0)
        unit = lambda X: X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)
        F = np.hstack([unit(C), unit(Z)]) / math.sqrt(2.0)
        S = F @ F.T  # 0.5 cos(chroma) + 0.5 cos(timbre), -1..1
        h = SEC_KERNEL  # only where the full kernel fits: truncated kernels near the ends see part of a loop
        u = np.arange(-h, h)
        g = np.exp(-0.5 * ((u + 0.5) / (h / 2.0)) ** 2) * np.where(u < 0, -1.0, 1.0)
        K = np.outer(g, g)
        K /= np.sum(np.abs(K))
        nov = [None] * (nb + 1)
        for i in range(h, nb - h + 1):
            nov[i] = float(np.sum(K * S[i - h:i + h, i - h:i + h]))
        cut = pick_boundaries(nov, cap=SEC_MAX - 1)
        bounds = [0.0] + [edges[i] for i in cut] + [duration]
        sections = list(zip(bounds[:-1], bounds[1:]))
        idx = [0] + cut + [nb]
        cm = [C[a:b].mean(axis=0).tolist() for a, b in zip(idx[:-1], idx[1:])]
        zm = [Z[a:b].mean(axis=0).tolist() for a, b in zip(idx[:-1], idx[1:])]
        debug_nov = nov
        k = len(cm)
        full = [[0.0] * k for _ in range(k)]
        for a in range(k):
            for b in range(a, k):  # upper triangle mirrored: exactly symmetric
                full[a][b] = full[b][a] = _segment_similarity(cm[a], cm[b], zm[a], zm[b])
    silent = [section_silent(energy, s, e) for s, e in sections]
    keep = [k for k in range(len(sections)) if not silent[k]]  # silence takes no letter and is no reference
    if len(keep) == 1:
        sub = [("A", 1.0)]
    elif keep:
        sim = [[_segment_similarity(cm[a], cm[b], zm[a], zm[b]) for b in keep] for a in keep]
        sub = assign_labels(sim)
    else:
        sub = []
    it = iter(sub)
    labels = [(SILENCE_LABEL, None) if silent[k] else next(it) for k in range(len(sections))]
    out = []
    for (s, e), (lab, sf) in zip(sections, labels):
        out.append({"start": r3(s), "end": r3(e), "label": lab, "energy_mean": section_energy(energy, s, e),
                    "top_chord": top_chord(chords, s, e), "similarity_to_first": sf})
    return out, warn, structure_debug(debug_nov, edges, cut, full)


def sections_from_segments(segs, chroma, mfcc, frame_times, duration, energy, chords):
    """Section rows for model segments [(start, end, function)], contiguous 0..duration. Equal functions share a letter
    and silent rows take none, as in structure(); similarity_to_first compares with the first section of that letter."""
    import numpy as np
    ft = np.asarray(frame_times, dtype=np.float64)
    sd = mfcc.std(axis=0)
    z = (mfcc - mfcc.mean(axis=0)) / np.where(sd > 1e-9, sd, 1.0)
    letters, first, out = {}, {}, []
    for s, e, fn in segs:
        a, b = np.searchsorted(ft, s, side="left"), np.searchsorted(ft, e, side="left")
        if b <= a:
            a = int(np.argmin(np.abs(ft - (s + e) / 2.0)))
            b = a + 1
        mean = (chroma[a:b].mean(axis=0).tolist(), z[a:b].mean(axis=0).tolist())
        if section_silent(energy, s, e):
            lab, sf = SILENCE_LABEL, None
        else:
            lab = letters.setdefault(fn, label_name(len(letters)))
            ref = first.setdefault(lab, mean)
            sim = _segment_similarity(ref[0], mean[0], ref[1], mean[1])
            sf = 1.0 if ref is mean else round(min(1.0, max(0.0, sim)), 3)
        out.append({"start": r3(s), "end": r3(e), "label": lab, "function": fn, "energy_mean": section_energy(energy, s, e),
                    "top_chord": top_chord(chords, s, e), "similarity_to_first": sf})
    return out


def clean_segments(rows, duration):
    """[(start, end, function)] contiguous 0..duration from the helper's rows, or None when they are not usable."""
    if not (isinstance(rows, list) and rows and all(
            isinstance(r, dict) and r.get("label") in SEC_FUNCTIONS and isinstance(r.get("start"), (int, float))
            and isinstance(r.get("end"), (int, float)) and math.isfinite(r["start"]) and math.isfinite(r["end"]) for r in rows)):
        return None
    rows = [r for r in rows if r["start"] < duration - SEC_MIN_SEGMENT and r["end"] - r["start"] > SEC_MIN_SEGMENT]
    if not rows or any(b["start"] < a["start"] for a, b in zip(rows, rows[1:])):
        return None
    cuts = [0.0] + [r["start"] for r in rows[1:]] + [duration]
    if any(b <= a for a, b in zip(cuts, cuts[1:])):
        return None
    return [(a, b, r["label"]) for a, b, r in zip(cuts, cuts[1:], rows)]


def lyric_norm(text):
    """NFKC, lowercase, keep only letters, digits and marks (Unicode categories L*, N*, M*)."""
    return "".join(c for c in unicodedata.normalize("NFKC", text).lower() if unicodedata.category(c)[0] in "LNM")


def parse_words(data):
    """stdin bytes of --words-stdin -> (words [(text, start, end)] or None, reason or None). Any parser or
    conversion failure (bad JSON, nesting too deep, an integer too large for a float) is words_invalid."""
    if len(data) > WORDS_MAX_BYTES:
        return None, "words_too_large"
    try:
        return _parse_words(data)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return None, "words_invalid"


def _parse_words(data):
    v = json.loads(data.decode("utf-8"), parse_constant=lambda name: float("nan"))
    if not isinstance(v, dict):
        return None, "words_invalid"
    w = v.get("words")
    if w is None:
        r = v.get("reason")
        return None, r if isinstance(r, str) and RE_REASON.match(r) else "words_invalid"
    if not isinstance(w, list) or len(w) > LYRIC_MAX_WORDS:
        return None, "words_invalid"
    out = []
    for x in w:
        if not (isinstance(x, list) and len(x) == 3 and isinstance(x[0], str) and len(x[0]) <= LYRIC_MAX_TEXT
                and all(isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) for t in x[1:])):
            return None, "words_invalid"
        out.append((x[0], float(x[1]), float(x[2])))
    return out, None


def kana_fold(text):
    """Katakana U+30A1-U+30F6 -> hiragana (same sound, so one ASR writing a line in katakana still matches)."""
    return "".join(chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c for c in text)


def lyric_spread_ok(a, b):
    """Start times a <= b at least LYRIC_SPREAD apart, inclusive and roundoff-stable: the difference is compared in
    whole microseconds (1.4 and 16.4 are 15 s apart although 16.4 - 1.4 = 14.999999999999998 in binary floats);
    the unit times themselves are not rounded."""
    return round((b - a) * 1e6) >= round(LYRIC_SPREAD * 1e6)


def _within_edits(a, b, k):
    """Levenshtein distance of strings a, b <= k (row minimum cut-off)."""
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j - 1] + (a[i - 1] != b[j - 1]), prev[j] + 1, cur[j - 1] + 1)
        if min(cur) > k:
            return False
        prev = cur
    return prev[-1] <= k


def lyric_approx_pairs(units):
    """Char units -> ([(i, j, m)] matched spans: LYRIC_APPROX_W-char windows within LYRIC_APPROX_K edits whose
    starts are >= LYRIC_SPREAD apart, trimmed at both ends while the chars at the same offset differ (so a window
    reaching past a chorus edge does not cover the neighbouring line), i, j = trimmed starts, m = length; capped flag). Candidates share a LYRIC_APPROX_SEED-char seed at the same
    offset (every offset 0..W-SEED); seeds occurring > LYRIC_APPROX_SEED_MAX times are skipped. Deterministic:
    seeds in first-occurrence order, pairs in index order."""
    s = "".join(u[0] for u in units)
    w, n = LYRIC_APPROX_W, len(s)
    idx = {}
    for i in range(n - LYRIC_APPROX_SEED + 1):
        idx.setdefault(s[i:i + LYRIC_APPROX_SEED], []).append(i)
    seen, out, checks = set(), [], 0
    for ps in idx.values():
        if len(ps) < 2 or len(ps) > LYRIC_APPROX_SEED_MAX:
            continue
        for x in range(len(ps)):
            for y in range(x + 1, len(ps)):
                for off in range(w - LYRIC_APPROX_SEED + 1):
                    i, j = ps[x] - off, ps[y] - off
                    if i < 0 or j + w > n or (i, j) in seen or not lyric_spread_ok(units[i][1], units[j][1]):
                        continue
                    if checks >= LYRIC_APPROX_MAX_CHECKS:
                        return out, True
                    seen.add((i, j))
                    checks += 1
                    if _within_edits(s[i:i + w], s[j:j + w], LYRIC_APPROX_K):
                        a, b = 0, w
                        while a < b and s[i + a] != s[j + a]:
                            a += 1
                        while b > a and s[i + b - 1] != s[j + b - 1]:
                            b -= 1
                        if b > a:
                            out.append((i + a, j + a, b - a))
    return out, False


def lyric_units(words, duration):
    """(units [(unit, t0, t1)], unit kind, normalized char count). Words sorted by start (stable), clamped to
    [0, duration]; empty normalized words dropped. Char units split their word span evenly."""
    toks = []
    for text, a, b in sorted(words, key=lambda x: x[1]):
        t = lyric_norm(text)
        if t:
            a = min(max(a, 0.0), duration)
            toks.append((t, a, min(max(b, a), duration)))
    nchar = sum(len(t) for t, _, _ in toks)
    kind = "char" if not toks or statistics.median(len(t) for t, _, _ in toks) < 2 else "word"
    if kind == "word":
        return toks, kind, nchar
    units = []
    for t, a, b in toks:
        t = kana_fold(t)
        d = (b - a) / len(t)
        units += [(c, a + d * k, a + d * (k + 1)) for k, c in enumerate(t)]
    return units, kind, nchar


def lyric_repeats(words, duration, reason=None):
    """lyric_repeats dict plus warnings (see docstring). words: [(text, start, end)] or None (then `reason`)."""
    if words is None:
        return {"status": "unavailable", "reason": reason or "no_words_input"}, []
    if not words:
        return {"status": "unavailable", "reason": "no_words"}, []
    units, kind, nchar = lyric_units(words, duration)
    if nchar < LYRIC_MIN_CHARS:
        return {"status": "unavailable", "reason": "too_little_text"}, []
    n = LYRIC_N_CHAR if kind == "char" else LYRIC_N_WORD
    pos = {}
    for i in range(len(units) - n + 1):
        pos.setdefault(tuple(u[0] for u in units[i:i + n]), []).append(i)
    reps = []  # (occurrence starts, length) of the repeats: exact n-grams, then approximate char windows
    for ps in pos.values():
        ts = [units[p][1] for p in ps]
        if len(ps) >= 2 and lyric_spread_ok(min(ts), max(ts)):
            reps.append((ps, n))
    warns = []
    if kind == "char":
        pairs, capped = lyric_approx_pairs(units)
        reps += [([i, j], m) for i, j, m in pairs]
        if capped:
            warns.append("lyric_repeats_approx_capped: %d window checks" % LYRIC_APPROX_MAX_CHECKS)
    covered = [False] * len(units)
    for ps, m in reps:
        for p in ps:
            for k in range(p, p + m):
                covered[k] = True
    blocks, of = [], [None] * len(units)  # [start, end], unit index -> block index
    for i, cv in enumerate(covered):
        if not cv:
            continue
        a, b = units[i][1], units[i][2]
        if blocks and a - blocks[-1][1] <= LYRIC_JOIN:
            blocks[-1][1] = max(blocks[-1][1], b)
        else:
            blocks.append([a, b])
        of[i] = len(blocks) - 1
    kept = [k for k, (a, b) in enumerate(blocks) if b - a >= LYRIC_MIN_BLOCK]
    if len(kept) > LYRIC_MAX_BLOCKS:
        warns.append("lyric_repeats_capped: %d blocks, the %d longest kept" % (len(kept), LYRIC_MAX_BLOCKS))
        kept = sorted(sorted(kept, key=lambda k: (-(blocks[k][1] - blocks[k][0]), k))[:LYRIC_MAX_BLOCKS])
    kset = set(kept)
    parent = {k: k for k in kept}

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for ps, m in reps:
        # every retained block touched by any unit of any occurrence (an occurrence split by a >3 s pause
        # spans several blocks)
        bs = sorted({of[k] for p in ps for k in range(p, p + m) if of[k] in kset})
        for k in bs[1:]:
            ra, rb = root(bs[0]), root(k)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    gid, out = {}, []
    for k in kept:  # time order: group ids by first appearance
        g = gid.setdefault(root(k), len(gid))
        out.append({"start": r3(blocks[k][0]), "end": r3(blocks[k][1]), "group": g})
    return ({"status": "done", "method": LYRIC_METHOD, "unit": kind, "n": n, "blocks": out, "group_count": len(gid),
             "coverage_s": r3(sum(b["end"] - b["start"] for b in out)), "note": LYRIC_NOTE}, warns)


def note_name(midi):
    """MIDI number -> scientific pitch name with sharps (60 = C4, 69 = A4)."""
    return NOTE_NAMES[midi % 12] + str(midi // 12 - 1)


def _pitch(midi):
    return {"midi": midi, "note": note_name(midi)}


def _wquantile(pairs, q):
    """Duration-weighted quantile of [(midi, weight)]: the smallest midi whose cumulative weight reaches q."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for m, w in pairs:
        acc += w
        if acc >= q * total - 1e-12:
            return m
    return pairs[-1][0]


def _frac(n, d):
    return round(n / float(d), 3) if d else 0.0


def melody_intervals(notes, gap=MEL_PHRASE_GAP):
    """Intervals between consecutive notes at most `gap` s apart (onset minus previous end): repeat (0 semitones),
    step (1-2), leap (>= 3), up and down as fractions of all counted intervals. None when there is none."""
    iv = [b["midi"] - a["midi"] for a, b in zip(notes, notes[1:]) if b["start"] - a["end"] <= gap]
    if not iv:
        return None
    n = len(iv)
    return {"count": n, "repeat": _frac(sum(1 for d in iv if d == 0), n),
            "step": _frac(sum(1 for d in iv if 1 <= abs(d) <= 2), n),
            "leap": _frac(sum(1 for d in iv if abs(d) >= 3), n),
            "up": _frac(sum(1 for d in iv if d > 0), n), "down": _frac(sum(1 for d in iv if d < 0), n),
            "max_gap_s": gap}


def melody_direction(notes, min_notes=MEL_DIR_NOTES, thresh=MEL_DIR_MIN):
    """Least-squares line of midi against note midpoint time; fitted change = slope x (last - first midpoint).
    rising >= +thresh semitones, falling <= -thresh, else flat; None with fewer than min_notes notes."""
    if len(notes) < min_notes:
        return None
    t = [(n["start"] + n["end"]) / 2.0 for n in notes]
    y = [float(n["midi"]) for n in notes]
    tm, ym = sum(t) / len(t), sum(y) / len(y)
    var = sum((a - tm) ** 2 for a in t)
    if var <= 0:
        return "flat"
    change = sum((a - tm) * (b - ym) for a, b in zip(t, y)) / var * (t[-1] - t[0])
    return "rising" if change >= thresh else "falling" if change <= -thresh else "flat"


def _reversals(midis):
    """Sign changes between consecutive nonzero pitch moves."""
    d = [b - a for a, b in zip(midis, midis[1:]) if b != a]
    return sum(1 for x, y in zip(d, d[1:]) if (x > 0) != (y > 0))


def _join(group, midi):
    return {"start": group[0]["start"], "end": group[-1]["end"], "midi": midi, "note": note_name(midi)}


def _ms(t):
    """Seconds -> integer milliseconds. Note rows are rounded to ms, so this is exact; every merge threshold is
    compared in these units (float differences such as 10.2 - 10.0 = 0.1999999999999993 would cross 0.2 s)."""
    return int(round(t * 1000.0))


def _dur_ms(x):
    return _ms(x["end"]) - _ms(x["start"])


def _gap_ms(a, b):
    return _ms(b["start"]) - _ms(a["end"])


def same_pitch_join(notes, gap=MERGE_GAP):
    """Consecutive notes with the same midi and gap <= `gap` (compared in integer ms) become one note."""
    out = []
    for x in notes:
        if out and out[-1]["midi"] == x["midi"] and _gap_ms(out[-1], x) <= _ms(gap):
            out[-1]["end"] = max(out[-1]["end"], x["end"])
        else:
            out.append(dict(x))
    return out


def _oscillation_ok(g, short, centre_max, reversals):
    lo, hi = min(x["midi"] for x in g), max(x["midi"] for x in g)
    for x in g:
        if _dur_ms(x) >= _ms(centre_max if hi - lo == 2 and x["midi"] == lo + 1 else short):
            return False
    return _reversals([x["midi"] for x in g]) >= reversals


def merge_notes(notes, gap=MERGE_GAP, short=MERGE_SHORT, centre_max=MERGE_CENTRE, span=MERGE_SPAN,
                reversals=MERGE_REVERSALS, max_run=MERGE_MAX_RUN):
    """Same-pitch join, oscillation join, same-pitch join again (see docstring). notes: sorted rows; returns new
    rows. From each start the longest qualifying window wins (greedy, left to right)."""
    notes = same_pitch_join(notes, gap)
    out, i, n = [], 0, len(notes)
    while i < n:
        best, lo, hi, j = i, notes[i]["midi"], notes[i]["midi"], i
        while j + 1 < n and j + 1 - i < max_run:
            x = notes[j + 1]
            lo2, hi2 = min(lo, x["midi"]), max(hi, x["midi"])
            if _gap_ms(notes[j], x) > _ms(gap) or hi2 - lo2 > span:
                break
            lo, hi, j = lo2, hi2, j + 1
            if _oscillation_ok(notes[i:j + 1], short, centre_max, reversals):
                best = j
        if best > i:
            g = notes[i:best + 1]
            w = sum(_dur_ms(y) for y in g)  # integer ms weights (>= 1 each: rows have end > start)
            sw = sum(y["midi"] * _dur_ms(y) for y in g)
            out.append(_join(g, (2 * sw + w) // (2 * w)))  # floor(sw / w + 1/2): exact half up
            i = best + 1
        else:
            out.append(notes[i])
            i += 1
    return same_pitch_join(out, gap)


def scoop_join(notes, short=SCOOP_MAX, gap=MERGE_GAP, steps=SCOOP_STEPS):
    """Vocal notes only (0.20.0). A note <= `short` whose gap to the next note is <= `gap`, where the next note is
    `steps` semitones higher and at least twice as long, is removed and the next note starts at its start: a sung
    scoop from below otherwise reads as an extra note a semitone flat (synthetic 200-cent scoops of 80 and 150 ms
    each gave a 0.104 s note). Left to right, once; the caller joins same-pitch pieces again afterwards. Compared in integer ms like the merge. Songs 51-100 of RWC-P
    against MIDI vocal notes: pitch F 31.5 -> 34.3, rhythm F 62.0 -> 62.2 (Goal R, not blind)."""
    out, i, n = [], 0, len(notes)
    while i < n:
        a = notes[i]
        if i + 1 < n:
            b = notes[i + 1]
            if (_dur_ms(a) <= _ms(short) and _gap_ms(a, b) <= _ms(gap) and b["midi"] - a["midi"] in steps
                    and _dur_ms(b) >= 2 * _dur_ms(a)):
                out.append(dict(b, start=a["start"]))
                i += 2
                continue
        out.append(a)
        i += 1
    return out


def notes_per_second(notes):
    """len(notes) / summed note duration (s), 3 decimals; None without notes."""
    t = sum(n["end"] - n["start"] for n in notes)
    return round(len(notes) / t, 3) if notes and t > 0 else None


def melody_summary(notes, voiced, hop_s, n_frames, tonic, scale, sections):
    """notes: [{start, end, midi, note}] sorted; voiced: sorted times (s) of voiced pitch frames; n_frames: all pitch
    frames. notes_per_second = notes / the summed duration of those notes (note time; the same definition for the
    song and for each section, so a note crossing a section edge counts with its own full length and the server can
    recompute it from the stored rows), so rests and gaps do not dilute it.
    Range p5/p95, median and scale degrees are duration-weighted. Section stats take the notes whose onset lies in
    the section ([start, end), the last one closed). Returns the `melody` dict (status done)."""
    vsec = len(voiced) * hop_s
    w = [(n["midi"], max(n["end"] - n["start"], 1e-6)) for n in notes]
    rng = med = degrees = None
    if notes:
        rng = {"low": _pitch(min(n["midi"] for n in notes)), "high": _pitch(max(n["midi"] for n in notes)),
               "p5": _pitch(_wquantile(w, 0.05)), "p95": _pitch(_wquantile(w, 0.95))}
        med = _wquantile(w, 0.5)
        pc = PITCH_CLASS.get(tonic)
        if pc is not None:
            bins = [0.0] * 12
            for m, d in w:
                bins[(m - pc) % 12] += d
            tot = sum(bins)
            degrees = {"tonic": tonic, "scale": scale, "weight": "duration",
                       "bins": [round(b / tot, 3) for b in bins]}
    secs = []
    for k, sec in enumerate(sections):
        last = k == len(sections) - 1
        inside = [n for n in notes if sec["start"] <= n["start"] < sec["end"] or (last and n["start"] == sec["end"])]
        row = {"label": sec["label"], "start": sec["start"], "end": sec["end"], "note_count": len(inside),
               "low": None, "high": None, "median_midi": None, "notes_per_second": None, "peak": None,
               "direction": melody_direction(inside)}
        if inside:
            top = max(inside, key=lambda n: (n["midi"], -n["start"]))
            row.update(low=min(n["midi"] for n in inside), high=top["midi"],
                       median_midi=_wquantile([(n["midi"], max(n["end"] - n["start"], 1e-6)) for n in inside], 0.5),
                       notes_per_second=notes_per_second(inside),
                       peak=dict(_pitch(top["midi"]), time=top["start"]))
        secs.append(row)
    return {"status": "done", "method": MEL_METHOD, "reliability": "low_reference",
            "voiced_fraction": _frac(len(voiced), n_frames), "voiced_s": round(vsec, 3), "note_count": len(notes),
            "notes_per_second": notes_per_second(notes),
            "range": rng, "median_midi": med, "intervals": melody_intervals(notes), "scale_degrees": degrees,
            "sections": secs}


def smooth_pitch(pitch, w=MEL_SMOOTH):
    """Median of the pitch in cents over w frames centred on each voiced frame, inside each voiced run (runs of
    frames > 0); runs are edge-padded with their nearest value. Unvoiced frames (<= 0) stay as they are."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view
    p = np.asarray(pitch, dtype=np.float64)
    out = p.copy()
    v = p > 0
    edges = np.flatnonzero(np.diff(np.concatenate(([0], v.astype(np.int8), [0]))))
    h = w // 2
    for a, b in zip(edges[::2], edges[1::2]):
        c = 1200.0 * np.log2(p[a:b] / 440.0)
        c = np.median(sliding_window_view(np.pad(c, h, mode="edge"), w), axis=1)
        out[a:b] = 440.0 * 2.0 ** (c / 1200.0)
    return out


def read_riff_pcm16(path):
    """(rate, channels, samples bytes) of an afconvert WAV, which can use the extensible format header."""
    with open(path, "rb") as f:
        b = f.read()
    i, chunks = 12, {}
    while i + 8 <= len(b):
        n = int.from_bytes(b[i + 4:i + 8], "little")
        chunks[b[i:i + 4]] = b[i + 8:i + 8 + n]
        i += 8 + n + (n & 1)
    fmt = chunks[b"fmt "]
    return int.from_bytes(fmt[4:8], "little"), int.from_bytes(fmt[2:4], "little"), chunks[b"data"]


def write_wav(path, rate, x):
    import numpy as np
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes())


def band_db(x):
    """Share of the energy above SEP_BAND_HZ in dB; silence counts as no band."""
    import numpy as np
    power = np.abs(np.fft.rfft(x)) ** 2
    total = float(power.sum())
    if total <= 0.0:
        return -math.inf
    high = float(power[np.fft.rfftfreq(len(x), 1.0 / SEP_RATE) > SEP_BAND_HZ].sum())
    return 10.0 * math.log10(high / total) if high > 0.0 else -math.inf


def run_tool(argv):
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          env=dict(os.environ, DEMUCS_MLX_NO_DOWNLOAD="1", HF_HUB_OFFLINE="1"), cwd="/").returncode


def mono_stem(sep, stem):
    """True when scratch/full/<stem>.wav is there. The first call separates all four stems in one Demucs run; when that
    run fails, no stem stage uses its files."""
    d = sep["scratch"]
    path = os.path.join(d, "full", stem + ".wav")
    if "mono_ok" not in sep and not os.path.isfile(path):
        sep["mono_ok"] = not run_tool([sep["demucs"]] + list(MONO_DEMUCS_ARGS) + ["-o", d, os.path.join(d, "full.wav")])
    return sep.get("mono_ok", True) and os.path.isfile(path)


def vocal_pitch(sep, n16):
    """(8 ms pitch, 16 kHz vocal, None) or (None, None, reason)."""
    import numpy as np
    from scipy.io import wavfile
    from scipy.signal import resample_poly
    for path, sha, cap in ((os.path.expanduser(DEMUCS_WEIGHTS), DEMUCS_WEIGHTS_SHA256, DEMUCS_WEIGHTS_MAX_BYTES),
                           (sep["rmvpe"], RMVPE_SHA256, RMVPE_MAX_BYTES),
                           (RMVPE_CODE, RMVPE_CODE_SHA256, RMVPE_CODE_MAX_BYTES)):
        if read_pinned(path, sha, cap, keep=False)[0]:
            return None, None, "models_invalid"
    d = sep["scratch"]
    os.makedirs(d, mode=0o700, exist_ok=True)
    decoded, full = os.path.join(d, "decoded.wav"), os.path.join(d, "full.wav")
    if run_tool(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@%d" % SEP_RATE, "-c", "1", sep["source"], decoded]):
        return None, None, "decode_failed"
    rate, channels, raw = read_riff_pcm16(decoded)
    if (rate, channels) != (SEP_RATE, 1):
        return None, None, "decode_failed"
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if band_db(x) < SEP_BAND_MIN_DB:
        return None, None, "band_limited"
    write_wav(full, SEP_RATE, x)
    os.unlink(decoded)
    if not mono_stem(sep, "vocals"):
        return None, None, "separation_failed"
    rate, y = wavfile.read(os.path.join(d, "full", "vocals.wav"))
    y = y.astype(np.float32)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if float(np.abs(y).max(initial=0.0)) > 2.0:
        y /= 32768.0
    g = math.gcd(rate, SRC_RATE)
    y = resample_poly(y, SRC_RATE // g, rate // g).astype(np.float32)
    stem = np.zeros(n16, dtype=np.float32)
    stem[:min(n16, len(y))] = y[:n16]
    stem_wav, f0_npy = os.path.join(d, "stem16.wav"), os.path.join(d, "f0.npy")
    write_wav(stem_wav, SRC_RATE, stem)
    if run_tool([sys.executable, "-I", RMVPE_CODE, sep["rmvpe"], stem_wav, f0_npy]):
        return None, None, "rmvpe_failed"
    f0 = np.load(f0_npy, allow_pickle=False)
    if f0.ndim != 1 or not len(f0) or not np.all(np.isfinite(f0)):
        return None, None, "rmvpe_failed"
    hop_s = MEL_HOP / float(SRC_RATE)
    i = np.minimum(np.round(np.arange(n16 // MEL_HOP + 1) * hop_s / RMVPE_HOP_S).astype(int), len(f0) - 1)
    return f0[i].astype(np.float64), stem, None


def bass_rows(f0, starts):
    """10 ms pitch and note starts -> rows. A same-pitch run splits only at a start; a same-pitch gap joins unless a
    start falls on the frame that ends it. Notes under 80 ms are dropped."""
    import numpy as np
    import scipy.signal
    m = np.where(f0 > 0, np.round(69 + 12 * np.log2(np.maximum(f0, 1e-6) / 440.0)), 0).astype(int)
    m = scipy.signal.medfilt(m, 5).astype(int)
    n, cuts = len(m), np.zeros(len(m) + 1, bool)
    for t in starts:
        i = int(round(t / RMVPE_HOP_S))
        if 0 <= i < n:
            cuts[i] = True
    runs, a = [], 0
    for i in range(1, n + 1):
        if i == n or m[i] != m[a] or cuts[i]:
            if m[a] > 0 and i - a >= BASS_MIN_FRAMES:
                runs.append([a, i, int(m[a])])
            a = i
    out = []
    for r in runs:
        if out and r[2] == out[-1][2] and r[0] - out[-1][1] <= BASS_JOIN_FRAMES and not cuts[r[0]]:
            out[-1][1] = r[1]
        else:
            out.append(r)
    return [{"start": r3(a * RMVPE_HOP_S), "end": r3(b * RMVPE_HOP_S), "midi": x, "note": note_name(x)}
            for a, b, x in out[:BASS_MAX_NOTES]]


def bass_confirmed(rows, notes):
    """Rows that a Basic Pitch note [start, end, midi] within BASS_CONFIRM_SEMIS overlaps in time; drum ring has none.
    No octave tolerance: on the drum-only song it kept 6 rows that are drum ring."""
    import bisect
    notes = sorted(notes)
    starts = [n[0] for n in notes]
    span = max([e - s for s, e, _ in notes], default=0.0)
    out = []
    for r in rows:  # only the notes that start in (row start - longest note, row end) can overlap it
        lo, hi = bisect.bisect_right(starts, r["start"] - span), bisect.bisect_left(starts, r["end"])
        if any(e > r["start"] and abs(m - r["midi"]) <= BASS_CONFIRM_SEMIS for _, e, m in notes[max(0, lo - 1):hi]):
            out.append(r)
    return out


def bass_tidied(rows, notes):
    """Joins same-pitch pieces, then moves late starts back to Basic Pitch's (see BASS_JOIN_GAP_S). notes: [start, end, midi]."""
    import bisect
    notes = sorted(notes)
    starts = [n[0] for n in notes]

    def near(t0, t1, midi):  # Basic Pitch starts in [t0, t1] within BASS_CONFIRM_SEMIS of midi, in time order
        return [s for s, _, m in notes[bisect.bisect_left(starts, t0 - 1e-9):bisect.bisect_right(starts, t1 + 1e-9)]
                if abs(m - midi) <= BASS_CONFIRM_SEMIS]
    joined = []
    for r in rows:
        last = joined[-1] if joined else None
        if (last and r["midi"] == last["midi"] and r["start"] - last["end"] <= BASS_JOIN_GAP_S + 1e-9
                and not near(r["start"] - BASS_JOIN_BLOCK_S, r["start"] + BASS_JOIN_BLOCK_S, r["midi"])):
            joined[-1] = dict(last, end=r["end"])
        else:
            joined.append(dict(r))
    out = []
    for r in joined:
        c = near(r["start"] - BASS_SNAP_S, r["start"] + BASS_JOIN_BLOCK_S, r["midi"])
        if c and c[-1] < r["start"]:  # a start at or just after the row's means the row's start is already right
            r["start"] = r3(max(c[-1], out[-1]["end"] if out else 0.0))
        out.append(r)
    return out


def bass_stem_notes(sep, n16):
    """(rows, None) or (None, reason). The scratch already holds full.wav."""
    import numpy as np
    from scipy.io import wavfile
    from scipy.signal import resample_poly
    if read_pinned(BASS_ONSETS_CODE, BASS_ONSETS_SHA256, BASS_ONSETS_MAX_BYTES, keep=False)[0]:
        return None, "models_invalid"
    d = sep["scratch"]
    if not mono_stem(sep, "bass"):
        return None, "separation_failed"
    rate, y = wavfile.read(os.path.join(d, "full", "bass.wav"))
    y = y.astype(np.float32)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if float(np.abs(y).max(initial=0.0)) > 2.0:
        y /= 32768.0
    g = math.gcd(rate, SRC_RATE)
    y = resample_poly(y, SRC_RATE // g, rate // g).astype(np.float32)
    stem_wav, f0_npy, onsets = (os.path.join(d, x) for x in ("bass16.wav", "bass.npy", "bass-onsets.json"))
    write_wav(stem_wav, SRC_RATE, y[:n16])
    if run_tool([sys.executable, "-I", RMVPE_CODE, sep["rmvpe"], stem_wav, f0_npy]):
        return None, "rmvpe_failed"
    f0 = np.load(f0_npy, allow_pickle=False)
    if f0.ndim != 1 or not len(f0) or not np.all(np.isfinite(f0)):
        return None, "rmvpe_failed"
    if run_tool([sep["basic_pitch"], "-I", BASS_ONSETS_CODE, stem_wav, onsets]):
        return None, "onsets_failed"
    with open(onsets) as f:
        notes = json.load(f)
    if not (isinstance(notes, list) and all(isinstance(n, list) and len(n) == 3 and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in n) for n in notes)):
        return None, "onsets_failed"
    return bass_tidied(bass_confirmed(bass_rows(f0, [n[0] for n in notes]), notes), notes), None


def bass_input(sep, n16, vocal_reason):
    """Sets sep["bass"] to (field, rows). Runs before the scratch goes. Never raises."""
    if not os.path.isfile(os.path.join(sep["scratch"], "full.wav")):
        sep["bass"] = ({"status": "skipped", "reason": vocal_reason or "separation_failed"}, [])
        return
    try:
        rows, reason = bass_stem_notes(sep, n16)
    except Exception:
        rows, reason = None, "separation_failed"
    if reason:
        sep["bass"] = ({"status": "skipped", "reason": reason}, [])
    else:
        sep["bass"] = ({"status": "done", "method": BASS_METHOD, "reliability": "low_reference", "note_count": len(rows)}, rows)


def drum_stem_hits(sep, duration):
    """(rows, None) or (None, reason). The scratch already holds full.wav."""
    model = sep["drum_model"]
    if not model.endswith(".index"):
        return None, "models_invalid"
    data = model[:-len(".index")] + ".data-00000-of-00001"
    for path, sha, cap in ((model, ADTOF_INDEX_SHA256, ADTOF_INDEX_MAX_BYTES), (data, ADTOF_DATA_SHA256, ADTOF_DATA_MAX_BYTES),
                           (ADTOF_CODE, ADTOF_CODE_SHA256, ADTOF_CODE_MAX_BYTES)):
        if read_pinned(path, sha, cap, keep=False)[0]:
            return None, "models_invalid"
    d = sep["scratch"]
    if not mono_stem(sep, "drums"):
        return None, "separation_failed"
    out = os.path.join(d, "drums.json")
    if run_tool([sys.executable, "-I", ADTOF_CODE, model, os.path.join(d, "full", "drums.wav"), out]):
        return None, "adtof_failed"
    with open(out) as f:
        v = json.load(f)
    if not (isinstance(v, dict) and set(v) == set(DRUM_CLASSES)
            and all(isinstance(t, (int, float)) and math.isfinite(t) for c in DRUM_CLASSES for t in v[c])):
        return None, "adtof_failed"
    rows = sorted(({"time": r3(t), "class": c} for c in DRUM_CLASSES for t in v[c] if 0 <= t <= duration),
                  key=lambda r: (r["time"], DRUM_CLASSES.index(r["class"])))
    return rows[:DRUM_MAX_HITS], None


def drum_input(sep, n16, vocal_reason):
    """Sets sep["drums"] to (field, rows). Runs before the scratch goes. Never raises."""
    if not os.path.isfile(os.path.join(sep["scratch"], "full.wav")):
        sep["drums"] = ({"status": "skipped", "reason": vocal_reason or "separation_failed"}, [])
        return
    try:
        rows, reason = drum_stem_hits(sep, n16 / SRC_RATE)
    except Exception:
        rows, reason = None, "adtof_failed"
    if reason:
        sep["drums"] = ({"status": "skipped", "reason": reason}, [])
    else:
        counts = {c: sum(r["class"] == c for r in rows) for c in DRUM_CLASSES}
        sep["drums"] = ({"status": "done", "method": DRUM_METHOD, "reliability": "reference_candidate", "hit_counts": counts}, rows)


def other_stem_notes(sep, duration):
    """(rows, None) or (None, reason). The scratch already holds full.wav and any stems made so far."""
    if read_pinned(OTHER_NOTES_CODE, OTHER_NOTES_SHA256, OTHER_NOTES_MAX_BYTES, keep=False)[0]:
        return None, "models_invalid"
    d = sep["scratch"]
    stem = os.path.join(d, "full", "other.wav")
    if not mono_stem(sep, "other"):
        return None, "separation_failed"
    out = os.path.join(d, "other-notes.json")
    if run_tool([sep["basic_pitch"], "-I", OTHER_NOTES_CODE, stem, out]):
        return None, "notes_failed"
    with open(out) as f:
        v = json.load(f)
    rows = []
    for s, e, m in v:
        if not (math.isfinite(s) and math.isfinite(e) and isinstance(m, int) and 0 <= m <= 127):
            return None, "notes_failed"
        start, end = r3(max(s, 0.0)), r3(min(e, duration))
        if start < end:
            rows.append({"start": start, "end": end, "midi": m, "note": note_name(m)})
    rows.sort(key=lambda r: (r["start"], r["midi"]))
    return (None, "too_many_notes") if len(rows) > OTHER_MAX_NOTES else (rows, None)


def other_input(sep, n16, vocal_reason):
    """Sets sep["other"] to (field, rows). Runs before the scratch goes. Never raises."""
    if not os.path.isfile(os.path.join(sep["scratch"], "full.wav")):
        sep["other"] = ({"status": "skipped", "reason": vocal_reason or "separation_failed"}, [])
        return
    try:
        rows, reason = other_stem_notes(sep, n16 / SRC_RATE)
    except Exception:
        rows, reason = None, "notes_failed"
    if reason:
        sep["other"] = ({"status": "skipped", "reason": reason}, [])
    else:
        sep["other"] = ({"status": "done", "method": OTHER_METHOD, "reliability": "low_reference", "note_count": len(rows)}, rows)


def sing_stem_notes(sep, duration, left):
    """(rows, None) or (None, reason). The scratch holds full/vocals.wav. left: seconds this step may take."""
    if left < SING_MIN:
        return None, "no_time"
    if (read_pinned(SING_CODE, SING_CODE_SHA256, SING_CODE_MAX_BYTES, keep=False)[0]
            or read_pinned(os.path.join(SING_DIR, SING_CKPT), SING_CKPT_SHA256, SING_CKPT_MAX_BYTES, keep=False)[0]):
        return None, "models_invalid"
    d = sep["scratch"]
    out, tmp = os.path.join(d, "sing-notes.json"), os.path.join(d, "sing-tmp")
    os.makedirs(tmp, mode=0o700, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~"), "LANG": "en_US.UTF-8", "TMPDIR": tmp, "MPLCONFIGDIR": tmp,
           "NUMBA_CACHE_DIR": tmp, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "WANDB_MODE": "disabled"}
    try:
        code = subprocess.run([sep["singing_python"], "-I", "-W", "ignore", SING_CODE, SING_DIR, os.path.join(d, "full", "vocals.wav"), out],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd="/",
                              timeout=min(left, SING_MAX)).returncode
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if code:
        return None, "notes_failed"
    with open(out) as f:
        v = json.load(f)
    rows = []
    for s, e, m in v:
        if not (isinstance(s, float) and isinstance(e, float) and math.isfinite(s) and math.isfinite(e)
                and isinstance(m, int) and 0 <= m <= 127):
            return None, "notes_failed"
        start, end = r3(max(s, 0.0)), r3(min(e, duration))
        if start < end:
            rows.append({"start": start, "end": end, "midi": m, "note": note_name(m)})
    rows.sort(key=lambda r: (r["start"], r["end"], r["midi"]))
    if not rows:
        return None, "no_notes"  # e.g. every note tagged chorus: keep the RMVPE notes
    return (None, "too_many_notes") if len(rows) > MELODY_MAX_NOTES else (rows, None)


def sing_input(sep, n16, vocal_reason):
    """Sets sep["sing"] to (field, rows). Runs last before the scratch goes, in the time left. Never raises."""
    if vocal_reason or not os.path.isfile(os.path.join(sep["scratch"], "full", "vocals.wav")):
        sep["sing"] = ({"status": "skipped", "reason": "no_vocal"}, [])
        return
    left = BUDGET - SING_RESERVE - (time.monotonic() - START)
    try:
        rows, reason = sing_stem_notes(sep, n16 / SRC_RATE, left)
    except Exception:
        rows, reason = None, "notes_failed"
    sep["sing"] = ({"status": "skipped", "reason": reason}, []) if reason else ({"status": "done"}, rows)


def clean_downbeats(times, duration):
    """Rounded ascending downbeats inside the song, or [] when they cannot make at least one bar."""
    if not (isinstance(times, list) and all(isinstance(t, (int, float)) and math.isfinite(t) for t in times)):
        return []
    rows = [r3(t) for t in times if 0 <= t < duration]
    if not 2 <= len(rows) <= BAR_MAX or any(b <= a for a, b in zip(rows, rows[1:])):
        return []
    return rows


def clean_beats(times, duration):
    """Rounded ascending beat times inside the song, or [] when the list is unusable."""
    if not (isinstance(times, list) and len(times) <= MAX_BEATS
            and all(isinstance(t, (int, float)) and math.isfinite(t) for t in times)):
        return []
    rows = [r3(t) for t in times if 0 <= t < duration]
    return [] if any(b <= a for a, b in zip(rows, rows[1:])) else rows


def beats_per_bar(beats, downbeats):
    """3 or 4 when the median count of beats in [downbeat, next downbeat) is that, else None."""
    if not beats or not downbeats or len(downbeats) < 2:
        return None
    n = statistics.median(bisect.bisect_left(beats, b) - bisect.bisect_left(beats, a) for a, b in zip(downbeats, downbeats[1:]))
    return int(n) if n in (3, 4) else None


def bar_steps(times, downbeats, steps=16):
    """{bar index: sorted steps}: each hit goes to the nearest of `steps` steps, the last one to the next bar's first."""
    out = {}
    for t in times:
        i = bisect.bisect_right(downbeats, t) - 1
        if i < 0 or i >= len(downbeats) - 1:
            continue
        k = int(round((t - downbeats[i]) / (downbeats[i + 1] - downbeats[i]) * steps))
        if k == steps:
            i, k = i + 1, 0
        if i < len(downbeats) - 1:
            out.setdefault(i, set()).add(k)
    return {i: sorted(v) for i, v in out.items()}


def chord_tones(label):
    """Pitch classes of a chord label (triad plus written 7th), root first, or None for no chord."""
    if label in ("N", "X", "", None):
        return None
    head = label.split("/")[0]
    root = head.split(":")[0]
    root = root[:2] if root[:2] in NOTE_PCS else root[:1]
    if root not in NOTE_PCS:
        return None
    q = (head.split(":")[1] if ":" in head else head[len(root):]).split("(")[0]
    if q.startswith(("hdim", "m7b5")):
        steps = (0, 3, 6, 10)
    elif q.startswith("mMaj7"):
        steps = (0, 3, 7, 11)
    elif q.startswith("dim"):
        steps = (0, 3, 6, 9) if q.startswith("dim7") else (0, 3, 6)
    elif q.startswith("aug"):
        steps = (0, 4, 8)
    elif q.startswith("sus2"):
        steps = (0, 2, 7)
    elif q.startswith("sus"):
        steps = (0, 5, 7)
    elif q.startswith(("maj7", "maj9")):
        steps = (0, 4, 7, 11)
    elif q.startswith(("min", "m")) and not q.startswith("maj"):
        steps = (0, 3, 7, 10) if q.startswith(("min7", "min9", "m7", "m9")) else (0, 3, 7)
    elif q[:1] in ("7", "9"):
        steps = (0, 4, 7, 10)
    else:
        steps = (0, 4, 7)
    return [(NOTE_PCS[root] + s) % 12 for s in steps]


def scale_path(labels, penalty=SCALE_PENALTY):
    """Major-scale tonic per label by Viterbi: a bar scores the share of its chord's tones in the scale, a change costs
    the penalty. None when no label is a chord."""
    tones = [chord_tones(x) for x in labels]
    if not any(tones):
        return None
    scales = [{(k + s) % 12 for s in MAJOR_STEPS} for k in range(12)]
    score = [[sum(p in sc for p in t) / len(t) for sc in scales] if t else [0.0] * 12 for t in tones]
    best, back = score[0][:], []
    for row in score[1:]:
        j = best.index(max(best))
        back.append([k if best[k] >= best[j] - penalty else j for k in range(12)])
        best = [max(best[k], best[j] - penalty) + row[k] for k in range(12)]
    out = [best.index(max(best))]
    for b in reversed(back):
        out.append(b[out[-1]])
    return out[::-1]


def bars_to_song_end(downbeats, duration):
    """Bars from the downbeats, then on at the median bar length to the song's end: the section model can stop early,
    and the chords there still count for the scale path."""
    spans = list(zip(downbeats, downbeats[1:]))
    size = statistics.median(b - a for a, b in spans)
    t = downbeats[-1]
    while t + size < duration:
        spans.append((t, t + size))
        t += size
    return spans + [(t, duration)]


def bar_spans(downbeats, duration):
    """The bar rows' spans: bars_to_song_end, at most BAR_MAX. Tail test (RWC-P, frozen, 2026-11-07): the added bar starts
    matched annotated downbeats 77% of the time (first added bar 91%) and found 80% of the downbeats after the last one."""
    return [(a, b) for a, b in ((r3(a), r3(b)) for a, b in bars_to_song_end(downbeats, duration)) if b > a][:BAR_MAX]


def bar_scales(spans, chords):
    """(scale name, degree name) per bar row; degree None for a bar with no chord, both None with no chords at all."""
    labels = [top_chord(chords, a, b) for a, b in spans]
    path = scale_path(labels)
    out = []
    for i in range(len(spans)):
        t = chord_tones(labels[i])
        out.append((SCALE_NAMES[path[i]] if path else None,
                    DEGREE_NAMES[(t[0] - path[i]) % 12] if path and t else None))
    return out


def key_change_warning(key_sections, bars):
    """The key_changes warning, or None. With bar scales it counts bars whose scale differs from the bar before (the key
    path; on RWC-P truth it reported 0.00 false changes per no-change song where the 20 s windows reported 3.08);
    without them it counts the 20 s key sections."""
    scales = [r["scale"] for r in (bars or {}).get("rows", [])]
    if scales and all(scales):
        n = sum(a != b for a, b in zip(scales, scales[1:]))
        return "key_changes: %d scale changes over bars (key path)" % n if n else None
    if len(key_sections) > 1:
        return "key_changes: %d sections on %g s windows (DSP estimate)" % (len(key_sections), KEY_WINDOW)
    return None


def half_chord(chords, start, end):
    """(top chord label, its share of [start, end]), or (None, 0)."""
    label = top_chord(chords, start, end)
    if label is None:
        return None, 0.0
    held = sum(max(0.0, min(c["end"], end) - max(c["start"], start)) for c in chords if c["label"] == label)
    return label, held / (end - start)


def bar_split(chords, start, end):
    """[first half's chord, second half's chord] when each holds BAR_SPLIT_SHARE of its half and their roots differ, else None."""
    mid = (start + end) / 2.0
    (a, share_a), (b, share_b) = half_chord(chords, start, mid), half_chord(chords, mid, end)
    ta, tb = chord_tones(a), chord_tones(b)
    if ta and tb and share_a >= BAR_SPLIT_SHARE and share_b >= BAR_SPLIT_SHARE and ta[0] != tb[0]:
        return [a, b]
    return None


def bars_field(downbeats, chords, drums, drum_hits, chords_ok, duration, beats=None):
    """The bars field, or None without downbeats. A bar's chord, scale and degree are None unless BTC made the chords;
    kick and snare are None without a drum layer. `beats` are the section model's beats, for beats per bar."""
    if not downbeats:
        return None
    meter = beats_per_bar(beats, downbeats)
    n = BAR_STEPS.get(meter, 16)
    spans = bar_spans(downbeats, duration)
    size = statistics.median(b - a for a, b in zip(downbeats, downbeats[1:]))
    edges = [a for a, _ in spans] + [max(spans[-1][1], spans[-1][0] + size)]  # a cut last bar keeps full-bar steps
    scales = bar_scales(spans, chords) if chords_ok else [(None, None)] * len(spans)
    done = bool(drums) and drums.get("status") == "done"
    steps = {c: bar_steps([h["time"] for h in drum_hits if h["class"] == c], edges, n) if done else None
             for c in ("kick", "snare")}
    return {"status": "done", "method": BAR_METHOD, "reliability": "reference_candidate", "beats_per_bar": meter, "steps": n,
            "extended_rows": len(spans) - (len(downbeats) - 1),
            "rows": [{"start": a, "end": b, "chord": top_chord(chords, a, b) if chords_ok else None,
                      "split": bar_split(chords, a, b) if chords_ok else None,
                      "scale": scales[i][0], "degree": scales[i][1],
                      **{c: steps[c].get(i, []) if done else None for c in steps}}
                     for i, (a, b) in enumerate(spans)]}


def swing_field(downbeats, drums, drum_hits, meter=None):
    """The swing field, or None without downbeats or a finished drum layer. The value is None under SWING_MIN_HITS.
    `meter` is the bars' beats per bar; None counts as 4."""
    if not downbeats or not drums or drums.get("status") != "done":
        return None
    off = []
    for h in drum_hits:
        i = bisect.bisect_right(downbeats, h["time"]) - 1
        if h["class"] in ("hihat", "cymbal") and 0 <= i < len(downbeats) - 1:
            pos = (h["time"] - downbeats[i]) / (downbeats[i + 1] - downbeats[i]) * (meter or 4) % 1
            if SWING_OFF_BEAT[0] < pos < SWING_OFF_BEAT[1]:
                off.append(pos)
    phase = round(statistics.median(off), 3) if len(off) >= SWING_MIN_HITS else None
    return {"status": "done", "method": SWING_METHOD, "reliability": "reference_candidate", "off_beat_phase": phase,
            "hits": len(off), "feel": None if phase is None else "swung" if phase >= SWING_AT else "straight"}


def stereo_energy(path, bands=False):
    """Per STEREO_WINDOW_S of a 16-bit stereo WAV: a row (LL, RR, LR), plus with bands one (mid, side) energy pair per band."""
    import numpy as np
    with wave.open(path, "rb") as w:
        if (w.getsampwidth(), w.getnchannels()) != (2, 2):
            raise ValueError("not 16-bit stereo")
        rate, hop = w.getframerate(), int(STEREO_WINDOW_S * w.getframerate())
        rows = []
        while True:
            b = w.readframes(hop)
            if not b:
                return rows
            x = np.frombuffer(b, dtype="<i2").astype(np.float64).reshape(-1, 2) / 32768.0
            left, right = x[:, 0], x[:, 1]
            row = [float(left @ left), float(right @ right), float(left @ right)]
            if bands:
                f = np.fft.rfftfreq(len(x), 1.0 / rate)
                # one-sided spectra; only the ratio of mid to side in a band is used
                mid, side = np.abs(np.fft.rfft((left + right) / 2.0)) ** 2, np.abs(np.fft.rfft((left - right) / 2.0)) ** 2
                for _, lo, hi in STEREO_BANDS:
                    k = (f >= lo) & (f < hi) if hi else f >= lo
                    row += [float(mid[k].sum()), float(side[k].sum())]
            rows.append(row)


def stereo_lufs(path):
    """(integrated LUFS, loudness range) of a 16-bit stereo WAV; None for a value that is not finite."""
    import essentia.standard as es
    import numpy as np
    with wave.open(path, "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32).reshape(-1, 2) / 32768.0
        rate = float(w.getframerate())
    _, _, integrated, lra = es.LoudnessEBUR128(sampleRate=rate)(np.ascontiguousarray(x))
    return tuple(round(float(v), 2) if math.isfinite(float(v)) else None for v in (integrated, lra))


def read_mono(path):
    """(samples, rate) of a 16-bit stereo WAV summed to mono."""
    import numpy as np
    with wave.open(path, "rb") as w:
        if (w.getsampwidth(), w.getnchannels()) != (2, 2):
            raise ValueError("not 16-bit stereo")
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64).reshape(-1, 2)
    return x.mean(axis=1) / 32768.0, rate


def tone_frames(x, rate):
    """(frame centre times, energies, log2 centroids in Hz; NaN where the band is empty)."""
    import numpy as np
    n = max(0, (len(x) - TONE_WIN) // TONE_HOP + 1)
    f = np.fft.rfftfreq(TONE_WIN, 1.0 / rate)
    band = (f >= TONE_LOW_HZ) & (f <= TONE_HIGH_HZ)
    window, energy, bright = np.hanning(TONE_WIN), np.zeros(n), np.full(n, np.nan)
    for first in range(0, n, TONE_CHUNK):
        idx = np.arange(first, min(n, first + TONE_CHUNK))
        p = np.abs(np.fft.rfft(np.stack([x[i * TONE_HOP:i * TONE_HOP + TONE_WIN] for i in idx]) * window, axis=1)) ** 2
        energy[idx] = p.sum(axis=1)
        total = p[:, band].sum(axis=1)
        ok = total > 0
        bright[idx[ok]] = np.log2((p[ok][:, band] * f[band]).sum(axis=1) / total[ok])
    return (np.arange(n) * TONE_HOP + TONE_WIN / 2.0) / rate, energy, bright


def tone_hit_rows(x, rate, times, kind):
    """(time, value) per hit: kick weight in dB, or snare log2 centroid in Hz."""
    import numpy as np
    size = int(TONE_HIT_S * rate)
    f = np.fft.rfftfreq(size, 1.0 / rate)
    low, band = f < TONE_KICK_HZ, (f >= TONE_LOW_HZ) & (f <= TONE_HIGH_HZ)
    rows = []
    for t in times:
        i = int(round(t * rate))
        if i < 0 or i + size > len(x):
            continue
        p = np.abs(np.fft.rfft(x[i:i + size])) ** 2
        if kind == "kick":
            if p.sum() > 0:
                rows.append((t, 10.0 * math.log10(max(float(p[low].sum()), 1e-30) / float(p.sum()))))
        elif p[band].sum() > 0:
            rows.append((t, math.log2(float((p[band] * f[band]).sum() / p[band].sum()))))
    return rows


def tone_measures(folder, drum_hits):
    """Everything tone needs later, when the sections are known and the stems are gone."""
    frames = {}
    for s in TONE_PARTS:
        x, rate = read_mono(os.path.join(folder, s + ".wav"))
        frames[s] = tone_frames(x, rate)
        if s == "drums":
            hits = {c: tone_hit_rows(x, rate, [h["time"] for h in drum_hits if h["class"] == c], c) for c in ("kick", "snare")}
    return {"frames": frames, "hits": hits}


def stereo_input(sep, vocal_reason):
    """Sets sep["stereo"] to (mix field or None, position field or None, measures). Runs before the scratch goes. Never raises."""
    def skip(reason, position_reason=None):
        sep["stereo"] = ({"status": "skipped", "reason": reason}, {"status": "skipped", "reason": position_reason or reason}, None)
    d = sep["scratch"]
    if not os.path.isfile(os.path.join(d, "full.wav")):
        return skip(vocal_reason or "separation_failed")
    wav = os.path.join(d, "stereo.wav")
    if run_tool(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16@%d" % SEP_RATE, "-c", "2", sep["source"], wav]):
        return skip("decode_failed")
    try:
        mix = stereo_energy(wav, bands=True)
        if not any(r[0] + r[1] - 2.0 * r[2] > 0 for r in mix):  # identical channels: a mono upload has no stereo to read
            return skip("mono_source")
        lufs, lra = stereo_lufs(wav)
    except Exception:
        return skip("mix_failed")
    parts = None
    try:
        out = os.path.join(d, "stereo_out")
        if not run_tool([sep["demucs"]] + list(STEREO_ARGS) + ["-o", out, wav]):
            parts = {s: stereo_energy(os.path.join(out, "stereo", s + ".wav")) for s in STEREO_PARTS}
    except Exception:
        parts = None
    tone = None
    if parts:
        try:
            tone = tone_measures(os.path.join(out, "stereo"), sep.get("drums", (None, []))[1])
        except Exception:
            tone = None
    position = None if parts else {"status": "skipped", "reason": "separation_failed"}
    sep["stereo"] = (None, position, {"mix": mix, "parts": parts, "lufs": lufs, "lra": lra, "tone": tone})


def _section_sum(rows, start, end):
    """Columns summed over the windows whose midpoint lies in [start, end) (else the window holding the midpoint), or None."""
    sel = [r for i, r in enumerate(rows) if start <= (i + 0.5) * STEREO_WINDOW_S < end]
    if not sel:
        i = int(((start + end) / 2.0) // STEREO_WINDOW_S)
        if i >= len(rows):
            return None
        sel = [rows[i]]
    return [sum(c) for c in zip(*sel)]


def _width_db(mid, side):
    if mid <= 0 and side <= 0:
        return None
    if side <= 0 or mid <= 0:
        return -STEREO_WIDTH_DB if side <= 0 else STEREO_WIDTH_DB
    return round(max(-STEREO_WIDTH_DB, min(STEREO_WIDTH_DB, 10.0 * math.log10(side / mid))), 2)


def _balance_width(ll, rr, lr):
    """(balance, width_db), or (None, None) for silence."""
    if ll + rr <= 0:
        return None, None
    return round((rr - ll) / (ll + rr), 3), _width_db(ll + rr + 2.0 * lr, ll + rr - 2.0 * lr)


def mix_row(sec, rows):
    c = _section_sum(rows, sec["start"], sec["end"])
    out = {"start": sec["start"], "end": sec["end"], "label": sec["label"], "balance": None, "width_db": None}
    out.update((b[0], None) for b in STEREO_BANDS)
    if c is None or sec["label"] == SILENCE_LABEL:  # 0.18.1: leftover noise there has a side and a width, but they mean nothing
        return out
    out["balance"], out["width_db"] = _balance_width(c[0], c[1], c[2])
    for i, b in enumerate(STEREO_BANDS):
        out[b[0]] = _width_db(c[3 + 2 * i], c[4 + 2 * i])
    return out


def position_row(sec, mix, parts):
    m = _section_sum(mix, sec["start"], sec["end"])
    out = {"start": sec["start"], "end": sec["end"], "label": sec["label"]}
    if sec["label"] == SILENCE_LABEL:
        out.update((s, None) for s in STEREO_PARTS)
        return out
    for s in STEREO_PARTS:
        c, v = _section_sum(parts[s], sec["start"], sec["end"]), None
        if c is not None and m is not None and m[0] + m[1] > 0 and c[0] + c[1] > 0:
            level = 10.0 * math.log10((c[0] + c[1]) / (m[0] + m[1]))
            if level >= STEREO_PART_DB:
                pan, width = _balance_width(c[0], c[1], c[2])
                v = {"pan": pan, "width_db": width, "level_db": round(level, 2)}
        out[s] = v
    return out


def stereo_fields(sep, sections):
    """(mix field or None, position field or None); both None without separation configured."""
    if not sep or "stereo" not in sep:
        return None, None
    mix, position, m = sep["stereo"]
    if m is None:
        return mix, position
    mix = {"status": "done", "method": MIX_METHOD, "reliability": "reference_candidate", "lufs": m["lufs"],
           "loudness_range": m["lra"], "sections": [mix_row(s, m["mix"]) for s in sections]}
    if position is None:
        position = {"status": "done", "method": PART_POSITION_METHOD, "reliability": "low_reference", "threshold_db": STEREO_PART_DB,
                    "sections": [position_row(s, m["mix"], m["parts"]) for s in sections]}
    return mix, position


def _tone_value(frames, start, end):
    """Hz at the median log2 centroid of the section's frames within 30 dB of its loudest, or None."""
    import numpy as np
    t, e, b = frames
    k = (t >= start) & (t < end)
    if not k.any() or e[k].max() <= 0:
        return None
    k &= (e >= e[k].max() * TONE_GATE) & np.isfinite(b)
    return round(2.0 ** float(np.median(b[k])), 1) if k.any() else None


def _hit_median(rows, start, end):
    import numpy as np
    v = [x for t, x in rows if start <= t and t + TONE_HIT_S <= end]
    return float(np.median(v)) if len(v) >= TONE_MIN_HITS else None


def tone_row(sec, m):
    out = {"start": sec["start"], "end": sec["end"], "label": sec["label"], "vocals_brightness_hz": None, "drums_brightness_hz": None,
           "other_brightness_hz": None, "kick_weight_db": None, "snare_brightness_hz": None}
    if sec["label"] == SILENCE_LABEL:
        return out
    start, end = sec["start"], sec["end"]
    mix = _section_sum(m["mix"], start, end)
    for s in TONE_PARTS:
        c = _section_sum(m["parts"][s], start, end)
        if not (mix and c and mix[0] + mix[1] > 0 and c[0] + c[1] > 0 and 10.0 * math.log10((c[0] + c[1]) / (mix[0] + mix[1])) >= STEREO_PART_DB):
            continue
        out[s + "_brightness_hz"] = _tone_value(m["tone"]["frames"][s], start, end)
        if s == "drums":
            kick, snare = _hit_median(m["tone"]["hits"]["kick"], start, end), _hit_median(m["tone"]["hits"]["snare"], start, end)
            out["kick_weight_db"] = None if kick is None else round(kick, 2)
            out["snare_brightness_hz"] = None if snare is None else round(2.0 ** snare, 1)
    return out


def tone_field(sep, sections):
    """The tone field, or None without separation configured."""
    if not sep or "stereo" not in sep:
        return None
    mix, position, m = sep["stereo"]
    if m is None or position:
        return {"status": "skipped", "reason": (mix or position)["reason"] if m is None else position["reason"]}
    if not m.get("tone"):
        return {"status": "skipped", "reason": "tone_failed"}
    return {"status": "done", "method": TONE_METHOD, "reliability": "low_reference", "sections": [tone_row(s, m) for s in sections]}


def section_segments(sep, duration):
    """(segments, None) or (None, reason). The scratch already holds full.wav and any stems made so far."""
    model = sep["sections_model"]
    root = os.path.dirname(model)
    if os.path.basename(model) != ALLIN1_WEIGHTS[0][0]:
        return None, "models_invalid"
    for path, sha, cap in [(os.path.join(root, n), h, ALLIN1_WEIGHT_MAX_BYTES) for n, h in ALLIN1_WEIGHTS] + [
            (ALLIN1_CODE, ALLIN1_CODE_SHA256, ALLIN1_CODE_MAX_BYTES)]:
        if read_pinned(path, sha, cap, keep=False)[0]:
            return None, "models_invalid"
    d = sep["scratch"]
    stems = os.path.join(d, "full")
    for s in MONO_STEMS:
        if not mono_stem(sep, s):
            return None, "separation_failed"
    out = os.path.join(d, "sections.json")
    if run_tool([sep["sections_python"], "-I", ALLIN1_CODE, root, stems, out]):
        return None, "sections_failed"
    with open(out) as f:
        got = json.load(f)
    if not isinstance(got, dict):
        return None, "sections_failed"
    segs = clean_segments(got.get("segments"), duration)
    if segs is None:
        return None, "sections_failed"
    sep["downbeats"] = clean_downbeats(got.get("downbeats"), duration)
    sep["model_beats"] = clean_beats(got.get("beats"), duration)
    return (None, "too_many_sections") if len(segs) > SEC_MAX else (segs, None)


def wav_rms(path, window_s):
    """(RMS of each window, RMS of the whole file) of a 16-bit WAV, channels averaged. The last window may be short."""
    import numpy as np
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("not 16-bit")
        ch, hop = w.getnchannels(), int(window_s * w.getframerate())
        out, total, count = [], 0.0, 0
        while True:
            b = w.readframes(hop)
            if not b:
                return out, math.sqrt(total / max(count, 1))
            x = np.frombuffer(b, dtype="<i2").astype(np.float64).reshape(-1, ch).mean(axis=1) / 32768.0
            power = float(np.sum(x * x))
            out.append(math.sqrt(power / len(x)))
            total, count = total + power, count + len(x)


def part_levels(d):
    """{part: [window active?] or None when that stem is absent or unreadable}, from scratch d."""
    floor = wav_rms(os.path.join(d, "full.wav"), PART_WINDOW_S)[1] * 10 ** (PART_THRESHOLD_DB / 20.0)
    out = {}
    for s in PART_STEMS:
        try:
            out[s] = [0 < r >= floor for r in wav_rms(os.path.join(d, "full", s + ".wav"), PART_WINDOW_S)[0]]
        except (OSError, EOFError, ValueError, wave.Error):
            out[s] = None
    return out


def parts_input(sep, vocal_reason):
    """Sets sep["parts"] to (field or None, levels). Runs before the scratch goes. Never raises."""
    if not os.path.isfile(os.path.join(sep["scratch"], "full.wav")):
        sep["parts"] = ({"status": "skipped", "reason": vocal_reason or "separation_failed"}, None)
        return
    try:
        levels = part_levels(sep["scratch"])
    except Exception:
        levels = None
    if levels is None or all(v is None for v in levels.values()):
        sep["parts"] = ({"status": "skipped", "reason": vocal_reason or "levels_failed"}, None)
    else:
        sep["parts"] = (None, levels)


def _active_share(windows, start, end):
    """Share of the windows whose midpoint lies in [start, end) (else the window holding the midpoint)."""
    if windows is None:
        return None
    sel = [v for i, v in enumerate(windows) if start <= (i + 0.5) * PART_WINDOW_S < end]
    if not sel:
        i = int(((start + end) / 2.0) // PART_WINDOW_S)
        sel = [windows[i]] if i < len(windows) else [False]
    return round(sum(sel) / float(len(sel)), 3)


def _rate(times, start, end):
    """Events per second in [start, end), or None when the layer gave no rows to count."""
    if times is None:
        return None
    return round(sum(1 for t in times if start <= t < end) / max(end - start, 0.001), 2)


def part_rows(sections, levels, bass_times, other_times, drum_times):
    """One row per section: part activity 0..1, and note or hit rates. None = that layer was not available."""
    return [{"start": s["start"], "end": s["end"], "label": s["label"],
             "vocals": _active_share(levels["vocals"], s["start"], s["end"]),
             "bass": _active_share(levels["bass"], s["start"], s["end"]),
             "drums": _active_share(levels["drums"], s["start"], s["end"]),
             "bass_notes_per_s": _rate(bass_times, s["start"], s["end"]),
             "other_notes_per_s": _rate(other_times, s["start"], s["end"]),
             "drum_hits_per_s": _rate(drum_times, s["start"], s["end"])} for s in sections]


def parts_field(sep, sections, bass, bass_notes, other, other_notes, drums, drum_hits):
    """The parts field, or None without separation configured."""
    if not sep or "parts" not in sep:
        return None
    field, levels = sep["parts"]
    if field:
        return field
    def done(layer, rows, key):
        return [r[key] for r in rows] if layer and layer.get("status") == "done" else None
    return {"status": "done", "method": PART_METHOD, "reliability": "reference_candidate", "window_s": PART_WINDOW_S,
            "threshold_db": PART_THRESHOLD_DB,
            "sections": part_rows(sections, levels, done(bass, bass_notes, "start"), done(other, other_notes, "start"),
                                  done(drums, drum_hits, "time"))}


def sections_input(sep, n16, vocal_reason):
    """Sets sep["sections"] to (field, segments). Runs before the scratch goes. Never raises."""
    if not os.path.isfile(os.path.join(sep["scratch"], "full.wav")):
        sep["sections"] = ({"status": "skipped", "reason": vocal_reason or "separation_failed"}, [])
        return
    try:
        segs, reason = section_segments(sep, n16 / SRC_RATE)
    except Exception:
        segs, reason = None, "sections_failed"
    sep["sections"] = ({"status": "skipped", "reason": reason}, []) if reason else ({"status": "done"}, segs)


def btc_frames(model, wav, duration):
    """([(time, label, None)], None) or (None, reason). Never raises."""
    try:
        for path, sha, cap in ((model, BTC_SHA256, BTC_MAX_BYTES), (BTC_CODE, BTC_CODE_SHA256, BTC_CODE_MAX_BYTES)):
            if read_pinned(path, sha, cap, keep=False)[0]:
                return None, "models_invalid"
        with tempfile.TemporaryDirectory(prefix="mlchords-") as d:
            out = os.path.join(d, "chords.json")
            if run_tool([sys.executable, "-I", BTC_CODE, model, wav, out]):
                return None, "btc_failed"
            with open(out) as f:
                v = json.load(f)
        hop, labels = v["hop_s"], v["labels"]
        if not (0 < hop < 1 and 0 < len(labels) <= BTC_MAX_FRAMES and all(isinstance(x, str) and 0 < len(x) <= 16 for x in labels)):
            return None, "btc_failed"
        return [(min(i * hop, duration), x, None) for i, x in enumerate(labels)], None
    except Exception:
        return None, "btc_failed"


def key_names(tonic, scale):
    """Note names for a key, or None for an unknown tonic. Its relative major decides the side."""
    pc = PITCH_CLASS.get(tonic)
    if pc is None:
        return None
    major = (pc + (3 if scale == "minor" else 0)) % 12
    return NATURAL_NAMES if major == 0 else FLAT_NAMES if major in FLAT_KEY_MAJORS else SHARP_NAMES


def slash_pc(start, end, root_pc, quality, bass_notes):
    """The pitch class to write after the slash, or None. bass_notes are sorted by start."""
    tones = CHORD_TONES.get(quality)
    if tones is None or end <= start:
        return None
    by = {}
    for n in bass_notes:
        if n["start"] >= end:
            break
        overlap = min(end, n["end"]) - max(start, n["start"])
        if overlap > 0:
            by[n["midi"] % 12] = by.get(n["midi"] % 12, 0.0) + overlap
    if not by:
        return None
    pc = max(sorted(by), key=by.get)
    return pc if by[pc] / (end - start) >= SLASH_MIN_SHARE and (pc - root_pc) % 12 in tones else None


def add_chord_display(chords, key_sections, tonic, scale, bass_notes):
    """Sets "display" on every chord row; "label" stays. N, X and labels with an unknown root are copied as they are."""
    for c in chords:
        label = c["label"]
        root = label[:2] if label[1:2] in ("#", "b") else label[:1]
        root_pc = PITCH_CLASS.get(root)
        c["display"] = label
        if root_pc is None:
            continue
        mid = (c["start"] + c["end"]) / 2
        sec = next((s for s in key_sections if s["start"] <= mid < s["end"]), None)
        names = key_names(sec["tonic"], sec["scale"]) if sec else None
        names = names or key_names(tonic, scale) or SHARP_NAMES
        pc = slash_pc(c["start"], c["end"], root_pc, label[len(root):], bass_notes)
        c["display"] = names[root_pc] + label[len(root):] + ("" if pc is None else "/" + names[pc])


def mark_one_beat(chords, beats):
    """Marks the segments with no beat strictly inside them (ends are beats, compared with a 1 ms margin)."""
    for c in chords:
        if not any(c["start"] + 0.001 < b < c["end"] - 0.001 for b in beats):
            c["one_beat"] = True


def melody_input(sep, n16):
    """(8 ms pitch or None, vocal or None, separation field). None pitch = use Melodia on the mix. Never raises."""
    if sep is None:
        return None, None, {"status": "skipped", "reason": "not_configured"}
    if sep.get("source") is None:
        sep["bass"] = sep["other"] = sep["drums"] = sep["sections"] = ({"status": "skipped", "reason": "canonical_16k"}, [])
        sep["parts"] = ({"status": "skipped", "reason": "canonical_16k"}, None)
        sep["stereo"] = ({"status": "skipped", "reason": "canonical_16k"}, {"status": "skipped", "reason": "canonical_16k"}, None)
        sep["sing"] = ({"status": "skipped", "reason": "canonical_16k"}, [])
        return None, None, {"status": "skipped", "reason": "canonical_16k"}
    try:
        pitch, stem, reason = vocal_pitch(sep, n16)
    except Exception:
        pitch, stem, reason = None, None, "separation_failed"
    try:
        if sep.get("basic_pitch"):
            bass_input(sep, n16, reason)
            other_input(sep, n16, reason)
        if sep.get("drum_model"):
            drum_input(sep, n16, reason)
        if sep.get("sections_python"):
            sections_input(sep, n16, reason)
        parts_input(sep, reason)
        stereo_input(sep, reason)
        if sep.get("singing_python"):
            sing_input(sep, n16, reason)
    finally:
        shutil.rmtree(sep["scratch"], ignore_errors=True)
    return pitch, stem, {"status": "done"} if reason is None else {"status": "skipped", "reason": reason}


def melody(x16, duration, tonic, scale, sections, sep=None, separated=None, sing=None):
    """(melody dict, notes, warnings) for the 16 kHz source; never raises (failure -> status failed, no notes).
    separated: melody_input's result when the caller already ran it. sing: sing_input's (field, rows) when the singing
    model is configured; done rows replace the RMVPE notes, which stay the fallback (0.21.0)."""
    try:
        import essentia.standard as es
        import numpy as np
        vocal_pitch_8ms, stem, separation = separated or melody_input(sep, len(x16))
        vocal = stem is not None
        a = es.EqualLoudness(sampleRate=SRC_RATE)(stem if vocal else x16)
        if vocal:
            pitch = vocal_pitch_8ms
        else:
            pitch, _ = es.PredominantPitchMelodia(sampleRate=SRC_RATE, frameSize=MEL_FRAME, hopSize=MEL_HOP,
                                                  maxFrequency=MEL_MAX_FREQ)(a)
        pitch = np.asarray(pitch, dtype=np.float64)
        if not np.all(np.isfinite(pitch)):
            return {"status": "failed", "code": "non_finite"}, [], ["melody_failed: non_finite"]
        hop_s = MEL_HOP / float(SRC_RATE)
        voiced = [round(i * hop_s, 6) for i in np.nonzero(pitch > 0)[0].tolist()]
        notes = []
        if voiced:
            on, du, mi = es.PitchContourSegmentation(sampleRate=SRC_RATE, hopSize=MEL_HOP)(
                np.ascontiguousarray(smooth_pitch(pitch), dtype=np.float32), a)
            for o, d, m in zip(on, du, mi):
                o, d, m = float(o), float(d), float(m)
                if not (math.isfinite(o) and math.isfinite(d) and math.isfinite(m)):
                    if vocal:  # segmentation gives NaN pitch for a note with no voiced RMVPE frame
                        continue
                    return {"status": "failed", "code": "non_finite"}, [], ["melody_failed: non_finite"]
                s, e, mm = r3(min(max(o, 0.0), duration)), r3(min(max(o + d, 0.0), duration)), int(round(m))
                if e > s and 0 <= mm <= 127:
                    notes.append({"start": s, "end": e, "midi": mm, "note": note_name(mm)})
        notes.sort(key=lambda n: (n["start"], n["end"]))
        raw = len(notes)
        notes = merge_notes(notes)
        if vocal:
            notes = same_pitch_join(scoop_join(notes), MERGE_GAP)  # a dip inside a held note leaves two same-pitch pieces
        sung = vocal and sing is not None and sing[0].get("status") == "done"
        if sung:
            notes = [dict(n) for n in sing[1]]
            raw = len(notes)
        warns = []
        if vocal and sing is not None and sing[0].get("reason") in SING_FAIL_REASONS:
            warns.append("melody_singing_failed: %s, used RMVPE notes" % sing[0]["reason"])
        if separation.get("reason") in SEP_RETRY_REASONS:
            warns.append("melody_separation_failed: %s, used the mix method" % separation["reason"])
        if not notes:
            warns.append("no_melody: no pitched note was found")
        summary = melody_summary(notes, voiced, hop_s, len(pitch), tonic, scale, sections)
        summary["note_count_raw"] = raw
        summary["separation"] = separation
        if vocal:
            summary["method"] = MEL_METHOD_SING if sung else MEL_METHOD_VOCAL
        if sing is not None:
            summary["singing"] = sing[0]
        if summary["scale_degrees"] is None and notes:
            warns.append("melody_scale_degrees_unavailable: key tonic not recognized")
        if len(notes) > MELODY_MAX_NOTES:
            warns.append("melody_notes_capped: %d notes, first %d stored" % (len(notes), MELODY_MAX_NOTES))
            notes = notes[:MELODY_MAX_NOTES]
        return summary, notes, warns
    except Exception as e:  # never fail the worker because of the melody layer
        return ({"status": "failed", "code": "dependency_missing" if isinstance(e, (ImportError, AttributeError))
                 else "analysis_error"}, [], ["melody_failed: " + type(e).__name__])


def onset_envelope(audio):
    """Normalized (0..1) half-rectified L1 spectral flux per ONSET_HOP frame; frame i centred at i*ONSET_HOP."""
    import essentia.standard as es
    window, spectrum = es.Windowing(type="hann"), es.Spectrum()
    flux = es.Flux(norm="L1", halfRectify=True)
    env = [float(flux(spectrum(window(f))))
           for f in es.FrameGenerator(audio, frameSize=ONSET_FRAME, hopSize=ONSET_HOP, startFromZero=False)]
    if env:
        env[0] = 0.0  # first frame is a difference against nothing
    env = [x if math.isfinite(x) and x > 0 else 0.0 for x in env]
    top = max(env) if env else 0.0
    return [x / top for x in env] if top > 0 else env


def read_wav(path):
    import numpy as np
    try:
        with wave.open(path, "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) != (1, 2, SRC_RATE, "NONE"):
                raise Fail("invalid_audio", "expected PCM mono 16 kHz 16-bit WAV")
            n = w.getnframes()
            if n <= 0 or n > MAX_SECONDS * SRC_RATE:
                raise Fail("invalid_audio", "duration must be within 0..600 s")
            raw = w.readframes(n)
    except (wave.Error, EOFError, OSError) as e:
        raise Fail("invalid_audio", "unreadable WAV: " + type(e).__name__)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return x, len(x) / float(SRC_RATE)


def analyze(path, model=TEMPO_CNN_MODEL, words=None, words_reason=None, genre_dir=GENRE_DIR, sep=None, chord_model=None):
    import essentia
    import essentia.standard as es
    import numpy as np

    x16, duration = read_wav(path)
    if duration < MIN_SECONDS:
        raise Fail("too_short", "audio shorter than 1 s")
    warnings = []
    if duration < SHORT_SECONDS:
        warnings.append("short_audio: under 10 s, estimates are unreliable")
    audio = es.Resample(inputSampleRate=SRC_RATE, outputSampleRate=RATE, quality=1)(x16)
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if float(np.max(np.abs(audio))) < 1e-4:
        warnings.append("near_silent: estimates are not meaningful")

    bpm, ticks, conf, estimates, _ = es.RhythmExtractor2013(method="multifeature")(audio)
    bpm, conf = finite(bpm), finite(conf)
    beats = [r3(t) for t in ticks if 0.0 <= float(t) <= duration + 0.05][:MAX_BEATS]
    counts = {}
    for e in estimates:
        e = float(e)
        if math.isfinite(e) and e > 0:
            k = round(e, 1)
            counts[k] = counts.get(k, 0) + 1
    total = sum(counts.values())
    bpm_estimates = [{"bpm": k, "share": round(v / float(total), 3)}
                     for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_ESTIMATES]]
    if not beats:
        warnings.append("no_beats: no beat was detected")
    if conf < LOW_BPM_CONFIDENCE:
        warnings.append("low_bpm_confidence: below 1.5 on the native 0..5.32 scale")

    dte, dte_warn = double_tempo_evidence(onset_envelope(audio), ONSET_HOP / float(RATE), beats)
    if dte_warn:
        warnings.append(dte_warn)
    energy = energy_values(x16, duration)
    tc = tempo_cnn(x16, model, energy)
    gn = genre(x16, r3(duration), energy, genre_dir)

    tonic, scale, kstrength = es.KeyExtractor(profileType=KEY_PROFILE, sampleRate=RATE, frameSize=FRAME,
                                              hopSize=HOP)(audio)
    kstrength = finite(kstrength)
    if kstrength < LOW_KEY_STRENGTH:
        warnings.append("low_key_strength: below 0.5")
    rows, bad_ks = [], False
    for ws, we in key_windows(duration):
        seg = np.ascontiguousarray(audio[int(ws * RATE):int(we * RATE)], dtype=np.float32)
        if len(seg) < FRAME:
            continue
        t_, s_, k_ = es.KeyExtractor(profileType=KEY_PROFILE, sampleRate=RATE, frameSize=FRAME, hopSize=HOP)(seg)
        k_ = float(k_)
        if not math.isfinite(k_):
            k_, bad_ks = None, True
        rows.append((ws, we, str(t_), str(s_), k_))
    key_sections = merge_key_sections(rows)
    if bad_ks:
        warnings.append("key_section_strength_unavailable: non-finite native strength; section strength is null")

    window = es.Windowing(type="blackmanharris62")
    spectrum = es.Spectrum()
    peaks = es.SpectralPeaks(orderBy="magnitude", magnitudeThreshold=0.00001, minFrequency=20, maxFrequency=3500,
                             maxPeaks=60, sampleRate=RATE)
    hpcp = es.HPCP(size=12, sampleRate=RATE)
    mfcc = es.MFCC(inputSize=FRAME // 2 + 1, sampleRate=RATE, numberCoefficients=13)
    pcps, mfccs = [], []
    for frame in es.FrameGenerator(audio, frameSize=FRAME, hopSize=HOP, startFromZero=True):
        sp = spectrum(window(frame))
        f, m = peaks(sp)
        pcps.append(hpcp(f, m))
        mfccs.append(mfcc(sp)[1][1:])  # coefficient 0 (overall level) left out: energy is reported on its own
    chords, frame_count, btc_used = [], 0, False
    if chord_model:
        frames, reason = btc_frames(chord_model, path, duration)
        if frames:
            frame_count = len(merge_frames(frames, duration))
            chords = beat_chords(frames, beats, duration) if beats else merge_frames(frames, duration)
            if beats:
                mark_one_beat(chords, beats)
            btc_used = True
        else:
            warnings.append("chord_model_unavailable: " + reason)
    if pcps and not btc_used:
        labels, strengths = es.ChordsDetection(hopSize=HOP, sampleRate=RATE, windowSize=2.0)(
            essentia.array(np.array(pcps, dtype=np.float32)))
        step = HOP / float(RATE)
        frames, bad_strength = [], False
        for i, (lab, st) in enumerate(zip(labels, strengths)):
            st = float(st)
            if not math.isfinite(st):
                st, bad_strength = None, True
            frames.append((min(i * step, duration), str(lab)[:16], st))
        frame_count = len(merge_frames(frames, duration))
        chords = beat_chords(frames, beats, duration) if beats else merge_frames(frames, duration)
        if bad_strength:
            warnings.append("chord_strength_unavailable: non-finite native strength; segment strength is null")
    if not chords:
        warnings.append("no_chords: no chord segment was produced")

    step = HOP / float(RATE)
    separated = melody_input(sep, len(x16))  # stems first: sections can come from them, and melody needs sections
    pcp_rows = np.array(pcps, dtype=np.float64).reshape(-1, 12)
    mfcc_rows = np.array(mfccs, dtype=np.float64).reshape(-1, 12)
    frame_times = [min(i * step, duration) for i in range(len(pcps))]
    sec_model, sec_segs = sep.get("sections", (None, [])) if sep and sep.get("sections_python") else (None, [])
    if sec_model and sec_model.get("reason") in SEC_MODEL_FAIL_REASONS:
        warnings.append("sections_model_failed: %s, used the beat method" % sec_model["reason"])
    if sec_segs and pcps:
        sections, sec_warn, sec_debug = sections_from_segments(sec_segs, pcp_rows, mfcc_rows, frame_times, duration, energy,
                                                               chords), None, None
    else:
        sec_segs = []
        sections, sec_warn, sec_debug = structure(pcp_rows, mfcc_rows, frame_times, beats, duration, energy, chords)
    if sec_warn:
        warnings.append(sec_warn)
    mel, mel_notes, mel_warn = melody(x16, r3(duration), str(tonic), str(scale), sections, separated=separated,
                                      sing=sep.get("sing") if sep and sep.get("singing_python") else None)
    warnings.extend(mel_warn)
    bass, bass_notes = sep.get("bass", (None, [])) if sep and sep.get("basic_pitch") else (None, [])
    if bass and bass.get("reason") in BASS_FAIL_REASONS:
        warnings.append("bass_failed: " + bass["reason"])
    other, other_notes = sep.get("other", (None, [])) if sep and sep.get("basic_pitch") else (None, [])
    if other and other.get("reason") in OTHER_FAIL_REASONS:
        warnings.append("other_failed: " + other["reason"])
    drums, drum_hits = sep.get("drums", (None, [])) if sep and sep.get("drum_model") else (None, [])
    if drums and drums.get("reason") in DRUM_FAIL_REASONS:
        warnings.append("drums_failed: " + drums["reason"])
    add_chord_display(chords, key_sections, str(tonic), str(scale), bass_notes)
    parts = parts_field(sep, sections, bass, bass_notes, other, other_notes, drums, drum_hits)
    if parts and parts.get("reason") == "levels_failed":
        warnings.append("parts_failed: levels_failed")
    bars = bars_field((sep or {}).get("downbeats"), chords, drums, drum_hits, btc_used, duration, (sep or {}).get("model_beats"))
    kw = key_change_warning(key_sections, bars)
    if kw:
        warnings.append(kw)
    swing = swing_field((sep or {}).get("downbeats"), drums, drum_hits, bars and bars["beats_per_bar"])
    mix_field, positions = stereo_fields(sep, sections)
    tone = tone_field(sep, sections)
    for name, f in (("mix", mix_field), ("part_positions", positions), ("tone", tone)):
        if f and f.get("reason") in ("decode_failed", "mix_failed", "separation_failed", "tone_failed"):
            warnings.append("%s_failed: %s" % (name, f["reason"]))
    try:  # pure Python on the server's words; never fails the worker
        lyr, lyr_warn = lyric_repeats(words, r3(duration), words_reason)
    except Exception:
        lyr, lyr_warn = {"status": "failed", "code": "analysis_error"}, []
    warnings.extend(lyr_warn)

    return {
        "bpm": round(bpm, 2),
        "bpm_confidence": round(conf, 3),
        "bpm_confidence_scale": "RhythmExtractor2013 multifeature native, 0..~5.32, uncalibrated",
        "bpm_estimates": bpm_estimates,
        "bpm_alternatives": bpm_alternatives(round(bpm, 2)),
        "bpm_alternatives_note": ALT_NOTE,
        "beats": beats,
        "double_tempo_evidence": dte,
        "double_tempo_evidence_note": DTE_NOTE,
        "tempo_cnn": tc,
        "downbeats": ({"status": "done", "method": DOWNBEAT_METHOD, "reliability": "reference_candidate",
                       "count": len(bars["rows"]) - bars["extended_rows"] + 1} if bars else
                      {"status": "not_provided", "reason": "Essentia has no native downbeat tracker"}),
        "key": {"tonic": str(tonic), "scale": str(scale), "strength": round(kstrength, 3), "profile": KEY_PROFILE},
        "key_sections": key_sections,
        "key_change_count": max(0, len(key_sections) - 1),
        "key_section_window": KEY_WINDOW,
        "chords": chords,
        "chords_grid": "beat" if beats else "frame",
        "chord_frame_segment_count": frame_count,
        "chords_reliability": "reference_candidate" if btc_used else "low_reference",
        "chord_vocabulary": CHORD_VOCABULARY if btc_used else "major/minor triads (ChordsDetection)",
        "energy": {"unit": ENERGY_UNIT, "step_s": ENERGY_STEP, "values": energy},
        "energy_summary": energy_summary(energy, r3(duration)),
        "sections": sections,
        "sections_method": SEC_METHOD_ALLIN1 if sec_segs else SEC_METHOD,
        "sections_reliability": "reference_candidate" if sec_segs else "low_reference",
        **({"sections_model": sec_model} if sec_model else {}),
        "melody": mel,
        "melody_notes": mel_notes,
        **({"bass": bass, "bass_notes": bass_notes} if bass else {}),
        **({"other": other, "other_notes": other_notes} if other else {}),
        **({"drums": drums, "drum_hits": drum_hits} if drums else {}),
        **({"parts": parts} if parts else {}),
        **({"bars": bars} if bars else {}),
        **({"swing": swing} if swing else {}),
        **({"mix": mix_field} if mix_field else {}),
        **({"part_positions": positions} if positions else {}),
        **({"tone": tone} if tone else {}),
        "structure_debug": sec_debug,
        "lyric_repeats": lyr,
        "genre": gn,
        "sample_rate": RATE,
        "source_sample_rate": SRC_RATE,
        "duration": r3(duration),
        "clock": "seconds_from_audio_start",
        "tool": dict(TOOL, essentia_version=str(essentia.__version__)),
        "warnings": warnings,
    }


def main(argv):
    model, use_model, use_words, rest = TEMPO_CNN_MODEL, False, False, list(argv[1:])
    genre_dir, use_genre = GENRE_DIR, False
    sep, chord_model, budget = {}, None, None
    usage = ("usage: rhythm_worker.py [--tempo-cnn-model /abs/model.pb] [--genre-models /abs/dir] [--words-stdin] "
             "[--demucs /abs --rmvpe-model /abs --melody-scratch /abs [--melody-source /abs]] "
             "[--sections-python /abs --sections-model /abs] [--chord-model /abs] /absolute/path.wav")
    while len(rest) > 1:
        if rest[0] == "--tempo-cnn-model" and not use_model and len(rest) > 2 and os.path.isabs(rest[1]):
            model, use_model, rest = rest[1], True, rest[2:]
        elif rest[0] == "--genre-models" and not use_genre and len(rest) > 2 and os.path.isabs(rest[1]):
            genre_dir, use_genre, rest = rest[1], True, rest[2:]
        elif rest[0] == "--words-stdin" and not use_words:
            use_words, rest = True, rest[1:]
        elif rest[0] == "--time-budget" and not budget and len(rest) > 2 and re.fullmatch(r"[0-9]{1,4}(\.[0-9]{1,3})?", rest[1]) \
                and float(rest[1]) > 0:
            budget, rest = float(rest[1]), rest[2:]
        elif rest[0] == "--chord-model" and not chord_model and len(rest) > 2 and os.path.isabs(rest[1]):
            chord_model, rest = rest[1], rest[2:]
        elif SEP_FLAGS.get(rest[0]) and SEP_FLAGS[rest[0]] not in sep and len(rest) > 2 and os.path.isabs(rest[1]):
            sep[SEP_FLAGS[rest[0]]], rest = rest[1], rest[2:]
        else:
            fail_out("invalid_argument", usage)
    if sep and not {"demucs", "rmvpe", "scratch"} <= set(sep) or ("sections_python" in sep) != ("sections_model" in sep):
        fail_out("invalid_argument", usage)
    if len(rest) != 1 or not os.path.isabs(rest[0]):
        fail_out("invalid_argument", usage)
    words, words_reason = None, None
    if use_words:
        try:
            words, words_reason = parse_words(sys.stdin.buffer.read(WORDS_MAX_BYTES + 1))
        except OSError:
            words, words_reason = None, "words_unreadable"
    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(WALL_SECONDS)
    global START, BUDGET
    START, BUDGET = time.monotonic(), budget or SING_BUDGET
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, SING_CPU_SECONDS if sep.get("singing_python") else CPU_SECONDS))
    except (ValueError, OSError):
        pass
    try:
        out = analyze(rest[0], model, words, words_reason, genre_dir, sep or None, chord_model)
    except Fail as e:
        fail_out(e.code, e.message)
    except ImportError as e:
        fail_out("dependency_missing", "essentia or numpy unavailable: " + type(e).__name__)
    except Exception as e:  # never leak a traceback to the API
        fail_out("analysis_failed", type(e).__name__)
    sys.stdout.write(json.dumps(out, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
