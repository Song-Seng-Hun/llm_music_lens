import test from "node:test";
import assert from "node:assert/strict";
import { structureInputSchema } from "../lib/music-structure.ts";
import { callMusicTool, MCP_TOOLS } from "../lib/mcp.ts";

function packet(offset = 0, duration = 4) {
  return {
    schema: "music-structure-packet/1", recipe: "logmel16-1s-sequence-v1",
    source: { sha256: "a".repeat(64), offsetSeconds: offset, duration },
    frames: Array.from({ length: Math.ceil(duration) }, (_, i) => ({
      start: i, end: Math.min(i + 1, duration), activeFraction: 1, rmsDbfs: -12,
      lowShare: 0.2, mel: Array.from({ length: 16 }, (_, j): number => j === i % 16 ? 1 : 0),
    })),
  };
}

// Packet claims are untrusted. Wrong clocks/recipes/vectors could otherwise
// fabricate coverage or make the whole-song quadratic work unbounded.
test("structure wire rejects incompatible, malformed, overlapping and oversized packets", () => {
  const good = { duration: 8, packets: [packet(), packet(4)] };
  structureInputSchema.parse(good);
  const corruptions = [
    (x: typeof good) => { x.packets[0].recipe = "different"; },
    (x: typeof good) => { x.packets[0].frames.pop(); },
    (x: typeof good) => { x.packets[0].frames[1].start = 0.9; },
    (x: typeof good) => { x.packets[0].frames[3].end = 5; },
    (x: typeof good) => { x.packets[0].frames[0].mel[0] = 0.5; },
    (x: typeof good) => { x.packets[0].frames[0].mel.push(0); },
    (x: typeof good) => { x.packets[0].frames[0].rmsDbfs = Infinity; },
    (x: typeof good) => { x.packets[0].frames[0].activeFraction = 0; },
    (x: typeof good) => { x.packets[1].source.offsetSeconds = 3; },
    (x: typeof good) => { x.duration = 7; },
    (x: typeof good) => { x.duration = 601; },
    (x: typeof good) => { x.packets = Array.from({ length: 61 }, (_, i) => packet(i * 4)); x.duration = 244; },
  ];
  for (const corrupt of corruptions) { const value = structuredClone(good); corrupt(value); assert.equal(structureInputSchema.safeParse(value).success, false); }
  const missingRms = packet();
  const claimed = { ...missingRms, frames: missingRms.frames.map(row => ({ ...row, rmsDbfs: null, mel: null, lowShare: null })) };
  assert.equal(structureInputSchema.safeParse({ duration: 4, packets: [claimed] }).success, false);
  structureInputSchema.parse({ duration: 4, packets: [{ ...claimed, frames: claimed.frames.map(row => ({ ...row, activeFraction: 0 })) }] });
  assert.equal(structureInputSchema.safeParse({ ...good, storage: true }).success, false);
});

// Actual dispatcher must execute the new tool without a browser, model or
// report-store dependency; gaps and partial tails must remain visible.
test("MCP structure dispatch preserves fractional source offsets, gaps and partial tails", async () => {
  const args = { duration: 12, packets: [packet(7, 4.25), packet(0.25, 4)], includeExperimentalRepetitions: true };
  const before = JSON.stringify(args);
  const services = { list: async () => { throw new Error("No report IO"); }, get: async (): Promise<never> => { throw new Error("No report IO"); } };
  const result = await callMusicTool("analyze_music_structure", args, services) as ReturnType<typeof import("../lib/music-structure.ts").analyzeMusicStructure>;
  assert.equal(JSON.stringify(args), before);
  assert.equal(result.coverage.coveredSeconds, 8.25);
  assert.equal(result.coverage.complete, false);
  assert.deepEqual(result.coverage.gaps, [{ start: 0, end: 0.25 }, { start: 4.25, end: 7 }, { start: 11.25, end: 12 }]);
  assert.equal(result.repetitionEvidence.status, "experimental");
  assert.ok(result.diagnostics.comparisonCells > 0);
  assert.ok(result.repetitions!.every(pair => pair.first.end <= 4.25 && pair.second.start >= 7));
  assert.ok(result.transitions.every(transition => transition.time < 4.25 || transition.time >= 7));
  assert.deepEqual(result, await callMusicTool("analyze_music_structure", args, services));
  const normal = await callMusicTool("analyze_music_structure", { ...args, includeExperimentalRepetitions: false }, services) as typeof result;
  assert.equal(normal.repetitionEvidence.status, "withheld");
  assert.equal("repetitions" in normal, false);
  assert.equal("repetitionsTotal" in normal, false);
  assert.equal("repetitionsTruncated" in normal, false);
  // Withheld recurrence must skip its work, not compute then discard results.
  assert.equal(normal.diagnostics.comparisonCells, 0);
  assert.equal(normal.method, "16 grouped log-mel bands; recurrence withheld (not computed); 3s before/after novelty; 1s resolution");
  assert.deepEqual(normal.transitions, result.transitions);
  assert.ok(MCP_TOOLS.some(tool => tool.name === "analyze_music_structure"));
});
