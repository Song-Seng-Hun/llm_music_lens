import test from "node:test";
import assert from "node:assert/strict";
import { decodeTimingAudio, TIMING_MAX_BASE64, TIMING_SAMPLE_RATE } from "../lib/music-timing.ts";
import { callMusicTool } from "../lib/mcp.ts";
import { AppError } from "../lib/validation.ts";
import { linkVocalTiming } from "../lib/vocal-timing.ts";
import { beatContext } from "../lib/beat-context.ts";
import { waveformContext } from "../lib/waveform-context.ts";
import { attackContext } from "../lib/attack-context.ts";

function input(seconds = 1) {
  const bytes = Buffer.alloc(TIMING_SAMPLE_RATE * seconds * 2);
  [-32768, -1, 0, 32767].forEach((n, i) => bytes.writeInt16LE(n, i * 2));
  return { audio: { encoding: "pcm_s16le", sampleRate: 22050, channels: 1, data: bytes.toString("base64") }, offsetSeconds: 0 };
}

// Wrong signedness/endian would change actual inference; existing UI tests use
// decoded floats and cannot catch the new MCP wire-format failure.
test("server PCM decoding preserves signed samples, bytes and excerpt clock", () => {
  const value = input(); value.offsetSeconds = 40;
  const decoded = decodeTimingAudio(value);
  assert.deepEqual(Array.from(decoded.samples.subarray(0, 4)), [-1, -1 / 32768, 0, 32767 / 32768]);
  assert.equal(decoded.duration, 1); assert.equal(decoded.input.offsetSeconds, 40);
  assert.equal(Buffer.from(decoded.bytes).toString("base64"), value.audio.data);
  assert.equal(decodeTimingAudio(input(30)).samples.length, 661500);
});

// Reject malformed/oversized/unsupported audio before any heavy model call.
test("server audio rejects unsupported formats and invalid bounds", () => {
  const valid = input();
  for (const value of [
    { ...valid, audio: { ...valid.audio, channels: 2 } },
    { ...valid, audio: { ...valid.audio, sampleRate: 48000 } },
    { ...valid, audio: { ...valid.audio, data: "?".repeat(4) } },
    { ...valid, audio: { ...valid.audio, data: "AAAA" } },
    { ...valid, audio: { ...valid.audio, data: "A".repeat(TIMING_MAX_BASE64 + 4) } },
    { ...valid, audio: { ...valid.audio, url: "https://example.test/audio" } },
    { ...valid, offsetSeconds: 600 },
    { ...valid, sourceKind: "automatically-verified-voice" },
    { ...valid, waveformDetail: "unbounded" },
    { ...valid, timedVocal: { unit: "phone", source: "fixture", items: [{ start: 0.5, end: 2, text: "a" }] } },
    { ...valid, noteEvents: { source: "fixture", mode: "solo-vocal", items: [{ start: 0.8, end: 0.5, midi: 60 }] } },
  ]) assert.throws(() => decodeTimingAudio(value));
});

// A cropped excerpt must not fabricate a vocal entrance; supplied timings and
// note identity remain independent evidence even when their intervals overlap.
test("waveform context distinguishes cropped edges and preserves bounded source references", () => {
  const decoded = decodeTimingAudio({ ...input(2), timedVocal: { unit: "word", source: "caller", items: Array.from({ length: 12 }, (_, i) => ({ start: i / 10, end: i / 10 + 0.08, text: String(i) })) } });
  assert.equal(decoded.input.sourceKind, "mixture");
  assert.equal(decoded.input.waveformDetail, "summary");
  const before = JSON.stringify(decoded.input.timedVocal);
  const result = waveformContext([
    { start: 0, end: 2, peakTime: 0.52, startClipped: true, endClipped: true },
    { start: 0.31, end: 1.3, peakTime: 1, startClipped: false, endClipped: false },
  ], decoded.input, [0, 0.5, 1, 1.5]).items;
  assert.equal(result[0].startBeat, null); assert.equal(result[0].endBeat, null);
  assert.equal(result[0].peakBeat?.offsetSeconds, 0.02);
  assert.deepEqual(result[0].suppliedVocalUnits, { indices: [0, 1, 2, 3, 4, 5, 6, 7], total: 12, truncated: true });
  assert.equal(result[1].startBeat?.offsetSeconds, -0.19);
  assert.equal(JSON.stringify(decoded.input.timedVocal), before);
  const dense = waveformContext(Array.from({ length: 64 }, () => ({ start: 0, end: 2, peakTime: 0.52, startClipped: true, endClipped: true })), decoded.input, [0, 0.5, 1, 1.5]);
  assert.equal(dense.total, 64); assert.equal(dense.items.length, 16); assert.equal(dense.truncated, true);
});

// Real unit boundaries must survive linkage; even word subdivision would hide
// missing alignment and change the meaning of melisma/rap timing.
test("vocal timing links supplied intervals without manufacturing syllables or meter", () => {
  const parsed = decodeTimingAudio({ ...input(2), timedVocal: { unit: "syllable", source: "fixture-only", items: [
    { start: 0.3, end: 1, text: "가" }, { start: 1, end: 1.8, text: "나" },
  ] }, noteEvents: { source: "fixture-only", mode: "solo-vocal", items: [
    { start: 0.3, end: 0.6, midi: 60 }, { start: 0.6, end: 1, midi: 62 }, { start: 1, end: 1.8, midi: 64 },
  ] } });
  const linked = linkVocalTiming(parsed.input, [0, 0.5, 1, 1.5], [0], 2);
  assert.equal(linked.status, "linked-supplied-timing");
  assert.equal(linked.items?.length, 2); assert.equal(linked.items?.[0].start, 0.3);
  assert.equal(linked.items?.[0].beatPhase?.fraction, 0.6);
  assert.equal(linked.items?.[0].nearestBeat?.offsetSeconds, -0.2);
  assert.deepEqual(linked.items?.[0].simultaneousNotes.items.map(x => x.midi), [60, 62]);
  assert.equal(linked.items?.[1].nearestBeat?.offsetSeconds, 0);
  assert.equal(linkVocalTiming(decodeTimingAudio(input()).input, [0], [], 1).status, "unavailable");
  const context = beatContext([0, 0.5, 1, 1.5, 2], [0, 2]);
  assert.equal(context.tempo.bpm, 120); assert.equal(context.meter, null);
  assert.equal(context.downbeatSpans[0].detectedBeatCount, 4);
});

// Candidate-to-beat signs, midpoint ties and excerpt-edge extrapolation affect
// groove interpretation; onset arithmetic alone cannot check these relations.
test("attack context uses actual beat intervals, signed offsets and no extrapolation", () => {
  const times = [0.1, 0.5, 0.92, 1.08, 1.3, 1.6, 1.8];
  const candidates = times.map(time => ({ time, strength: 0.7, band: 'mid', fluxLow: 1, fluxMid: 3, fluxHigh: 0 }));
  const before = JSON.stringify(candidates);
  const { events } = attackContext(candidates, [0.5, 1, 1.6], 2);
  assert.equal(events[0].nearestBeat, null); assert.equal(events[6].beatPhase, null);
  assert.equal(events[1].beatPhase?.fraction, 0);
  assert.equal(events[2].nearestBeat?.offsetSeconds, -0.08);
  assert.equal(events[3].nearestBeat?.offsetSeconds, 0.08);
  assert.equal(events[4].nearestBeat?.index, 1);
  assert.equal(events[4].beatPhase?.fraction, 0.5);
  assert.equal(events[5].nearestBeat?.index, 2); assert.equal(events[5].beatPhase?.fraction, 1);
  assert.equal(JSON.stringify(candidates), before);
  assert.ok(attackContext(candidates, [1], 2).events.every(event => event.nearestBeat === null));
  const midpoint = attackContext([{ ...candidates[0], time: 0.2 }], [0.1, 0.3], 1).events[0];
  assert.equal(midpoint.nearestBeat?.index, 0, 'decimal midpoint ties choose earlier beat');
  assert.equal(midpoint.nearestBeat?.offsetSeconds, 0.1);
});

// The cap must not silently lower density, overrun fractional tails, or make
// the text+structured MCP duplication exceed the frozen response budget.
test("attack context bounds event details while retaining all density counts", () => {
  const duration = 29 + 1 / 22050;
  const candidates = Array.from({ length: 290 }, (_, i) => ({ time: i / 10, strength: 0.7654321, band: ['low', 'mid', 'high'][i % 3], fluxLow: 1.111, fluxMid: 2.222, fluxHigh: 3.334 }));
  const result = attackContext(candidates, Array.from({ length: 60 }, (_, i) => i / 2), duration);
  assert.equal(result.events.length, 64); assert.equal(result.total, 290); assert.equal(result.truncated, true);
  assert.equal(result.density.reduce((sum, window) => sum + window.count, 0), 290);
  assert.equal(result.density.at(-1)?.end, duration);
  const wire = JSON.stringify({ content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: { result } });
  assert.ok(Buffer.byteLength(wire) < 48000, 'allow at least 2KB for method/warning fields within 50KB guard');
});

// Dispatch can silently remain on saved-report paths even with working DSP.
test("MCP selects the analysis service and never substitutes a stored report", async () => {
  let calls = 0;
  const services = { list: async () => [], get: async () => { throw new Error("not a report read"); },
    analyzeTiming: async (args: unknown) => { calls++; return { duration: decodeTimingAudio(args).duration }; } };
  assert.deepEqual(await callMusicTool("analyze_music_timing", input(), services), { duration: 1 });
  assert.equal(calls, 1);
  await assert.rejects(callMusicTool("analyze_music_timing", { audio: {} }, services));
  assert.equal(calls, 1);
  await assert.rejects(callMusicTool("analyze_music_timing", input(), { list: services.list, get: services.get }), (e: AppError) => e.code === "ANALYSIS_UNAVAILABLE");
});
