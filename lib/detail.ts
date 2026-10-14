import { z } from "zod";

const time = z.number().finite().min(0).max(600);
const unit = z.number().finite().min(0).max(1);
const interval = { start: time, end: time };
const stage = z.enum(["complete", "partial", "failed", "skipped"]);
const warnings = z.array(z.string().max(500)).max(16);
const db = z.number().finite().nullable();
export const tempoEvidenceSchema = z.object({
  status: z.enum(["supported", "ambiguous", "variable", "insufficient"]),
  onsetCoverage: unit, gridSupport: unit, medianOffsetMs: z.number().finite().nonnegative().nullable(),
  segments: z.array(z.object({ ...interval, bpm: z.number().min(20).max(400).nullable(), score: unit })).max(80),
  reason: z.string().max(500),
});
export const acousticsSchema = z.object({
  method: z.string().max(300),
  windows: z.array(z.object({ ...interval, rms: z.number().finite().nonnegative(), rmsDbfs: db, peakDbfs: db, crestDb: db,
    activeFraction: unit, centroidHz: z.number().min(0).max(11025).nullable(), rolloff85Hz: z.number().min(0).max(11025).nullable(), flatness: unit.nullable(),
    bandBalance: z.object({ low: unit, mid: unit, high: unit }).nullable(), onsetRate: z.number().finite().nonnegative(),
  })).max(600),
  summary: z.object({ rmsDbfs: db, samplePeakDbfs: db, nearFullScaleFraction: unit, activeWindowDynamicRangeDb: db }),
  warnings,
});
export const rhythmSchema = z.object({
  method: z.string().max(300), bpm: z.number().min(20).max(400).nullable(),
  tempoCandidates: z.array(z.object({ bpm: z.number().min(20).max(400), score: unit })).max(8),
  beats: z.array(time).max(4000),
  onsets: z.array(z.object({ time, strength: unit, band: z.enum(["low", "mid", "high"]) })).max(6000),
  envelope: z.array(z.object({ time, low: unit, mid: unit, high: unit, rms: z.number().finite().min(0) })).max(2400),
  sections: z.array(z.object({ ...interval, label: z.string().max(100), energy: z.number().finite().min(0) })).max(80),
  key: z.object({ label: z.string().max(80).nullable(), score: unit }),
  chords: z.array(z.object({ ...interval, label: z.string().max(80), confidence: unit })).max(300),
  tempoEvidence: tempoEvidenceSchema.optional(), acoustics: acousticsSchema.optional(),
  warnings, truncated: z.object({ onsets: z.boolean(), envelope: z.boolean(), chords: z.boolean() }),
});
export type Rhythm = z.infer<typeof rhythmSchema>;
export const noteSchema = z.object({ ...interval, midi: z.number().finite().min(21).max(108), confidence: unit.nullable() });
export const wordSchema = z.object({ ...interval, text: z.string().min(1).max(200) });
const harmonicSource = z.enum(["note-events", "spectral-only"]);
const keyMode = z.enum(["major", "minor"]);
export const harmonySchema = z.object({
  method: z.string().max(300), source: harmonicSource,
  pitchClasses: z.array(z.object({ name: z.string().max(4), share: unit })).max(12),
  key: z.object({
    status: z.enum(["estimated", "ambiguous", "insufficient", "unavailable"]),
    label: z.string().max(80).nullable(), tonic: z.string().max(4).nullable(), mode: keyMode.nullable(), score: unit,
    candidates: z.array(z.object({ label: z.string().max(80), tonic: z.string().max(4), mode: keyMode, correlation: z.number().finite().min(-1).max(1) })).max(4),
    margin: z.number().finite().min(0).max(2), reason: z.string().max(500),
  }),
  chords: z.array(z.object({ ...interval, label: z.string().max(80), confidence: unit, source: harmonicSource, pitchClasses: z.array(z.string().max(4)).max(12) })).max(300),
  truncated: z.boolean(), warnings,
}).superRefine((h, ctx) => {
  const completeKey = h.key.label !== null && h.key.tonic !== null && h.key.mode !== null;
  const emptyKey = h.key.label === null && h.key.tonic === null && h.key.mode === null;
  if (h.key.status === "estimated" ? !completeKey : !emptyKey) ctx.addIssue({ code: "custom", message: "Key label must match its estimate/abstention status." });
});
export type Harmony = z.infer<typeof harmonySchema>;
export const featuresSchema = z.object({
  method: z.string().max(300),
  strongBeats: z.array(z.object({ time, strength: unit })).max(2000),
  offbeats: z.array(z.object({ time, strength: unit, beatPhase: unit })).max(2000),
  percussion: z.array(z.object({ time, strength: unit, kind: z.enum(["low-hit", "mid-hit", "high-hit"]) })).max(2000),
  dynamics: z.array(z.object({ ...interval, kind: z.enum(["build-up", "drop", "energy-fall"]), score: unit })).max(80),
  vocalDensity: z.array(z.object({ ...interval, wordsPerSecond: z.number().finite().nonnegative(), syllableProxyPerSecond: z.number().finite().nonnegative().nullable(), source: z.literal("transcript-proxy") })).max(2000),
  pitchSummary: z.object({ lowestMidi: z.number().finite().min(21).max(108).nullable(), highestMidi: z.number().finite().min(21).max(108).nullable(), noteCount: z.number().int().nonnegative().max(4000), pitchClasses: z.array(z.object({ name: z.string().max(4), share: unit })).max(12) }),
  warnings, truncated: z.object({ strongBeats: z.boolean(), offbeats: z.boolean(), percussion: z.boolean(), dynamics: z.boolean(), vocalDensity: z.boolean() }),
});
export const detailSchema = z.object({
  schema: z.literal("music-lens-detail/1"),
  rhythm: rhythmSchema,
  notes: z.object({ status: stage, mode: z.enum(["polyphonic", "solo-vocal"]).optional(), model: z.string().max(180), items: z.array(noteSchema).max(4000), truncated: z.boolean(), warnings }),
  lyrics: z.object({ status: stage, model: z.string().max(180), language: z.string().max(40), words: z.array(wordSchema).max(2000), truncated: z.boolean(), warnings }),
  features: featuresSchema.optional(), harmony: harmonySchema.optional(),
  warnings,
}).superRefine((d, ctx) => {
  for (const [key, items] of [["notes", d.notes.items], ["lyrics", d.lyrics.words], ["sections", d.rhythm.sections], ["chords", d.rhythm.chords], ["harmonyChords", d.harmony?.chords ?? []], ["dynamics", d.features?.dynamics ?? []], ["vocalDensity", d.features?.vocalDensity ?? []], ["tempoSegments", d.rhythm.tempoEvidence?.segments ?? []], ["acoustics", d.rhythm.acoustics?.windows ?? []]] as const) {
    if (items.some(v => v.end <= v.start)) ctx.addIssue({ code: "custom", message: `Invalid ${key} interval.` });
  }
});
export type DetailResult = z.infer<typeof detailSchema>;
