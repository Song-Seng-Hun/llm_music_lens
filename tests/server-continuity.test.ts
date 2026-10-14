import test from "node:test";
import assert from "node:assert/strict";
import { continuityInputSchema, composeMusicTimeline } from "../lib/music-continuity.ts";
import { callMusicTool, MCP_TOOLS } from "../lib/mcp.ts";
import { analyzeVocalPitch } from "../analysis/vocal-pitch.mjs";
import { vocalPitchContext } from "../lib/vocal-pitch-context.ts";

function segment(offsetSeconds = 0, duration = 2, sha256 = "a".repeat(64)) {
  return {
    source: { encoding: "pcm_s16le", sampleRate: 22050, channels: 1, sha256, offsetSeconds, duration },
    timing: { beats: [0.15, 0.65], downbeats: [0.15], provenance: {
      checkpointSha256: "b".repeat(64), onnxSha256: "c".repeat(64),
      frameRate: 50, contextFrames: 250, borderFrames: 6, overlap: "keep_first", decoder: "official-minimal",
    } },
    pitch: { status: "unavailable", reason: "Mixture; not separated" },
  };
}
const noIO = { list: async (): Promise<never> => { throw new Error("No report/model IO"); }, get: async (): Promise<never> => { throw new Error("No report/model IO"); } };

// Real producer projection catches drift in sample counts, public rounding and
// null alignment; a hand-crafted perfect contour would miss these failures.
test("MCP continuity accepts current native output without reanalysis or changing values", async () => {
  const samples = Float32Array.from({ length: 22051 }, (_, i) => 0.3 * Math.sin(2 * Math.PI * 233 * i / 22050));
  const pitch = vocalPitchContext(analyzeVocalPitch(samples));
  const input = { duration: 20, start: 10, end: 12, segments: [{ ...segment(10.003, samples.length / 22050), pitch }] };
  const before = structuredClone(input);
  const result = await callMusicTool("compose_music_timeline", input, noIO) as ReturnType<typeof composeMusicTimeline>;
  assert.deepEqual(input, before);
  assert.equal(pitch.status, "estimated");
  assert.deepEqual(result.pitchSlices[0].hz, pitch.contour!.hz);
  assert.deepEqual(result.pitchSlices[0].midi, pitch.contour!.midi);
  assert.deepEqual(result.pitchSlices[0].periodicity, pitch.contour!.periodicity);
  assert.equal(result.pitchSlices[0].firstFrameIndex, 0);
  assert.equal(result.beats[0].time, 10.003 + 0.15);
  assert.equal(result.sources[0].pitch.methodId, "monophonic-yin-style-central-check-v2");
  assert.ok(MCP_TOOLS.some(tool => tool.name === "compose_music_timeline"));
});

// Invalid clocks or unsupported recipes would fabricate song-level evidence.
test("continuity wire rejects invalid source clocks, event order, recipes and bounds", () => {
  const valid = { duration: 40, start: 0, end: 30, segments: [segment()] };
  const mutations: ((x: typeof valid) => void)[] = [
    x => { x.end = 31; }, x => { x.start = x.end; }, x => { x.duration = 601; },
    x => { x.segments[0].source.offsetSeconds = 39; },
    x => { x.segments[0].source.duration = 1.00001; },
    x => { x.segments[0].source.channels = 2; },
    x => { x.segments[0].source.sha256 = "claimed"; },
    x => { x.segments[0].timing.beats = [0.5, 0.4]; },
    x => { x.segments[0].timing.downbeats = [0.1, 0.1]; },
    x => { x.segments[0].timing.beats = [2]; },
    x => { x.segments[0].timing.provenance.decoder = "other"; },
    x => { x.segments[0].timing.provenance.frameRate = 100; },
    x => { x.segments = Array.from({ length: 61 }, () => segment()); },
  ];
  for (const mutate of mutations) { const value = structuredClone(valid); mutate(value); assert.equal(continuityInputSchema.safeParse(value).success, false); }
  assert.equal(continuityInputSchema.safeParse({ ...valid, save: true }).success, false);
  const collapsed = segment(500); collapsed.timing.beats = [0, 1e-16];
  assert.equal(continuityInputSchema.safeParse({ duration: 510, start: 500, end: 510, segments: [collapsed] }).success, false);
  const roundedEnd = segment(500, 1); roundedEnd.timing.beats = [0.9999999999999999];
  assert.equal(continuityInputSchema.safeParse({ duration: 501, start: 500, end: 501, segments: [roundedEnd] }).success, false);
  const supplied = { ...segment(), secretExtra: "do not echo", timing: { ...segment().timing, diagnostics: { extra: "do not echo" } } };
  const parsed = continuityInputSchema.parse({ ...valid, segments: [supplied] });
  assert.equal("secretExtra" in parsed.segments[0], false);
  assert.equal("diagnostics" in parsed.segments[0].timing, false);
});

test("continuity rejects partial contours and aggregate observation overflow before composition", () => {
  const pitch = vocalPitchContext(analyzeVocalPitch(new Float32Array(22050)));
  assert.equal(pitch.status, "estimated");
  const base = { duration: 30, start: 0, end: 30, segments: [{ ...segment(0, 1), pitch }] };
  const badLength = structuredClone(base); badLength.segments[0].pitch.contour!.hz.pop();
  const badNull = structuredClone(base); badNull.segments[0].pitch.contour!.midi[0] = 60;
  const badClock = structuredClone(base); badClock.segments[0].pitch.contour!.start = 0.02;
  for (const bad of [badLength, badNull, badClock]) assert.equal(continuityInputSchema.safeParse(bad).success, false);
  const dense = segment(0, 30); dense.timing.beats = Array.from({ length: 1500 }, (_, i) => i * 0.02); dense.timing.downbeats = [];
  assert.equal(continuityInputSchema.safeParse({ duration: 30, start: 0, end: 30, segments: Array(5).fill(dense) }).success, false);
  const longPitch = { ...pitch, contour: { ...pitch.contour!, duration: 30, total: 1500, hz: Array(1500).fill(null), midi: Array(1500).fill(null), periodicity: Array(1500).fill(null) } };
  assert.equal(continuityInputSchema.safeParse({ duration: 30, start: 0, end: 30, segments: Array(23).fill({ ...segment(0, 30), pitch: longPitch }) }).success, false);
});

test("duplicate conflicts surface as actionable tool errors, not silently selected evidence", async () => {
  const first = segment(), second = segment(); second.timing.beats = [0.16, 0.65];
  await assert.rejects(() => callMusicTool("compose_music_timeline", { duration: 4, start: 0, end: 4, segments: [first, second] }, noIO), { code: "CONTINUITY_CONFLICT", status: 400 });
});
