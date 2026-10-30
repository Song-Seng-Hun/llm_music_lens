import { z } from "zod";
import { MAX_DURATION, MAX_REPORT_BYTES, reportSchema, summaryOf, timelineOf, type MusicReport } from "./report.ts";
import { AppError } from "./validation.ts";
import { contourSummary } from "./pitch-contour.ts";
import { TIMING_TOOL, timingInputSchema } from "./music-timing.ts";
import { STRUCTURE_TOOL, analyzeMusicStructure } from "./music-structure.ts";
import { CONTINUITY_TOOL, composeMusicTimeline } from "./music-continuity.ts";

export const MCP_TOOLS = [
  TIMING_TOOL,
  STRUCTURE_TOOL,
  CONTINUITY_TOOL,
  { name: "import_music_report", description: "Save one <=1.9 MB Music Lens JSON report to the authenticated user's private library. Accepts local Whisper/emotion exports; no audio upload, URL fetch or hosted emotion inference. Imported estimates remain unvalidated. Returns only the saved summary; read bounded timelines separately.", inputSchema: { type: "object", properties: { report: { type: "object", description: "Complete music-lens/1 export, optionally with music-lens-local/2 localAnalysis." } }, required: ["report"], additionalProperties: false }, annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false } },
  { name: "list_music_reports", description: "List up to 30 music reports saved by the authenticated user. No audio, lyrics or credentials returned.", inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false } },
  { name: "get_music_report", description: "Read a bounded summary and evidence-linked musical interpretations. All estimates remain unvalidated; a report is not precise transcription proof.", inputSchema: { type: "object", properties: { id: { type: "string", format: "uuid" } }, required: ["id"], additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false } },
  { name: "get_music_timeline", description: "Read <=120 seconds: browser words, separate localWords/localEmotion native estimates, beat offsets, note/chord candidates, band attacks, dynamics, transcript density, energy, tempo support and 1 s acoustic texture/band balance. Each layer <=200; inspect total/truncated and narrow the range if needed. Local word probabilities are uncalibrated; arousal scales depend on voice/music mode. Missing is unknown, not neutral. Solo-vocal pitchContour uses nextStart continuation; null=model-unvoiced, not silence. No certified note identity or emotion accuracy.", inputSchema: { type: "object", properties: { id: { type: "string", format: "uuid" }, start: { type: "number", minimum: 0 }, end: { type: "number", maximum: 600 } }, required: ["id", "start", "end"], additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false } },
];

export type McpServices = { list: () => Promise<unknown>; get: (id: string) => Promise<MusicReport>; analyzeTiming?: (args: unknown) => Promise<unknown>; importReport?: (report: MusicReport) => Promise<MusicReport> };
export async function callMusicTool(name: string, args: unknown, services: McpServices) {
  if (name === "import_music_report") {
    const { report } = z.object({ report: z.unknown() }).strict().parse(args);
    if (new TextEncoder().encode(JSON.stringify(report)).byteLength > MAX_REPORT_BYTES) throw new AppError(413, "REPORT_TOO_LARGE", "분석 JSON은 1.9 MB 이하여야 합니다.");
    const parsed = reportSchema.parse(report);
    if (!services.importReport) throw new AppError(503, "IMPORT_UNAVAILABLE", "이 서버에서 결과 저장을 사용할 수 없습니다.");
    return summaryOf(await services.importReport(parsed));
  }
  if (name === "compose_music_timeline") return composeMusicTimeline(args);
  if (name === "analyze_music_structure") return analyzeMusicStructure(args);
  if (name === "analyze_music_timing") {
    const input = timingInputSchema.parse(args);
    if (!services.analyzeTiming) throw new AppError(503, "ANALYSIS_UNAVAILABLE", "이 서버에서 분석 실행 경로를 사용할 수 없습니다.");
    return services.analyzeTiming(input);
  }
  if (name === "list_music_reports") { z.object({}).strict().parse(args ?? {}); return services.list(); }
  if (name === "get_music_report") {
    const { id } = z.object({ id: z.string().uuid() }).strict().parse(args);
    const report = await services.get(id);
    const d = report.detail;
    return { ...summaryOf(report), key: d?.harmony?.key ?? d?.rhythm.key ?? report.interpretation?.key ?? null, moments: report.interpretation?.moments ?? [],
      detail: d ? {
        harmony: d.harmony ? { method: d.harmony.method, source: d.harmony.source, key: d.harmony.key, pitchClasses: d.harmony.pitchClasses, chordCount: d.harmony.chords.length, truncated: d.harmony.truncated, warnings: d.harmony.warnings } : null,
        tempoEvidence: d.rhythm.tempoEvidence ?? null, tempoCandidates: d.rhythm.tempoCandidates,
        acoustics: d.rhythm.acoustics ? { method: d.rhythm.acoustics.method, summary: d.rhythm.acoustics.summary, windows: d.rhythm.acoustics.windows.length, warnings: d.rhythm.acoustics.warnings } : null,
        notes: { status: d.notes.status, mode: d.notes.mode ?? "polyphonic", count: d.notes.items.length, model: d.notes.model, contour: contourSummary(d.notes.contour), truncated: d.notes.truncated, warnings: d.notes.warnings },
        lyrics: { status: d.lyrics.status, count: d.lyrics.words.length, model: d.lyrics.model, truncated: d.lyrics.truncated, warnings: d.lyrics.warnings },
        features: d.features ? { method: d.features.method, pitchSummary: d.features.pitchSummary,
          counts: { strongBeats: d.features.strongBeats.length, offbeats: d.features.offbeats.length, percussion: d.features.percussion.length, dynamics: d.features.dynamics.length, vocalDensity: d.features.vocalDensity.length },
          truncated: d.features.truncated, warnings: d.features.warnings } : null,
      } : null,
      limitations: [...(report.signal?.warnings ?? []), ...(report.interpretation?.limitations ?? []), ...(d?.warnings ?? [])] };
  }
  if (name === "get_music_timeline") {
    const value = z.object({ id: z.string().uuid(), start: z.number().min(0), end: z.number().max(MAX_DURATION) }).strict().refine(v => v.end > v.start && v.end - v.start <= 120).parse(args);
    const report = await services.get(value.id);
    return timelineOf(report, value.start, value.end);
  }
  throw new AppError(400, "UNKNOWN_TOOL", "지원하지 않는 도구입니다.");
}

