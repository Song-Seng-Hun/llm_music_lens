import { z } from "zod";
import { detailSchema } from "./detail.ts";
import { wordContexts } from "./alignment.ts";

export const SCHEMA_VERSION = "music-lens/1" as const;
export const MAX_DURATION = 600;
export const MAX_FILE_BYTES = 12 * 1024 * 1024;
// Leave space for row metadata below D1's 2,000,000-byte row limit.
export const MAX_REPORT_BYTES = 1_900_000;
const time = z.number().finite().min(0).max(MAX_DURATION);
const interval = { start: time, end: time };
const text = z.string().max(2000);
const timed = <T extends z.ZodRawShape>(shape: T) => z.object({ ...interval, ...shape })
  .refine((v) => v.end !== undefined && v.start !== undefined && v.end > v.start, "구간의 끝은 시작보다 뒤여야 합니다.");

export const musicalAnalysisSchema = z.object({
  summary: text,
  styleTags: z.array(z.string().max(60)).max(10),
  instruments: z.array(z.string().max(100)).max(16),
  bpm: z.object({ value: z.number().min(20).max(400).nullable(), alternatives: z.array(z.number().min(20).max(400)).max(4), reason: text }),
  key: z.object({ value: z.string().max(80).nullable(), reason: text }),
  sections: z.array(timed({ label: z.string().max(80), kind: z.enum(["singing", "rap", "instrumental", "mixed", "unknown"]), description: text })).max(40),
  moments: z.array(timed({ title: z.string().max(100), observation: text, interpretation: text })).max(12),
  vocals: z.array(timed({ delivery: z.enum(["singing", "rap", "vocalizing", "mixed", "unknown"]), description: text, syllablesPerSecond: z.number().min(0).max(30).nullable() })).max(40),
  chords: z.array(timed({ label: z.string().max(80), reason: z.string().max(250) })).max(80),
  limitations: z.array(z.string().max(500)).max(20),
});
export type MusicalAnalysis = z.infer<typeof musicalAnalysisSchema>;

export const signalSchema = z.object({
  duration: z.number().positive().max(MAX_DURATION),
  waveform: z.array(z.number().finite()).max(800),
  energy: z.array(z.object({ time, rms: z.number().finite().min(0) })).max(12000),
  tempo: z.object({
    bpm: z.number().positive().max(400).nullable(),
    candidates: z.array(z.object({ bpm: z.number().positive().max(400), score: z.number().finite() })).max(12),
    beats: z.array(time).max(4000),
    segments: z.array(z.object({ ...interval, bpm: z.number().positive().max(400).nullable() })).max(200),
    status: z.string().max(100),
  }),
  pitch: z.array(z.object({ ...interval, hz: z.number().positive().max(10000), midi: z.number().finite().min(0).max(140), note: z.string().max(20), confidence: z.number().finite() })).max(12000),
  key: z.object({ label: z.string().max(80).nullable(), status: z.string().max(100) }),
  chords: z.array(z.object({ ...interval, label: z.string().max(80), confidence: z.number().finite() })).max(300),
  warnings: z.array(z.string().max(1000)).max(20),
  method: z.string().max(300),
});
export type SignalResult = z.infer<typeof signalSchema>;

export const sourceSchema = z.object({
  kind: z.enum(["file", "youtube", "synthetic"]),
  title: z.string().min(1).max(300),
  youtubeId: z.string().regex(/^[A-Za-z0-9_-]{11}$/).optional(),
  url: z.string().max(200).optional(),
  sha256: z.string().regex(/^[a-f0-9]{64}$/).optional(),
  originalSha256: z.string().regex(/^[a-f0-9]{64}$/).optional(),
  duration: z.number().positive().max(MAX_DURATION).optional(),
});
export const reportSchema = z.object({
  schema: z.literal(SCHEMA_VERSION),
  id: z.string().uuid(),
  createdAt: z.string().datetime(),
  source: sourceSchema,
  signal: signalSchema.nullable(),
  interpretation: musicalAnalysisSchema.nullable(),
  detail: detailSchema.optional(),
  provenance: z.object({
    mode: z.enum(["local-signal", "model-estimate", "combined", "imported"]),
    model: z.string().max(100).optional(),
    inputStatus: z.string().max(200),
    audioTokens: z.number().int().nonnegative().optional(),
    videoTokens: z.number().int().nonnegative().optional(),
    totalTokens: z.number().int().nonnegative().optional(),
    accuracy: z.literal("unvalidated"),
  }),
});
export type MusicReport = z.infer<typeof reportSchema>;

export function reportDuration(report: MusicReport): number {
  return report.signal?.duration ?? report.source.duration ?? Math.max(0, ...(report.interpretation?.sections.map(s => s.end) ?? []));
}

export function summaryOf(report: MusicReport) {
  return { id: report.id, title: report.source.title, source: report.source, createdAt: report.createdAt,
    bpm: report.detail ? report.detail.rhythm.bpm : report.signal ? report.signal.tempo.bpm : report.interpretation?.bpm.value ?? null,
    key: keyOf(report),
    summary: report.interpretation?.summary ?? (report.detail ? "브라우저 모델의 음표·가사와 리듬·화음 추정입니다. 음악적 정확도와 스타일 해석은 미검증입니다." : "로컬 신호 추정만 포함합니다. 스타일·가사·랩·화음 해석은 미확인입니다."),
    styles: report.interpretation?.styleTags ?? [], provenance: report.provenance,
    available: { signal: Boolean(report.signal), interpretation: Boolean(report.interpretation), detail: Boolean(report.detail), notes: report.detail?.notes.status ?? "unavailable", lyrics: report.detail?.lyrics.status ?? "unavailable" } };
}

// A new analysis's explicit unknown is not replaced with an older interpretation.
export function keyOf(report: MusicReport): string | null {
  if (report.detail?.harmony) return report.detail.harmony.key.label;
  if (report.detail) return report.detail.rhythm.key.label;
  return report.interpretation?.key.value ?? report.signal?.key.label ?? null;
}

export function timelineOf(report: MusicReport, start: number, end: number) {
  const overlap = (v: { start: number; end: number }) => v.start < end && v.end > start;
  const limited = <T>(v: T[]) => ({ items: v.slice(0, 200), total: v.length, truncated: v.length > 200 });
  return { id: report.id, start, end, provenance: report.provenance,
    sections: limited(report.interpretation?.sections.filter(overlap) ?? []),
    moments: limited(report.interpretation?.moments.filter(overlap) ?? []),
    vocals: limited(report.interpretation?.vocals.filter(overlap) ?? []),
    chords: limited(report.interpretation?.chords.filter(overlap) ?? []),
    pitch: limited(report.signal?.pitch.filter(overlap) ?? []),
    beats: limited((report.detail?.rhythm.beats ?? report.signal?.tempo.beats ?? []).filter(v => v >= start && v < end)),
    tempoEvidence: report.detail?.rhythm.tempoEvidence ? { ...report.detail.rhythm.tempoEvidence, segments: limited(report.detail.rhythm.tempoEvidence.segments.filter(overlap)) } : null,
    acoustics: report.detail?.rhythm.acoustics ? { method: report.detail.rhythm.acoustics.method, windows: limited(report.detail.rhythm.acoustics.windows.filter(overlap)), warnings: report.detail.rhythm.acoustics.warnings } : null,
    harmony: report.detail?.harmony ? { ...report.detail.harmony, chords: limited(report.detail.harmony.chords.filter(overlap)) } : null,
    noteEvents: limited(report.detail?.notes.items.filter(overlap) ?? []),
    words: limited(report.detail?.lyrics.words.filter(overlap) ?? []),
    bandOnsets: limited(report.detail?.rhythm.onsets.filter(v => v.time >= start && v.time < end) ?? []),
    estimatedChords: limited(report.detail?.rhythm.chords.filter(overlap) ?? []),
    energy: limited(report.detail?.rhythm.envelope.filter(v => v.time >= start && v.time < end) ?? report.signal?.energy.filter(v => v.time >= start && v.time < end) ?? []),
    energySections: limited(report.detail?.rhythm.sections.filter(overlap) ?? []),
    wordContext: report.detail ? wordContexts(report.detail, start, end) : null,
    features: report.detail?.features ? { method: report.detail.features.method,
      strongBeatCandidates: limited(report.detail.features.strongBeats.filter(v => v.time >= start && v.time < end)),
      offbeatCandidates: limited(report.detail.features.offbeats.filter(v => v.time >= start && v.time < end)),
      percussionCandidates: limited(report.detail.features.percussion.filter(v => v.time >= start && v.time < end)),
      dynamics: limited(report.detail.features.dynamics.filter(overlap)),
      vocalDensity: limited(report.detail.features.vocalDensity.filter(overlap)),
      sourceTruncated: report.detail.features.truncated, warnings: report.detail.features.warnings } : null,
    detailStatus: report.detail ? { notes: report.detail.notes.status, noteMode: report.detail.notes.mode ?? "polyphonic", lyrics: report.detail.lyrics.status,
      models: { notes: report.detail.notes.model, lyrics: report.detail.lyrics.model },
      stageWarnings: { rhythm: report.detail.rhythm.warnings, notes: report.detail.notes.warnings, lyrics: report.detail.lyrics.warnings },
      truncated: { notes: report.detail.notes.truncated, lyrics: report.detail.lyrics.truncated, ...report.detail.rhythm.truncated }, warnings: report.detail.warnings } : null,
  };
}

export function asMarkdown(report: MusicReport): string {
  const a = report.interpretation;
  const lines = [`# ${report.source.title}`, ``, `Created: ${report.createdAt}`, `Schema: ${report.schema}`, `Source: ${report.source.url ?? report.source.sha256 ?? report.source.kind}`, `Evidence: ${report.provenance.mode}; accuracy unvalidated`, ``,
    `BPM (estimate): ${summaryOf(report).bpm ?? "unknown"}`, `Key (estimate): ${keyOf(report) ?? "unknown"}`, ``, a?.summary ?? "Local signal analysis only.", ``, `## Evidence-linked moments`];
  for (const m of a?.moments ?? []) lines.push(`- ${m.start.toFixed(2)}–${m.end.toFixed(2)}s | ${m.title}\n  Observation (model estimate): ${m.observation}\n  Interpretation: ${m.interpretation}`);
  if (report.detail) lines.push("", "## Browser specialist analysis", `Notes: ${report.detail.notes.status}; model ${report.detail.notes.model}; ${report.detail.notes.items.length} retained events; truncated ${report.detail.notes.truncated}.`, `Lyrics: ${report.detail.lyrics.status}; model ${report.detail.lyrics.model}; ${report.detail.lyrics.words.length} retained words; truncated ${report.detail.lyrics.truncated}.`, "Detailed estimated intervals in report JSON; neither transcription nor chord accuracy is independently validated.", ...report.detail.warnings.map(w => `- ${w}`));
  if (report.detail?.rhythm.tempoEvidence) lines.push(`Pulse support: ${report.detail.rhythm.tempoEvidence.status}; ${report.detail.rhythm.tempoEvidence.reason}. Supported means acoustic pulse fit, not verified musical meter.`);
  if (report.detail?.rhythm.acoustics) lines.push(`Acoustic texture: ${report.detail.rhythm.acoustics.windows.length} one-second windows in JSON (spectral centroid/flatness, power-band balance, RMS and onset density). Instrument/genre/quality unknown.`);
  if (report.detail?.harmony) {
    const h = report.detail.harmony;
    lines.push("", "## Chords and major/minor key candidates", `Source: ${h.source}; key ${h.key.status}. ${h.key.reason}`,
      `Alternatives (correlation, not probability): ${h.key.candidates.map(k => `${k.label} ${k.correlation.toFixed(3)}`).join("; ") || "none"}.`,
      `Chord intervals: first ${Math.min(24, h.chords.length)} of ${h.chords.length} retained; source truncated ${h.truncated}. Full data in JSON.`,
      ...h.chords.slice(0, 24).map(c => `- ${c.start.toFixed(2)}–${c.end.toFixed(2)}s | ${c.label} | ${c.source}; observed pitch classes: ${c.pitchClasses.join(", ") || "unavailable"}`),
      ...h.warnings.map(w => `- ${w}`));
  }
  lines.push(``, `## Limits`, ...(report.signal?.warnings ?? []).map(x => `- ${x}`), ...(a?.limitations ?? []).map(x => `- ${x}`), `- Musical accuracy has not been independently validated.`);
  return lines.join("\n");
}
