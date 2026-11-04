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
       --chord-model /abs/btc_model_large_voca.pt (0.7.0; chords from BTC instead of ChordsDetection)
       --basic-pitch-python /abs/python (0.8.0; with the Demucs options: a bass layer, see "Bass notes" below)
       --drum-model /abs/checkpoint.index (0.9.0; with the Demucs options: a drum layer, see "Drum hits" in README.md)
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
- downbeats: Essentia has no native downbeat tracker; reported as {"status":"not_provided"}.
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
import unicodedata
import wave

TOOL = {"name": "music-lens-rhythm-worker", "version": "0.11.0"}
SRC_RATE = 16000
RATE = 44100
MAX_SECONDS = 600.0
MIN_SECONDS = 1.0  # below this the analysis is refused
SHORT_SECONDS = 10.0  # below this a warning is added
WALL_SECONDS = 330  # self-bound in case the parent never kills us (server wall timeout is 300 s)
CPU_SECONDS = 600
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
MERGE_REVERSALS = 2  # sign changes an oscillation needs: A-B-A-B = 3 alternating moves = 2 sign changes
# Vocal melody (0.6.0, optional): Demucs on the full-band upload, then RMVPE on the vocal, instead of Melodia on the mix.
# Mix0 mean against Melodia (RPA / false voicing, 2026-11-03): 4 synthetic songs 84.5/27.9 vs 79.6/47.5, 40 human clips
# 95.8/37.1 vs 54.2/45.8. With 16 kHz input to Demucs RPA falls to 53.9, so band-limited audio keeps Melodia.
# 44.1 kHz mono files had -20.4..-27.6 dB above 8.2 kHz; 16 kHz resampled up had -50.6 dB.
# Batch 1 keeps Demucs at 8.6 GB for 600 s (34.1 GB with the automatic batch); the seed makes it repeatable.
SEP_RATE = 44100
SEP_BAND_HZ, SEP_BAND_MIN_DB = 8200.0, -40.0
DEMUCS_ARGS = ("-n", "htdemucs_ft", "--stem", "vocals", "-b", "1", "--seed", "0")
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
BASS_DEMUCS_ARGS = ("-n", "htdemucs_ft", "--stem", "bass", "-b", "1", "--seed", "0")
BASS_ONSETS_CODE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bass_onsets.py")
BASS_ONSETS_SHA256 = "3c0f91abb732b432463aea9fca4c7372fc164eba16179118da996be54994fd30"
BASS_ONSETS_MAX_BYTES = 16 * 1024
BASS_MIN_FRAMES, BASS_JOIN_FRAMES = 8, 20  # at RMVPE's 10 ms: shortest note 80 ms, same pitch joins across 200 ms
BASS_MAX_NOTES = 7500  # 600 s / the 80 ms shortest note
BASS_METHOD = ("Demucs htdemucs_ft bass at 44.1 kHz, RMVPE pitch on CPU, same-pitch notes split at Basic Pitch note "
               "starts. Pitch is the sounding pitch; note ends are weak.")
BASS_FAIL_REASONS = ("models_invalid", "decode_failed", "separation_failed", "rmvpe_failed", "onsets_failed")
# Drum hits (0.9.0, optional): Demucs drum stem, then ADTOF Frame_RNN. Measured on 23 songs of MDB Drums; tom is weak.
DRUM_DEMUCS_ARGS = ("-n", "htdemucs_ft", "--stem", "drums", "-b", "1", "--seed", "0")
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
ALLIN1_CODE_SHA256 = "e2055c108779bc5f3d4e36ae943d8dd75ffe076b3eed413307dd23e65d113117"
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
             "--basic-pitch-python": "basic_pitch", "--drum-model": "drum_model",
             "--sections-python": "sections_python", "--sections-model": "sections_model"}
MEL_METHOD_VOCAL = ("Demucs htdemucs_ft vocal at 44.1 kHz, then RMVPE threshold 0.03 on CPU in 30 s pieces; "
                    "same note segmentation and merge as the mix method. The vocal stem can include backing vocals.")
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
    if run_tool([sep["demucs"]] + list(DEMUCS_ARGS) + ["-o", d, full]):
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


def bass_stem_notes(sep, n16):
    """(rows, None) or (None, reason). The scratch already holds full.wav."""
    import numpy as np
    from scipy.io import wavfile
    from scipy.signal import resample_poly
    if read_pinned(BASS_ONSETS_CODE, BASS_ONSETS_SHA256, BASS_ONSETS_MAX_BYTES, keep=False)[0]:
        return None, "models_invalid"
    d = sep["scratch"]
    if run_tool([sep["demucs"]] + list(BASS_DEMUCS_ARGS) + ["-o", d, os.path.join(d, "full.wav")]):
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
        starts = json.load(f)
    if not (isinstance(starts, list) and all(isinstance(t, (int, float)) and math.isfinite(t) for t in starts)):
        return None, "onsets_failed"
    return bass_rows(f0, starts), None


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
    if run_tool([sep["demucs"]] + list(DRUM_DEMUCS_ARGS) + ["-o", d, os.path.join(d, "full.wav")]):
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
    for s in ("vocals", "bass", "drums", "other"):
        if not os.path.isfile(os.path.join(stems, s + ".wav")) and run_tool(
                [sep["demucs"], "-n", "htdemucs_ft", "--stem", s, "-b", "1", "--seed", "0", "-o", d, os.path.join(d, "full.wav")]):
            return None, "separation_failed"
    out = os.path.join(d, "sections.json")
    if run_tool([sep["sections_python"], "-I", ALLIN1_CODE, root, stems, out]):
        return None, "sections_failed"
    with open(out) as f:
        segs = clean_segments(json.load(f), duration)
    if segs is None:
        return None, "sections_failed"
    return (None, "too_many_sections") if len(segs) > SEC_MAX else (segs, None)


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
        sep["bass"] = sep["drums"] = sep["sections"] = ({"status": "skipped", "reason": "canonical_16k"}, [])
        return None, None, {"status": "skipped", "reason": "canonical_16k"}
    try:
        pitch, stem, reason = vocal_pitch(sep, n16)
    except Exception:
        pitch, stem, reason = None, None, "separation_failed"
    try:
        if sep.get("basic_pitch"):
            bass_input(sep, n16, reason)
        if sep.get("drum_model"):
            drum_input(sep, n16, reason)
        if sep.get("sections_python"):
            sections_input(sep, n16, reason)
    finally:
        shutil.rmtree(sep["scratch"], ignore_errors=True)
    return pitch, stem, {"status": "done"} if reason is None else {"status": "skipped", "reason": reason}


def melody(x16, duration, tonic, scale, sections, sep=None, separated=None):
    """(melody dict, notes, warnings) for the 16 kHz source; never raises (failure -> status failed, no notes).
    separated: melody_input's result when the caller already ran it."""
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
        warns = []
        if separation.get("reason") in SEP_RETRY_REASONS:
            warns.append("melody_separation_failed: %s, used the mix method" % separation["reason"])
        if not notes:
            warns.append("no_melody: no pitched note was found")
        summary = melody_summary(notes, voiced, hop_s, len(pitch), tonic, scale, sections)
        summary["note_count_raw"] = raw
        summary["separation"] = separation
        if vocal:
            summary["method"] = MEL_METHOD_VOCAL
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
    if len(key_sections) > 1:
        warnings.append("key_changes: %d sections on %g s windows (DSP estimate)" % (len(key_sections), KEY_WINDOW))

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
    mel, mel_notes, mel_warn = melody(x16, r3(duration), str(tonic), str(scale), sections, separated=separated)
    warnings.extend(mel_warn)
    bass, bass_notes = sep.get("bass", (None, [])) if sep and sep.get("basic_pitch") else (None, [])
    if bass and bass.get("reason") in BASS_FAIL_REASONS:
        warnings.append("bass_failed: " + bass["reason"])
    drums, drum_hits = sep.get("drums", (None, [])) if sep and sep.get("drum_model") else (None, [])
    if drums and drums.get("reason") in DRUM_FAIL_REASONS:
        warnings.append("drums_failed: " + drums["reason"])
    add_chord_display(chords, key_sections, str(tonic), str(scale), bass_notes)
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
        "downbeats": {"status": "not_provided", "reason": "Essentia has no native downbeat tracker"},
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
        **({"drums": drums, "drum_hits": drum_hits} if drums else {}),
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
    sep, chord_model = {}, None
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
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
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
