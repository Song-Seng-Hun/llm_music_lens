import { z } from "zod";
import { MAX_DURATION, summaryOf, timelineOf, type MusicReport } from "./report.ts";
import { AppError } from "./validation.ts";
import { contourSummary } from "./pitch-contour.ts";
import { TIMING_TOOL, timingInputSchema } from "./music-timing.ts";
import { STRUCTURE_TOOL, analyzeMusicStructure } from "./music-structure.ts";

export const MCP_TOOLS = [
  TIMING_TOOL,
  STRUCTURE_TOOL,
  { name: "list_music_reports", description: "List up to 30 music reports saved by the authenticated user. No audio, lyrics or credentials returned.", inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false } },
  { name: "get_music_report", description: "Read a bounded summary and evidence-linked musical interpretations. All estimates remain unvalidated; a report is not precise transcription proof.", inputSchema: { type: "object", properties: { id: { type: "string", format: "uuid" } }, required: ["id"], additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false } },
  { name: "get_music_timeline", description: "Read <=120 seconds: words, beat offsets, note/chord candidates, band attacks, dynamics, transcript density, energy, tempo support and 1 s acoustic texture/band balance. Each layer <=200; inspect total/truncated. Solo-vocal native pitchContour frames use nextStart continuation with the same end; null=model-unvoiced, not silence. Pulse support is not meter; overlap is not vocal note identity; missing is unknown. Smaller intervals recover truncated layers.", inputSchema: { type: "object", properties: { id: { type: "string", format: "uuid" }, start: { type: "number", minimum: 0 }, end: { type: "number", maximum: 600 } }, required: ["id", "start", "end"], additionalProperties: false }, annotations: { readOnlyHint: true, openWorldHint: false } },
];

export type McpServices = { list: () => Promise<unknown>; get: (id: string) => Promise<MusicReport>; analyzeTiming?: (args: unknown) => Promise<unknown> };
export async function callMusicTool(name: string, args: unknown, services: McpServices) {
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
