import { z } from "zod";

const finite = z.number().finite();
const hash = z.string().regex(/^[a-f0-9]{64}$/);
const time = finite.min(0).max(600);
const warnings = z.array(z.string().max(1000)).max(100);
const relativeFile = z.string().min(1).max(300).refine(v => /^[A-Za-z0-9_.\/-]+$/.test(v) && v.split("/").every(p => p !== "" && p !== "." && p !== ".."), "Only portable relative model filenames are allowed.");
const modelFiles = z.array(z.object({ file: relativeFile, sha256: hash, bytes: finite.int().nonnegative().max(16 * 1024 ** 3) }).strict()).max(4096);
const recipe = z.object({ window_seconds: finite.nonnegative().max(5), hop_seconds: finite.nonnegative().max(2.5), minimum_tail_seconds: finite.nonnegative().max(3) }).strict();
const emotionProvenance = z.object({
  mode: z.enum(["none", "voice", "music"]), settings: recipe, worker_sha256: hash.optional(),
  availability: z.enum(["ready", "unavailable"]).optional(), reason: z.string().regex(/^[a-z_]{1,100}$/).optional(),
  model_files: modelFiles.optional(),
  engine_versions: z.record(z.string().regex(/^[A-Za-z0-9_.+-]{1,80}$/), z.string().regex(/^[A-Za-z0-9_.+-]{1,128}$/)).optional(),
}).strict();
const provenance = z.object({
  tool_version: z.enum(["0.2.0", "0.2.1"]), engine: z.literal("mlx-whisper"), engine_version: z.string().regex(/^[A-Za-z0-9_.+-]{1,128}$/),
  model_files: modelFiles.min(1), source_sha256: hash,
  settings: z.object({ language: z.enum(["auto", "en", "ko", "ja"]), word_timestamps: z.literal(true), temperature: z.literal(0) }).strict(),
  runner_sha256: hash, emotion: emotionProvenance, failure_attempt: z.string().regex(/^[0-9a-f]{32}$/).optional(),
}).strict();
export const VOICE_LABELS = ["生气/angry", "厌恶/disgusted", "恐惧/fearful", "开心/happy", "中立/neutral", "其他/other", "难过/sad", "吃惊/surprised", "<unk>"] as const;
export const MUSIC_LABELS = ["passionate, rousing, confident, boisterous, rowdy", "rollicking, cheerful, fun, sweet, amiable/good natured", "literate, poignant, wistful, bittersweet, autumnal, brooding", "humorous, silly, campy, quirky, whimsical, witty, wry", "aggressive, fiery, tense/anxious, intense, volatile, visceral"] as const;
const nativeDimension = finite.min(-100).max(100); // Preserve native regression values; do not clamp to the approximate display scale.
const commonWindow = { start: time, end: time, arousal: nativeDimension, valence: nativeDimension };
const voiceWindow = z.object({ ...commonWindow, dominance: nativeDimension,
  category_labels: z.array(z.string().max(512)).length(9), category_scores: z.array(finite.min(0).max(1)).length(9), dominant_emotion: z.string().max(512),
}).strict();
const musicWindow = z.object({ ...commonWindow, mood_labels: z.array(z.string().max(512)).length(5), mood_scores: z.array(finite.min(0).max(1)).length(5), top_mood: z.string().max(512) }).strict();
const emotionCommon = {
  status: z.enum(["complete", "partial", "failed", "unavailable", "not_requested"]), warnings,
  ...recipe.shape, coverage: z.object({ covered_seconds: time, uncovered_seconds: time }).strict(), score_semantics: z.literal("native_uncalibrated"),
};
const emotion = z.discriminatedUnion("mode", [
  z.object({ ...emotionCommon, mode: z.literal("none"), windows: z.array(z.never()).max(0), accuracy: z.literal("unmeasured"), dimension_scale: z.literal("model native, approximately 1..9") }).strict(),
  z.object({ ...emotionCommon, mode: z.literal("voice"), windows: z.array(voiceWindow).max(600), accuracy: z.literal("unvalidated_for_singing"), dimension_scale: z.literal("model native, approximately 0..1") }).strict(),
  z.object({ ...emotionCommon, mode: z.literal("music"), windows: z.array(musicWindow).max(600), accuracy: z.literal("unmeasured"), dimension_scale: z.literal("model native, approximately 1..9") }).strict(),
]);
const word = z.object({ text: z.string().min(1).max(1024).refine(v => !!v.trim()), start: time, end: finite.min(0).max(600.05), probability: finite.min(0).max(1).optional() }).strict();
const timingNote = z.object({ code: z.literal("native_end_overshoot"), word_count: finite.int().min(1).max(40000), max_overshoot_seconds: finite.positive().max(0.05 + 1e-9), tolerance_seconds: z.literal(0.05) }).strict();

export const localAnalysisSchema = z.object({
  schema_version: z.literal("music-lens-local/2"), result_id: hash, state: z.enum(["complete", "partial"]),
  audio: z.object({ duration_seconds: finite.positive().max(600), sample_rate_hz: z.literal(16000), channels: z.literal(1), sample_format: z.literal("signed16_pcm"), frames: finite.int().positive().max(9600000), sha256: hash }).strict(),
  clock: z.literal("seconds_from_audio_start"), provenance, detected_language: z.string().max(32).nullable(),
  word_count: finite.int().nonnegative().max(40000), warnings, timing_notes: z.array(timingNote).max(1), probability_semantics: z.literal("native_uncalibrated"),
  created_unix: finite.nonnegative(), words: z.array(word).max(40000), asr_status: z.enum(["complete", "partial", "failed"]), emotion,
}).strict().superRefine((v, ctx) => {
  const fail = (message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, message });
  const duration = v.audio.duration_seconds;
  if (Math.abs(duration - v.audio.frames / 16000) > 1e-9 || v.provenance.source_sha256 !== v.audio.sha256) fail("Audio duration/hash disagrees with provenance.");
  if (v.word_count !== v.words.length || (v.asr_status === "failed") !== !v.words.length) fail("ASR state/count disagrees with retained words.");
  let previous = -1, overshoots = 0, maximumOvershoot = 0;
  for (const row of v.words) {
    if (row.start < previous || row.start > duration || row.end < row.start || row.end > duration + 0.05) fail("Invalid or reordered native word interval.");
    previous = row.start;
    if (row.end > duration) { overshoots++; maximumOvershoot = Math.max(maximumOvershoot, row.end - duration); }
  }
  const note = v.timing_notes[0];
  if (overshoots ? !note || note.word_count !== overshoots || Math.abs(note.max_overshoot_seconds - maximumOvershoot) > 1e-9 : !!note) fail("Native overshoot note disagrees with word timing.");
  const e = v.emotion, p = v.provenance.emotion;
  if (v.provenance.failure_attempt && v.asr_status !== "failed" && e.status !== "failed") fail("Failure attempt requires an explicitly failed layer.");
  const settings = e.mode === "voice" ? [4, 2, 1] : e.mode === "music" ? [5, 2.5, 3] : [0, 0, 0];
  const keys = ["window_seconds", "hop_seconds", "minimum_tail_seconds"] as const;
  if (p.mode !== e.mode || keys.some((key, i) => e[key] !== settings[i] || p.settings[key] !== settings[i])) fail("Emotion mode/window recipe differs from provenance.");
  if (e.mode !== "none" && !p.worker_sha256) fail("Requested emotion requires a worker hash.");
  if (p.availability === "ready" && (!p.model_files?.length || !p.engine_versions || !Object.keys(p.engine_versions).length)) fail("Ready emotion provenance is incomplete.");
  if ((e.mode === "none") !== (e.status === "not_requested") || (["failed", "unavailable", "not_requested"].includes(e.status) && e.windows.length) || (["complete", "partial"].includes(e.status) && !e.windows.length)) fail("Emotion state disagrees with windows.");
  if (["complete", "partial"].includes(e.status) && p.availability !== "ready") fail("Executed emotion requires ready provenance.");
  let covered = 0, right = 0, lastStart = -1;
  for (const row of e.windows) {
    if (row.start <= lastStart || row.end <= row.start || row.end > duration || row.end - row.start < settings[2] - 1e-8 || Math.abs(row.start / settings[1] - Math.round(row.start / settings[1])) > 1e-7 || Math.abs(row.end - Math.min(row.start + settings[0], duration)) > 1e-7) fail("Emotion interval differs from ordered window recipe.");
    const labels = "category_labels" in row ? row.category_labels : row.mood_labels;
    const scores = "category_scores" in row ? row.category_scores : row.mood_scores;
    const top = "dominant_emotion" in row ? row.dominant_emotion : row.top_mood;
    const wanted = e.mode === "voice" ? VOICE_LABELS : MUSIC_LABELS;
    if (labels.some((label, i) => label !== wanted[i]) || top !== labels[scores.indexOf(Math.max(...scores))]) fail("Native labels/score ordering or strongest label is inconsistent.");
    covered += Math.max(0, row.end - Math.max(row.start, right));
    right = Math.max(right, row.end); lastStart = row.start;
  }
  const uncovered = Math.max(0, duration - covered);
  if (Math.abs(e.coverage.covered_seconds - covered) > 1e-7 || Math.abs(e.coverage.uncovered_seconds - uncovered) > 1e-7) fail("Emotion coverage disagrees with windows.");
  if (e.status === "complete" && (e.warnings.length || uncovered > 1e-7)) fail("Complete emotion has warnings or uncovered time.");
  const complete = v.asr_status === "complete" && ["complete", "not_requested"].includes(e.status);
  if ((v.state === "complete") !== complete || (v.asr_status === "failed" && !["complete", "partial"].includes(e.status))) fail("Overall state disagrees with independent analysis layers.");
});
export type LocalAnalysis = z.infer<typeof localAnalysisSchema>;
export type EmotionWindow = z.infer<typeof voiceWindow> | z.infer<typeof musicWindow>;
export const LOCAL_LIMIT = "Native model scores are uncalibrated estimates. Speech emotion is unvalidated for singing; music-mood accuracy is unmeasured. Missing windows are unknown, not neutral. Imported hashes do not authenticate the source or prove model execution.";

export function localSummary(local?: LocalAnalysis) {
  if (!local) return null;
  const e = local.emotion;
  const rows: EmotionWindow[] = e.windows;
  let peak: EmotionWindow | null = null;
  for (const row of rows) if (!peak || row.arousal > peak.arousal) peak = row;
  return { result_id: local.result_id, state: local.state, clock: local.clock, asr_status: local.asr_status, word_count: local.word_count,
    cacheable: !local.provenance.failure_attempt, retry_recommended: !!local.provenance.failure_attempt,
    emotion: { mode: e.mode, status: e.status, window_count: e.windows.length, coverage: e.coverage, dimension_scale: e.dimension_scale,
      highest_observed_arousal: peak ? { start: peak.start, end: peak.end, arousal: peak.arousal, label: "dominant_emotion" in peak ? peak.dominant_emotion : peak.top_mood } : null,
      warnings: e.warnings }, warnings: local.warnings, timing_notes: local.timing_notes, limits: LOCAL_LIMIT };
}

export function localTimeline(local: LocalAnalysis | undefined, start: number, end: number) {
  // Preserve a native point word exactly at audio EOF; no later half-open page could recover it.
  const overlap = (row: { start: number; end: number }) => (row.start < end || (row.start === row.end && row.start === end && end === local?.audio.duration_seconds)) && (row.end > start || (row.end === row.start && row.start >= start));
  const bounded = <T>(rows: T[]) => ({ items: rows.slice(0, 200), total: rows.length, truncated: rows.length > 200 });
  const emotionRows: EmotionWindow[] = local?.emotion.windows ?? [];
  return { localWords: { status: local?.asr_status ?? "unavailable", ...bounded(local?.words.filter(overlap) ?? []), probability_semantics: local?.probability_semantics ?? null },
    localEmotion: { mode: local?.emotion.mode ?? "none", status: local?.emotion.status ?? "unavailable", ...bounded(emotionRows.filter(overlap)),
      dimension_scale: local?.emotion.dimension_scale ?? null, coverage: local?.emotion.coverage ?? null, warnings: local?.emotion.warnings ?? [], limits: LOCAL_LIMIT } };
}
