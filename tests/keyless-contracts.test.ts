import test from "node:test";
import assert from "node:assert/strict";
import { parseYouTube, sameOrigin, boundedJson, AppError } from "../lib/validation.ts";
import { callMusicTool, MCP_TOOLS } from "../lib/mcp.ts";
import { reportSchema, timelineOf, type MusicReport } from "../lib/report.ts";
import { asTeamHandoff } from "../lib/handoff.ts";

const id = "af32ed31-a267-4d03-a12d-cad4353f5ea3";
function fixture(): MusicReport {
  return { schema: "music-lens/1", id, createdAt: "2026-10-13T00:00:00Z",
    source: { kind: "synthetic", title: "Fixture", sha256: "a".repeat(64), duration: 12 },
    signal: { duration: 12, waveform: [], energy: [],
      tempo: { bpm: null, candidates: [], beats: [], segments: [], status: "unknown" },
      pitch: [], key: { label: null, status: "unknown" }, chords: [], warnings: ["fixture only"], method: "fixture" },
    interpretation: null, provenance: { mode: "local-signal", inputStatus: "synthetic fixture", accuracy: "unvalidated" } };
}

test("URL reference stays on a single canonical public YouTube video", () => {
  assert.equal(parseYouTube("https://youtu.be/ALxVpirKqRQ?si=abc").url, "https://www.youtube.com/watch?v=ALxVpirKqRQ");
  for (const url of ["http://youtu.be/ALxVpirKqRQ", "https://youtube.com.example.org/watch?v=ALxVpirKqRQ", "https://youtube.com@127.0.0.1/watch?v=ALxVpirKqRQ", "https://youtube.com/playlist?list=test", "https://youtu.be/a/b"]) assert.throws(() => parseYouTube(url), AppError);
});
test("JSON bytes and cross-site submissions are bounded", async () => {
  await assert.rejects(boundedJson(new Request("https://site.example/api", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: "가".repeat(20) }) }), 30), (e: AppError) => e.status === 413);
  assert.throws(() => sameOrigin(new Request("https://site.example/api", { headers: { origin: "https://different.example" } })), (e: AppError) => e.status === 403);
  sameOrigin(new Request("https://site.example/mcp"));
});
test("MCP retains bounded half-open timelines beside explicit server timing analysis", async () => {
  const report = fixture();
  report.signal!.tempo.beats = Array.from({ length: 250 }, (_, i) => i / 25);
  const window = timelineOf(report, 0, 8);
  assert.equal(window.beats.items.length, 200); assert.equal(window.beats.truncated, false);
  assert.equal(timelineOf(report, 0, 10).beats.truncated, true);
  assert.deepEqual(timelineOf(report, 8, 8.1).beats.items, [8, 8.04, 8.08]);
  const services = { list: async () => [], get: async () => report };
  await callMusicTool("get_music_report", { id }, services);
  await callMusicTool("get_music_timeline", { id, start: 0, end: 120 }, services);
  await assert.rejects(callMusicTool("get_music_timeline", { id, start: 0, end: 121 }, services));
  await assert.rejects(callMusicTool("analyze_youtube", {}, services), (e: AppError) => e.code === "UNKNOWN_TOOL");
  assert.equal(MCP_TOOLS.length, 6); assert.ok(MCP_TOOLS.every(t => t.annotations.readOnlyHint));
  assert.ok(MCP_TOOLS.some(t => t.name === "compose_music_timeline"));
  assert.ok(MCP_TOOLS.some(t => t.name === "analyze_music_structure"));
  assert.ok(MCP_TOOLS.some(t => t.name === "analyze_music_timing"));
});
test("handoff preserves source, uncertainty and sampled timing without inventing analysis", () => {
  const report = fixture();
  report.signal!.pitch = Array.from({ length: 120 }, (_, i) => ({ start: i / 10, end: i / 10 + 0.05, hz: 440, midi: 69, note: "A4", confidence: 0.8 }));
  const text = asTeamHandoff(report);
  assert.match(text, /cannot access and listen to the original audio/);
  assert.match(text, /not note onsets\/durations/);
  const data = JSON.parse(text.split("\n").filter(line => line.startsWith("    ")).map(line => line.slice(4)).join("\n"));
  assert.deepEqual(data.source, report.source); assert.equal(data.provenance.accuracy, "unvalidated");
  assert.equal(data.signal.bpmEstimate, null); assert.equal(data.importedInterpretation, null);
  assert.equal(data.signal.sparsePitchCandidates.total, 120); assert.equal(data.signal.sparsePitchCandidates.sampled, true);
  assert.equal(data.signal.sparsePitchCandidates.items.length, 24);
  assert.deepEqual(data.signal.sparsePitchCandidates.items[0], report.signal!.pitch[0]);
  assert.deepEqual(data.signal.sparsePitchCandidates.items.at(-1), report.signal!.pitch.at(-1));
  assert.ok(data.signal.energyBins.every((v: { meanRms: number | null }) => v.meanRms === null));
});
test("existing model reports remain readable as unvalidated supplied data", () => {
  const report = fixture(); report.signal = null;
  report.provenance = { mode: "model-estimate", model: "historical-model", inputStatus: "legacy fixture", accuracy: "unvalidated", audioTokens: 25 };
  report.interpretation = { summary: "Supplied interpretation", styleTags: [], instruments: [], bpm: { value: null, alternatives: [], reason: "unknown" }, key: { value: null, reason: "unknown" }, sections: [], moments: [], vocals: [], chords: [], limitations: ["not independently checked"] };
  const parsed = reportSchema.parse(report);
  const handoff = asTeamHandoff(parsed);
  assert.match(handoff, /Supplied interpretation/); assert.match(handoff, /not independently checked/);
  assert.equal(parsed.provenance.model, "historical-model");
});
